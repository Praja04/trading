import MetaTrader5 as mt5
import logging
from datetime import datetime
from typing import Optional

class TradeExecutor:
    """Execute and manage trading orders on MT5 with flexible strategy support"""
    
    def __init__(self, broker_config, trading_config, strategy_manager=None):
        self.broker_config = broker_config
        self.trading_config = trading_config
        self.strategy_manager = strategy_manager
        
        # Get risk parameters from strategy if available
        if strategy_manager:
            risk_params = strategy_manager.get_risk_parameters()
            self.risk_per_trade_min = risk_params['risk_per_trade_min']
            self.risk_per_trade_max = risk_params['risk_per_trade_max']
            self.max_leverage = risk_params['max_leverage']
            self.max_drawdown_limit = risk_params['max_drawdown_limit']
        else:
            # Fallback to config
            self.risk_per_trade_min = 0.01
            self.risk_per_trade_max = trading_config.get('max_risk_per_trade', 2) / 100
            self.max_leverage = 100
            self.max_drawdown_limit = 0.15
        
        # Trading parameters
        self.lot_size = trading_config.get('lot_size', 0.01)
        self.max_positions = trading_config.get('max_positions', 3)
        self.slippage = trading_config.get('slippage', 10)
        self.magic_number = trading_config.get('magic_number', 234000)
        
        # Risk management
        self.max_daily_loss = trading_config.get('max_daily_loss', 100)
        
        self.daily_pnl = 0
        self.active_positions = {}
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        
    def execute_signal(self, symbol, signal):
        """Execute trading signal (BUY or SELL)"""
        try:
            # Check if maximum positions reached
            positions = mt5.positions_get(symbol=symbol)
            if positions is not None and len(positions) >= self.max_positions:
                logging.warning(f"Max positions reached for {symbol}")
                return False
            
            # Check daily loss limit
            if self.daily_pnl <= -self.max_daily_loss:
                logging.warning(f"Daily loss limit reached: ${self.daily_pnl:.2f}")
                return False
            
            # Get symbol info
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                logging.error(f"Symbol {symbol} not found")
                return False
            
            if not symbol_info.visible:
                if not mt5.symbol_select(symbol, True):
                    logging.error(f"Failed to select {symbol}")
                    return False
            
            # Prepare order request
            point = symbol_info.point
            price = signal['price']
            
            if signal['action'] == 'BUY':
                order_type = mt5.ORDER_TYPE_BUY
                price = mt5.symbol_info_tick(symbol).ask
                sl = signal.get('stop_loss', price - 20 * point)
                tp = signal.get('take_profit', price + 30 * point)
                
            elif signal['action'] == 'SELL':
                order_type = mt5.ORDER_TYPE_SELL
                price = mt5.symbol_info_tick(symbol).bid
                sl = signal.get('stop_loss', price + 20 * point)
                tp = signal.get('take_profit', price - 30 * point)
            else:
                return False
            
            # Calculate lot size based on risk and strategy
            lot = self.calculate_position_size(symbol, price, sl, signal)
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": self.slippage,
                "magic": self.magic_number,
                "comment": f"{signal.get('strategy', 'Auto')}_{signal['confidence']}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            # Send order
            result = mt5.order_send(request)
            
            if result is None:
                logging.error(f"Order send failed: {mt5.last_error()}")
                return False
            
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                logging.error(f"Order failed: {result.retcode} - {result.comment}")
                return False
            
            # Log successful trade
            logging.info("="*60)
            logging.info(f"[OK] ORDER EXECUTED SUCCESSFULLY")
            logging.info(f"Symbol: {symbol}")
            logging.info(f"Strategy: {signal.get('strategy', 'Auto')}")
            logging.info(f"Action: {signal['action']}")
            logging.info(f"Volume: {lot}")
            logging.info(f"Price: {price:.5f}")
            logging.info(f"Stop Loss: {sl:.5f}")
            logging.info(f"Take Profit: {tp:.5f}")
            logging.info(f"Confidence: {signal['confidence']}%")
            logging.info(f"Order ID: {result.order}")
            logging.info(f"Risk/Reward: 1:{self._calculate_risk_reward(price, sl, tp):.2f}")
            logging.info("="*60)
            
            return True
            
        except Exception as e:
            logging.error(f"Error executing signal for {symbol}: {str(e)}")
            import traceback
            logging.error(traceback.format_exc())
            return False
    
    def calculate_position_size(self, symbol, entry_price, stop_loss, signal):
        """
        Calculate position size based on risk management and strategy
        Implements Kelly Criterion and anti-martingale if strategy supports it
        """
        try:
            # Get account balance
            account_info = mt5.account_info()
            if account_info is None:
                return self.lot_size
            
            balance = account_info.balance
            
            # Calculate base risk amount
            risk_percent = self.risk_per_trade_min
            
            # Apply confidence-based position sizing
            if signal.get('confidence', 0) > 80:
                risk_percent = self.risk_per_trade_max
            elif signal.get('confidence', 0) > 70:
                risk_percent = (self.risk_per_trade_min + self.risk_per_trade_max) / 2
            
            # Anti-martingale: increase position size after wins
            if self.strategy_manager:
                unique_features = self.strategy_manager.strategy_config.get('unique_features', {})
                anti_martingale = unique_features.get('anti_martingale_progression', {})
                
                if anti_martingale:
                    win_trigger = anti_martingale.get('consecutive_wins_trigger', 3)
                    size_increase = anti_martingale.get('position_size_increase', 0.5)
                    max_multiplier = anti_martingale.get('max_position_size_multiplier', 4.0)
                    
                    if self.consecutive_wins >= win_trigger:
                        multiplier = 1 + (size_increase * (self.consecutive_wins - win_trigger + 1))
                        multiplier = min(multiplier, max_multiplier)
                        risk_percent *= multiplier
                        logging.info(f"Anti-martingale: {self.consecutive_wins} wins, multiplier: {multiplier:.2f}x")
                    
                    # Recovery after loss
                    if self.consecutive_losses > 0:
                        recovery_extra = anti_martingale.get('recovery_extra_risk_after_loss', 0.005)
                        risk_percent += recovery_extra
                        logging.info(f"Recovery mode: adding {recovery_extra*100}% risk")
            
            # Ensure we don't exceed max risk
            risk_percent = min(risk_percent, self.risk_per_trade_max)
            risk_amount = balance * risk_percent
            
            # Calculate risk in pips
            symbol_info = mt5.symbol_info(symbol)
            pip_value = symbol_info.point * 10
            
            risk_pips = abs(entry_price - stop_loss) / pip_value
            
            if risk_pips <= 0:
                return self.lot_size
            
            # Calculate lot size
            # For forex: 1 lot = 100,000 units, pip value varies by pair
            tick_value = symbol_info.trade_tick_value
            tick_size = symbol_info.trade_tick_size
            
            # Calculate position size
            lot = risk_amount / (risk_pips * 10)  # Simplified calculation
            
            # Adjust based on tick value if available
            if tick_value > 0 and tick_size > 0:
                lot = risk_amount / (risk_pips * tick_value / tick_size)
            
            # Round to valid lot size
            lot = max(symbol_info.volume_min, min(lot, symbol_info.volume_max))
            lot = round(lot / symbol_info.volume_step) * symbol_info.volume_step
            
            logging.info(f"Position sizing: Risk={risk_percent*100:.2f}%, Amount=${risk_amount:.2f}, Pips={risk_pips:.1f}, Lot={lot:.2f}")
            
            return lot
            
        except Exception as e:
            logging.error(f"Error calculating position size: {str(e)}")
            return self.lot_size
    
    def _calculate_risk_reward(self, entry, sl, tp):
        """Calculate risk/reward ratio"""
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        
        if risk == 0:
            return 0
        
        return reward / risk
    
    def manage_positions(self):
        """Monitor and manage open positions"""
        try:
            positions = mt5.positions_get()
            
            if positions is None or len(positions) == 0:
                return
            
            total_profit = 0
            
            for position in positions:
                profit = position.profit
                total_profit += profit
                
                # Calculate pips
                symbol_info = mt5.symbol_info(position.symbol)
                if 'JPY' in position.symbol:
                    pips = (position.price_current - position.price_open) * 100 if position.type == 0 else (position.price_open - position.price_current) * 100
                else:
                    pips = (position.price_current - position.price_open) * 10000 if position.type == 0 else (position.price_open - position.price_current) * 10000
                
                # Log position status
                status = "[+]" if profit >= 0 else "[-]"
                logging.info(
                    f"{status} Position {position.ticket}: {position.symbol} | "
                    f"{'BUY' if position.type == 0 else 'SELL'} | "
                    f"Vol: {position.volume} | "
                    f"Price: {position.price_open:.5f} -> {position.price_current:.5f} | "
                    f"Pips: {pips:+.1f} | "
                    f"Profit: ${profit:.2f}"
                )
                
                # Optional: Trailing stop logic
                self.update_trailing_stop(position)
            
            # Update daily PnL
            self.daily_pnl = total_profit
            
            logging.info(f"Total Open Positions: {len(positions)} | Total P&L: ${total_profit:.2f}")
            
        except Exception as e:
            logging.error(f"Error managing positions: {str(e)}")
    
    def update_trailing_stop(self, position):
        """Update trailing stop for profitable positions"""
        try:
            trailing_stop_enabled = self.trading_config.get('trailing_stop_enabled', False)
            if not trailing_stop_enabled:
                return
            
            trailing_stop_pips = self.trading_config.get('trailing_stop_pips', 15)
            
            symbol_info = mt5.symbol_info(position.symbol)
            point = symbol_info.point
            
            # Calculate new stop loss
            if position.type == mt5.ORDER_TYPE_BUY:
                new_sl = position.price_current - (trailing_stop_pips * point * 10)
                if new_sl > position.sl:
                    self.modify_position(position, new_sl, position.tp)
                    
            elif position.type == mt5.ORDER_TYPE_SELL:
                new_sl = position.price_current + (trailing_stop_pips * point * 10)
                if new_sl < position.sl or position.sl == 0:
                    self.modify_position(position, new_sl, position.tp)
            
        except Exception as e:
            logging.error(f"Error updating trailing stop: {str(e)}")
    
    def modify_position(self, position, new_sl, new_tp):
        """Modify position stop loss and take profit"""
        try:
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": position.ticket,
                "sl": new_sl,
                "tp": new_tp,
            }
            
            result = mt5.order_send(request)
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"Position {position.ticket} modified: SL={new_sl:.5f}")
                return True
            else:
                logging.warning(f"Failed to modify position: {result.comment}")
                return False
                
        except Exception as e:
            logging.error(f"Error modifying position: {str(e)}")
            return False
    
    def close_position(self, position):
        """Close a specific position"""
        try:
            tick = mt5.symbol_info_tick(position.symbol)
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": position.ticket,
                "symbol": position.symbol,
                "volume": position.volume,
                "type": mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY,
                "price": tick.bid if position.type == 0 else tick.ask,
                "deviation": self.slippage,
                "magic": self.magic_number,
                "comment": "Close position",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                # Track wins/losses for anti-martingale
                if position.profit > 0:
                    self.consecutive_wins += 1
                    self.consecutive_losses = 0
                else:
                    self.consecutive_losses += 1
                    self.consecutive_wins = 0
                
                logging.info(f"Position {position.ticket} closed with profit: ${position.profit:.2f}")
                logging.info(f"Win streak: {self.consecutive_wins}, Loss streak: {self.consecutive_losses}")
                return True
            else:
                logging.error(f"Failed to close position: {result.comment}")
                return False
                
        except Exception as e:
            logging.error(f"Error closing position: {str(e)}")
            return False
    
    def close_all_positions(self):
        """Close all open positions"""
        positions = mt5.positions_get()
        if positions is None:
            return
        
        for position in positions:
            self.close_position(position)