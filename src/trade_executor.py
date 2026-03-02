import MetaTrader5 as mt5
import logging
from datetime import datetime
from typing import Optional, Dict


class TradeExecutor:
    """
    Execute and manage MT5 orders.
    ALL risk/sizing parameters come from the loaded strategy JSON via StrategyManager.
    trading_config is only used for execution mechanics (slippage, magic number, daily loss).

    v12 Update:
    - Passes tick_data to strategy.analyze() for Cross-Asset Risk Sentiment
    - Calls strategy.update_pair_result() after close untuk AI Capital Allocator
    - Uses portfolio_heat_cap from strategy (weekday vs weekend)
    - Compounding support (risk on equity instead of balance)
    """

    def __init__(self, broker_config: dict, trading_config: dict, strategy_manager=None):
        self.broker_config    = broker_config
        self.trading_config   = trading_config
        self.strategy_manager = strategy_manager

        self.slippage       = trading_config.get('slippage',       10)
        self.magic_number   = trading_config.get('magic_number', 234000)
        self.max_daily_loss = trading_config.get('max_daily_loss', 0)

        self._refresh_risk_params()

        self.daily_pnl          = 0.0
        self.consecutive_wins   = 0
        self.consecutive_losses = 0

        # Tick data cache untuk CARS sentiment (di-update dari main.py)
        self._tick_data_cache: Dict = {}

    # ------------------------------------------------------------------

    def refresh_from_strategy(self):
        self._refresh_risk_params()
        logging.info(
            f"[TradeExecutor] Risk params refreshed — "
            f"risk_min={self.risk_per_trade_min:.3f}, "
            f"risk_max={self.risk_per_trade_max:.3f}, "
            f"max_pos={self.max_positions}, "
            f"compounding={self.compounding}"
        )

    def update_tick_cache(self, tick_data: Dict):
        """Dipanggil dari main.py setiap cycle untuk update tick data semua pair."""
        self._tick_data_cache = tick_data

    def _refresh_risk_params(self):
        if self.strategy_manager:
            rp = self.strategy_manager.get_risk_parameters()
            self.risk_per_trade_min = rp['risk_per_trade_min']
            self.risk_per_trade_max = rp['risk_per_trade_max']
            self.max_leverage       = rp['max_leverage']
            self.max_drawdown_limit = rp['max_drawdown_limit']
            self.max_positions      = rp.get('max_positions', 6)
            self.compounding        = rp.get('compounding', False)
        else:
            self.risk_per_trade_min = 0.003
            self.risk_per_trade_max = 0.010
            self.max_leverage       = 100
            self.max_drawdown_limit = 0.04
            self.max_positions      = 6
            self.compounding        = False

    # ------------------------------------------------------------------
    # EXECUTE SIGNAL
    # ------------------------------------------------------------------

    def execute_signal(self, symbol: str, signal: dict) -> bool:
        try:
            # Gate 1: max open positions
            total_positions = mt5.positions_get()
            total_count     = len(total_positions) if total_positions else 0
            if total_count >= self.max_positions:
                logging.warning(f"Max positions reached ({total_count}/{self.max_positions}), skipping {symbol}")
                return False

            # Gate 2: daily loss limit
            if self.max_daily_loss > 0 and self.daily_pnl <= -self.max_daily_loss:
                logging.warning(f"Daily loss limit hit: ${self.daily_pnl:.2f}, skipping")
                return False

            # Gate 3: drawdown
            account_info = mt5.account_info()
            if account_info:
                equity  = account_info.equity
                balance = account_info.balance
                if balance > 0:
                    dd = (balance - equity) / balance
                    if dd >= self.max_drawdown_limit:
                        logging.warning(f"Drawdown limit hit: {dd:.2%}, skipping")
                        return False

            # Gate 4: Portfolio heat cap
            if not self._check_portfolio_heat(account_info):
                return False

            # Symbol availability
            sym_info = mt5.symbol_info(symbol)
            if sym_info is None:
                logging.error(f"Symbol {symbol} not found in MT5")
                return False
            if not sym_info.visible:
                if not mt5.symbol_select(symbol, True):
                    logging.error(f"Cannot select symbol {symbol}")
                    return False

            action = signal['action']
            if action == 'BUY':
                order_type = mt5.ORDER_TYPE_BUY
                price      = mt5.symbol_info_tick(symbol).ask
            elif action == 'SELL':
                order_type = mt5.ORDER_TYPE_SELL
                price      = mt5.symbol_info_tick(symbol).bid
            else:
                return False

            sl = signal.get('stop_loss')
            tp = signal.get('take_profit')
            if sl is None or tp is None:
                logging.error(f"Signal has no SL/TP for {symbol}. Order skipped.")
                return False

            digits = sym_info.digits
            sl = round(sl, digits)
            tp = round(tp, digits)

            lot = self.calculate_position_size(symbol, price, sl, signal)

            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       lot,
                "type":         order_type,
                "price":        price,
                "sl":           sl,
                "tp":           tp,
                "deviation":    self.slippage,
                "magic":        self.magic_number,
                "comment":      "Bot v12",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result is None:
                logging.error(f"Order send failed: {mt5.last_error()}")
                return False
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logging.error(f"Order rejected: {result.retcode} — {result.comment}")
                return False

            rr = self._rr(price, sl, tp)
            logging.info("=" * 60)
            logging.info("[OK] ORDER EXECUTED")
            logging.info(f"Symbol     : {symbol}")
            logging.info(f"Strategy   : {signal.get('strategy','?')}")
            logging.info(f"Action     : {action}")
            logging.info(f"Volume     : {lot}")
            logging.info(f"Price      : {price:.{digits}f}")
            logging.info(f"SL         : {sl:.{digits}f}")
            logging.info(f"TP         : {tp:.{digits}f}")
            logging.info(f"Confidence : {signal.get('confidence',0):.1f}%")
            logging.info(f"R:R        : 1:{rr:.2f}")
            logging.info(f"Risk mult  : {signal.get('risk_multiplier',1):.3f}x")
            logging.info(f"  DD mult  : {signal.get('dd_multiplier',1):.3f}")
            logging.info(f"  Vol mult : {signal.get('vol_multiplier',1):.3f}")
            logging.info(f"  Sentiment: {signal.get('sentiment_score',0):.3f} → x{signal.get('sentiment_mult',1):.3f}")
            logging.info(f"  ACA mult : {signal.get('aca_mult',1):.3f}")
            logging.info(f"  Perf mult: {signal.get('perf_mult',1):.3f}")
            logging.info(f"Order ID   : {result.order}")
            logging.info("=" * 60)
            return True

        except Exception as e:
            logging.error(f"Error executing signal for {symbol}: {e}")
            import traceback; logging.error(traceback.format_exc())
            return False

    def _check_portfolio_heat(self, account_info) -> bool:
        """Cek total risk semua posisi open tidak melebihi portfolio heat cap."""
        if not account_info:
            return True

        if not self.strategy_manager:
            return True

        heat_cap = self.strategy_manager.get_portfolio_heat_cap()
        if heat_cap <= 0:
            return True

        positions = mt5.positions_get()
        if not positions:
            return True

        balance = account_info.balance
        if balance <= 0:
            return True

        total_risk = 0.0
        for pos in positions:
            if pos.sl > 0:
                sl_dist    = abs(pos.price_open - pos.sl)
                sym_info   = mt5.symbol_info(pos.symbol)
                if sym_info:
                    tick_val   = sym_info.trade_tick_value
                    tick_size  = sym_info.trade_tick_size
                    if tick_size > 0 and tick_val > 0:
                        sl_ticks   = sl_dist / tick_size
                        risk_amount = sl_ticks * tick_val * pos.volume
                        total_risk += risk_amount / balance

        if total_risk >= heat_cap:
            logging.warning(f"[PortfolioHeat] Cap reached: {total_risk:.3%} >= {heat_cap:.3%}")
            return False
        return True

    # ------------------------------------------------------------------
    # POSITION SIZING
    # ------------------------------------------------------------------

    def calculate_position_size(self, symbol: str, entry_price: float,
                                 stop_loss: float, signal: dict) -> float:
        try:
            account = mt5.account_info()
            if account is None:
                return self._min_lot(symbol)

            # Compounding: gunakan equity, bukan balance
            if self.compounding:
                base_capital = account.equity
            else:
                base_capital = account.balance

            confidence = signal.get('confidence', 0)
            if confidence >= 80:
                risk_pct = self.risk_per_trade_max
            elif confidence >= 65:
                risk_pct = (self.risk_per_trade_min + self.risk_per_trade_max) / 2
            else:
                risk_pct = self.risk_per_trade_min

            # Anti-martingale (from strategy config)
            if self.strategy_manager:
                uf = self.strategy_manager.strategy_config.get('unique_features', {})
                am = uf.get('anti_martingale_progression', {})
                if am:
                    trigger  = am.get('consecutive_wins_trigger', 3)
                    increase = am.get('position_size_increase',   0.5)
                    max_mult = am.get('max_position_size_multiplier', 4.0)
                    recovery = am.get('recovery_extra_risk_after_loss', 0.0)
                    if self.consecutive_wins >= trigger:
                        mult     = 1 + increase * (self.consecutive_wins - trigger + 1)
                        mult     = min(mult, max_mult)
                        risk_pct *= mult
                        logging.info(f"Anti-martingale x{mult:.2f}")
                    if self.consecutive_losses > 0 and recovery > 0:
                        risk_pct += recovery
                        logging.info(f"Recovery mode +{recovery*100:.2f}%")

            # ACA pair-specific risk allocation
            if self.strategy_manager and self.strategy_manager._norm.get('aca_enabled', False):
                risk_pct = self.strategy_manager.get_pair_risk_allocation(symbol, risk_pct)

            # Apply composite risk multiplier from signal
            risk_multiplier = signal.get('risk_multiplier', 1.0)
            risk_pct = risk_pct * risk_multiplier

            # Hard cap
            risk_pct    = min(risk_pct, self.risk_per_trade_max)
            risk_amount = base_capital * risk_pct

            sym_info   = mt5.symbol_info(symbol)
            tick_value = sym_info.trade_tick_value
            tick_size  = sym_info.trade_tick_size

            sl_distance = abs(entry_price - stop_loss)
            if sl_distance <= 0 or tick_size <= 0 or tick_value <= 0:
                return self._min_lot(symbol)

            sl_ticks = sl_distance / tick_size
            lot      = risk_amount / (sl_ticks * tick_value)

            lot = max(sym_info.volume_min, min(lot, sym_info.volume_max))
            lot = round(lot / sym_info.volume_step) * sym_info.volume_step

            logging.info(
                f"Position size: risk={risk_pct*100:.3f}% "
                f"(${risk_amount:.2f}), SL_dist={sl_distance:.5f}, lot={lot:.2f}"
                f" [compounding={self.compounding}]"
            )
            return lot

        except Exception as e:
            logging.error(f"Error calculating position size: {e}")
            return self._min_lot(symbol)

    def _min_lot(self, symbol: str) -> float:
        try:
            return mt5.symbol_info(symbol).volume_min
        except:
            return 0.01

    # ------------------------------------------------------------------
    # POSITION MANAGEMENT
    # ------------------------------------------------------------------

    def manage_positions(self):
        try:
            positions = mt5.positions_get()
            if not positions:
                return

            total_profit = 0.0
            for pos in positions:
                profit = pos.profit
                total_profit += profit
                if 'JPY' in pos.symbol:
                    pips = (pos.price_current - pos.price_open) * 100 if pos.type == 0 \
                           else (pos.price_open - pos.price_current) * 100
                else:
                    pips = (pos.price_current - pos.price_open) * 10000 if pos.type == 0 \
                           else (pos.price_open - pos.price_current) * 10000
                status = "[+]" if profit >= 0 else "[-]"
                logging.info(
                    f"{status} {pos.ticket}: {pos.symbol} "
                    f"{'BUY' if pos.type == 0 else 'SELL'} "
                    f"vol={pos.volume} pips={pips:+.1f} profit=${profit:.2f}"
                )
                self.update_trailing_stop(pos)

            self.daily_pnl = total_profit
            logging.info(f"Open: {len(positions)} | Total P&L: ${total_profit:.2f}")

        except Exception as e:
            logging.error(f"Error managing positions: {e}")

    def update_trailing_stop(self, position):
        try:
            if not self.trading_config.get('trailing_stop_enabled', False):
                return
            tsl_pips = self.trading_config.get('trailing_stop_pips', 15)
            sym_info = mt5.symbol_info(position.symbol)
            point    = sym_info.point
            if position.type == mt5.ORDER_TYPE_BUY:
                new_sl = position.price_current - tsl_pips * point * 10
                if new_sl > position.sl:
                    self.modify_position(position, new_sl, position.tp)
            elif position.type == mt5.ORDER_TYPE_SELL:
                new_sl = position.price_current + tsl_pips * point * 10
                if new_sl < position.sl or position.sl == 0:
                    self.modify_position(position, new_sl, position.tp)
        except Exception as e:
            logging.error(f"Error updating trailing stop: {e}")

    def modify_position(self, position, new_sl: float, new_tp: float) -> bool:
        try:
            result = mt5.order_send({
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": position.ticket,
                "sl":       new_sl,
                "tp":       new_tp,
            })
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"Modified {position.ticket}: SL={new_sl:.5f}")
                return True
            logging.warning(f"Modify failed: {result.comment}")
            return False
        except Exception as e:
            logging.error(f"Error modifying position: {e}")
            return False

    def close_position(self, position) -> bool:
        try:
            tick  = mt5.symbol_info_tick(position.symbol)
            price = tick.bid if position.type == 0 else tick.ask
            result = mt5.order_send({
                "action":       mt5.TRADE_ACTION_DEAL,
                "position":     position.ticket,
                "symbol":       position.symbol,
                "volume":       position.volume,
                "type":         mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY,
                "price":        price,
                "deviation":    self.slippage,
                "magic":        self.magic_number,
                "comment":      "Close",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            })
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                if position.profit > 0:
                    self.consecutive_wins  += 1
                    self.consecutive_losses = 0
                else:
                    self.consecutive_losses += 1
                    self.consecutive_wins   = 0

                # Notify AI Capital Allocator
                if self.strategy_manager:
                    self.strategy_manager.update_pair_result(position.symbol, position.profit)

                logging.info(f"Closed {position.ticket} profit=${position.profit:.2f}")
                return True
            logging.error(f"Close failed: {result.comment}")
            return False
        except Exception as e:
            logging.error(f"Error closing position: {e}")
            return False

    def close_all_positions(self):
        positions = mt5.positions_get()
        if positions:
            for p in positions:
                self.close_position(p)

    def _rr(self, entry, sl, tp) -> float:
        risk   = abs(entry - sl)
        reward = abs(tp - entry)
        return reward / risk if risk > 0 else 0