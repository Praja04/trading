import json
import logging
import os
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional, Tuple


class StrategyManager:
    """
    Flexible strategy manager — ALL behaviour is driven by the uploaded JSON.
    No hard-coded indicator weights, SL/TP values, or pair lists.
    """

    def __init__(self, strategy_path: str = None):
        self.strategy_config = None
        self.strategy_name = "Default"
        self.indicators_cache = {}

        if strategy_path and os.path.exists(strategy_path):
            self.load_strategy(strategy_path)
        else:
            logging.warning("No strategy file provided, using minimal default")
            self._load_default_strategy()

    # ------------------------------------------------------------------
    # LOADING
    # ------------------------------------------------------------------

    def load_strategy(self, strategy_path: str) -> bool:
        try:
            with open(strategy_path, 'r') as f:
                config = json.load(f)

            # Support both { "strategy_name": {...} } and flat { ... }
            if len(config) == 1 and isinstance(list(config.values())[0], dict):
                strategy_key = list(config.keys())[0]
                self.strategy_config = config[strategy_key]
                self.strategy_name = self.strategy_config.get('strategy_name', strategy_key)
            else:
                self.strategy_config = config
                self.strategy_name = config.get('strategy_name', 'Custom')

            logging.info(f"[OK] Strategy loaded: {self.strategy_name}")
            self._log_strategy_info()
            return True

        except Exception as e:
            logging.error(f"Error loading strategy from {strategy_path}: {e}")
            self._load_default_strategy()
            return False

    def _load_default_strategy(self):
        """Minimal default — only used when NO file is present at all."""
        self.strategy_config = {
            "strategy_name": "Default MA+RSI",
            "parameters": {
                "trading_pairs": [],
                "timeframes": ["M1"],
                "risk_per_trade_range": [0.01, 0.02],
                "max_positions": 3,
                "max_leverage": 100,
                "min_confidence": 60
            },
            "entry_conditions": {
                "indicators": {
                    "ma_fast": 10,
                    "ma_slow": 30,
                    "rsi_period": 14,
                    "rsi_oversold": 30,
                    "rsi_overbought": 70
                },
                # Default scoring weights — user can override in JSON
                "scoring": {
                    "ma_cross": 40,
                    "rsi": 35,
                    "macd": 25
                }
            },
            "exit_strategy": {
                "stop_loss_pips": 20,
                "take_profit_pips": 40
            }
        }
        self.strategy_name = "Default"

    def _log_strategy_info(self):
        logging.info("=" * 60)
        logging.info(f"STRATEGY : {self.strategy_name}")
        params = self.strategy_config.get('parameters', {})
        logging.info(f"Pairs    : {params.get('trading_pairs', [])}")
        logging.info(f"Timeframes: {params.get('timeframes', [])}")
        logging.info(f"Risk/trade: {params.get('risk_per_trade_range', [])}")
        logging.info(f"Max pos  : {params.get('max_positions', 'N/A')}")
        logging.info(f"Min conf : {params.get('min_confidence', 60)}%")
        logging.info("=" * 60)

    # ------------------------------------------------------------------
    # INDICATOR CALCULATION — driven entirely by strategy JSON
    # ------------------------------------------------------------------

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            df = df.copy()
            entry_cfg = self.strategy_config.get('entry_conditions', {})
            indic = {
                **entry_cfg.get('indicators', {}),
                **entry_cfg.get('momentum_confirmation', {})
            }

            if 'ma_fast' in indic:
                df['ma_fast'] = df['close'].rolling(int(indic['ma_fast'])).mean()
            if 'ma_slow' in indic:
                df['ma_slow'] = df['close'].rolling(int(indic['ma_slow'])).mean()
            if 'ema_fast' in indic:
                df['ema_fast'] = df['close'].ewm(span=int(indic['ema_fast']), adjust=False).mean()
            if 'ema_slow' in indic:
                df['ema_slow'] = df['close'].ewm(span=int(indic['ema_slow']), adjust=False).mean()
            if 'rsi_period' in indic:
                df['rsi'] = self._calculate_rsi(df['close'], int(indic['rsi_period']))
            if any(k in indic for k in ('macd_requirement', 'use_macd', 'macd_fast')):
                macd_fast   = int(indic.get('macd_fast',   12))
                macd_slow   = int(indic.get('macd_slow',   26))
                macd_signal = int(indic.get('macd_signal',  9))
                m = self._calculate_macd(df['close'], macd_fast, macd_slow, macd_signal)
                df['macd']           = m['macd']
                df['macd_signal']    = m['signal']
                df['macd_histogram'] = m['histogram']
            if any(k in indic for k in ('bollinger_bands', 'bb_period')):
                bb_period = int(indic.get('bb_period', 20))
                bb_std    = float(indic.get('bb_std_dev', 2))
                bb = self._calculate_bollinger_bands(df['close'], bb_period, bb_std)
                df['bb_upper']  = bb['upper']
                df['bb_middle'] = bb['middle']
                df['bb_lower']  = bb['lower']
            if 'atr_period' in indic:
                df['atr'] = self._calculate_atr(df, int(indic['atr_period']))
            if 'stochastic_period' in indic:
                stoch = self._calculate_stochastic(df, int(indic['stochastic_period']))
                df['stoch_k'] = stoch['k']
                df['stoch_d'] = stoch['d']

            df['momentum'] = df['close'].pct_change(periods=5) * 100
            return df

        except Exception as e:
            logging.error(f"Error calculating indicators: {e}")
            return df

    # ------------------------------------------------------------------
    # PRIVATE: math helpers
    # ------------------------------------------------------------------

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        delta = prices.diff()
        gain  = delta.where(delta > 0, 0).rolling(period).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs    = gain / loss
        return 100 - (100 / (1 + rs))

    def _calculate_macd(self, prices, fast=12, slow=26, signal=9):
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()
        macd     = ema_fast - ema_slow
        sig      = macd.ewm(span=signal, adjust=False).mean()
        return {'macd': macd, 'signal': sig, 'histogram': macd - sig}

    def _calculate_bollinger_bands(self, prices, period=20, std_dev=2):
        middle = prices.rolling(period).mean()
        std    = prices.rolling(period).std()
        return {'upper': middle + std * std_dev, 'middle': middle, 'lower': middle - std * std_dev}

    def _calculate_atr(self, df, period=14) -> pd.Series:
        hl  = df['high'] - df['low']
        hc  = np.abs(df['high'] - df['close'].shift())
        lc  = np.abs(df['low']  - df['close'].shift())
        tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def _calculate_stochastic(self, df, period=14, smooth_k=3, smooth_d=3):
        lo  = df['low'].rolling(period).min()
        hi  = df['high'].rolling(period).max()
        k   = 100 * (df['close'] - lo) / (hi - lo)
        k   = k.rolling(smooth_k).mean()
        d   = k.rolling(smooth_d).mean()
        return {'k': k, 'd': d}

    # ------------------------------------------------------------------
    # ANALYSIS — main entry point
    # ------------------------------------------------------------------

    def analyze(self, symbol: str, df: pd.DataFrame) -> Dict[str, Any]:
        try:
            df = self.calculate_indicators(df)

            if len(df) < 2:
                close = df.iloc[-1]['close'] if len(df) > 0 else 0
                return self._hold(close)

            latest = df.iloc[-1]
            prev   = df.iloc[-2]

            entry_cfg = self.strategy_config.get('entry_conditions', {})
            buy_score, sell_score = self._evaluate_conditions(latest, prev, entry_cfg)

            signal = {
                'action':     'HOLD',
                'confidence': 0,
                'price':      float(latest['close']),
                'timestamp':  datetime.now(),
                'strategy':   self.strategy_name,
                'indicators': self._extract_indicators(latest)
            }

            min_conf = self._get_min_confidence()

            if buy_score > sell_score and buy_score >= min_conf:
                signal['action']     = 'BUY'
                signal['confidence'] = buy_score
                signal['stop_loss'], signal['take_profit'] = self._exit_levels(
                    float(latest['close']), 'BUY', symbol, latest)

            elif sell_score > buy_score and sell_score >= min_conf:
                signal['action']     = 'SELL'
                signal['confidence'] = sell_score
                signal['stop_loss'], signal['take_profit'] = self._exit_levels(
                    float(latest['close']), 'SELL', symbol, latest)

            return signal

        except Exception as e:
            logging.error(f"Error analysing {symbol}: {e}")
            import traceback; logging.error(traceback.format_exc())
            return self._hold(0)

    # ------------------------------------------------------------------
    # SCORING — reads weights from strategy JSON
    # ------------------------------------------------------------------

    def _evaluate_conditions(self, latest, prev, entry_cfg) -> Tuple[int, int]:
        """
        Score based on weights defined in entry_conditions.scoring.
        If 'scoring' key is absent, falls back to equal-weight across
        whatever indicators are present in the data.
        """
        buy_score  = 0
        sell_score = 0

        indic    = {**entry_cfg.get('indicators', {}), **entry_cfg.get('momentum_confirmation', {})}
        # --- scoring weights from strategy JSON ---
        scoring  = entry_cfg.get('scoring', {})

        # Helper: get weight for a component, default = 25 if not specified
        def w(key, default=25):
            return scoring.get(key, default)

        # 1. Moving Average (ma_fast / ma_slow  OR  ema_fast / ema_slow)
        ma_score = w('ma_cross', 30)
        for fast_col, slow_col in [('ma_fast', 'ma_slow'), ('ema_fast', 'ema_slow')]:
            if fast_col in latest.index and slow_col in latest.index:
                if not pd.isna(latest[fast_col]) and not pd.isna(latest[slow_col]):
                    cross_bonus = w('ma_cross_bonus', 10)
                    if latest[fast_col] > latest[slow_col]:
                        buy_score += ma_score
                        if prev[fast_col] <= prev[slow_col]:
                            buy_score += cross_bonus
                    else:
                        sell_score += ma_score
                        if prev[fast_col] >= prev[slow_col]:
                            sell_score += cross_bonus
                    break   # only count once

        # 2. RSI
        if 'rsi' in latest.index and not pd.isna(latest['rsi']):
            rsi_score    = w('rsi', 25)
            rsi_oversold  = indic.get('rsi_oversold',  30)
            rsi_overbought= indic.get('rsi_overbought', 70)
            # support rsi_range list format
            if 'rsi_range' in indic:
                rsi_range = indic['rsi_range']
                rsi_oversold, rsi_overbought = rsi_range[0], rsi_range[1]

            rsi = latest['rsi']
            if rsi < rsi_oversold:
                buy_score  += rsi_score
            elif rsi > rsi_overbought:
                sell_score += rsi_score
            elif rsi < 50:
                buy_score  += int(rsi_score * 0.4)
            else:
                sell_score += int(rsi_score * 0.4)

        # 3. MACD
        if 'macd' in latest.index and 'macd_signal' in latest.index:
            if not pd.isna(latest['macd']) and not pd.isna(latest['macd_signal']):
                macd_score = w('macd', 25)
                cross_bonus = w('macd_cross_bonus', 10)
                if latest['macd'] > latest['macd_signal']:
                    buy_score  += macd_score
                    if prev['macd'] <= prev['macd_signal']:
                        buy_score += cross_bonus
                else:
                    sell_score += macd_score
                    if prev['macd'] >= prev['macd_signal']:
                        sell_score += cross_bonus

        # 4. Bollinger Bands
        if 'bb_upper' in latest.index and 'bb_lower' in latest.index:
            if not pd.isna(latest['bb_upper']) and not pd.isna(latest['bb_lower']):
                bb_score = w('bollinger_bands', 20)
                if latest['close'] < latest['bb_lower']:
                    buy_score  += bb_score
                elif latest['close'] > latest['bb_upper']:
                    sell_score += bb_score

        # 5. Stochastic
        if 'stoch_k' in latest.index and not pd.isna(latest['stoch_k']):
            stoch_score = w('stochastic', 15)
            stoch_os  = indic.get('stochastic_oversold',  20)
            stoch_ob  = indic.get('stochastic_overbought', 80)
            if latest['stoch_k'] < stoch_os:
                buy_score  += stoch_score
            elif latest['stoch_k'] > stoch_ob:
                sell_score += stoch_score

        # 6. Momentum
        if 'momentum' in latest.index and not pd.isna(latest['momentum']):
            mom_score = w('momentum', 10)
            if latest['momentum'] > 0:
                buy_score  += mom_score
            else:
                sell_score += mom_score

        return buy_score, sell_score

    # ------------------------------------------------------------------
    # EXIT LEVELS — fully from strategy JSON
    # ------------------------------------------------------------------

    def _exit_levels(self, entry: float, action: str, symbol: str,
                     latest: pd.Series) -> Tuple[float, float]:
        exit_cfg = self.strategy_config.get('exit_strategy', {})
        pip_value = 0.01 if 'JPY' in symbol else 0.0001

        # --- ATR-based (dynamic) ---
        use_atr = exit_cfg.get('use_atr', False) or 'atr_multiplier_sl' in exit_cfg
        if use_atr and 'atr' in latest.index and not pd.isna(latest['atr']):
            atr        = float(latest['atr'])
            sl_mult    = float(exit_cfg.get('atr_multiplier_sl', 1.5))
            tp_mult    = float(exit_cfg.get('atr_multiplier_tp', 2.5))
            sl_dist    = atr * sl_mult
            tp_dist    = atr * tp_mult

            if action == 'BUY':
                return entry - sl_dist, entry + tp_dist
            else:
                return entry + sl_dist, entry - tp_dist

        # --- Fixed pips ---
        sl_pips = self._parse_pips(exit_cfg.get('stop_loss',    exit_cfg.get('stop_loss_pips',    20)))
        tp_pips = self._parse_pips(exit_cfg.get('take_profit',  exit_cfg.get('take_profit_pips',  40)))

        sl_dist = sl_pips * pip_value
        tp_dist = tp_pips * pip_value

        if action == 'BUY':
            return entry - sl_dist, entry + tp_dist
        else:
            return entry + sl_dist, entry - tp_dist

    def _parse_pips(self, value, default=20) -> int:
        try:
            if isinstance(value, (int, float)):
                return int(value)
            import re
            nums = re.findall(r'\d+', str(value))
            return int(nums[0]) if nums else default
        except:
            return default

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _get_min_confidence(self) -> float:
        params    = self.strategy_config.get('parameters', {})
        entry_cfg = self.strategy_config.get('entry_conditions', {})
        conf      = entry_cfg.get('min_confidence', params.get('min_confidence', 60))
        return conf * 100 if conf < 1 else conf

    def _extract_indicators(self, latest: pd.Series) -> Dict:
        keys = ['ma_fast', 'ma_slow', 'ema_fast', 'ema_slow', 'rsi',
                'macd', 'macd_signal', 'bb_upper', 'bb_lower', 'bb_middle',
                'momentum', 'atr', 'stoch_k', 'stoch_d']
        return {k: float(latest[k]) for k in keys if k in latest.index and not pd.isna(latest[k])}

    def _hold(self, price: float) -> Dict:
        return {
            'action': 'HOLD', 'confidence': 0, 'price': price,
            'timestamp': datetime.now(), 'strategy': self.strategy_name, 'indicators': {}
        }

    # ------------------------------------------------------------------
    # PUBLIC API used by TradeExecutor & main.py
    # ------------------------------------------------------------------

    def get_risk_parameters(self) -> Dict:
        params    = self.strategy_config.get('parameters', {})
        kelly_cfg = self.strategy_config.get('unique_features', {}).get('kelly_optimization', {})
        rr        = params.get('risk_per_trade_range', [0.01, 0.02])
        return {
            'risk_per_trade_min':    rr[0],
            'risk_per_trade_max':    rr[1],
            'max_leverage':          params.get('max_leverage',       100),
            'max_drawdown_limit':    params.get('max_drawdown_limit', 0.15),
            'max_positions':         params.get('max_positions',       3),
            'kelly_base':            kelly_cfg.get('base_kelly', 0.25) if kelly_cfg else 0.25
        }

    def get_strategy_info(self) -> Dict:
        params = self.strategy_config.get('parameters', {})
        # Support both key names
        pairs  = (params.get('trading_pairs') or
                  params.get('pairs') or
                  self.strategy_config.get('trading_pairs') or
                  self.strategy_config.get('pairs') or [])
        return {
            'name':                self.strategy_name,
            'philosophy':          self.strategy_config.get('core_philosophy', 'N/A'),
            'timeframes':          params.get('timeframes', ['M1']),
            'pairs':               pairs,
            'performance_targets': self.strategy_config.get('performance_targets', {})
        }