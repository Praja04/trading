import json
import logging
import os
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional

class StrategyManager:
    """
    Flexible strategy manager that loads and executes strategies from JSON configuration
    """
    
    def __init__(self, strategy_path: str = None):
        """
        Initialize strategy manager
        
        Args:
            strategy_path: Path to JSON strategy file
        """
        self.strategy_config = None
        self.strategy_name = "Default"
        self.indicators_cache = {}
        
        if strategy_path and os.path.exists(strategy_path):
            self.load_strategy(strategy_path)
        else:
            logging.warning("No strategy file provided, using default strategy")
            self._load_default_strategy()
    
    def load_strategy(self, strategy_path: str) -> bool:
        """
        Load strategy from JSON file
        
        Args:
            strategy_path: Path to JSON strategy file
            
        Returns:
            bool: Success status
        """
        try:
            with open(strategy_path, 'r') as f:
                config = json.load(f)
            
            # Support different JSON structures
            if len(config) == 1:
                # Get the first key (strategy name)
                strategy_key = list(config.keys())[0]
                self.strategy_config = config[strategy_key]
                self.strategy_name = self.strategy_config.get('strategy_name', strategy_key)
            else:
                self.strategy_config = config
                self.strategy_name = config.get('strategy_name', 'Custom')
            
            logging.info(f"✓ Strategy loaded: {self.strategy_name}")
            self._log_strategy_info()
            return True
            
        except Exception as e:
            logging.error(f"Error loading strategy from {strategy_path}: {str(e)}")
            self._load_default_strategy()
            return False
    
    def _load_default_strategy(self):
        """Load default strategy configuration"""
        self.strategy_config = {
            "strategy_name": "Default MA+RSI",
            "parameters": {
                "timeframes": ["M1"],
                "risk_per_trade_range": [0.01, 0.02],
                "max_leverage": 100
            },
            "entry_conditions": {
                "indicators": {
                    "ma_fast": 10,
                    "ma_slow": 30,
                    "rsi_period": 14,
                    "rsi_oversold": 30,
                    "rsi_overbought": 70
                }
            },
            "exit_strategy": {
                "stop_loss": "20_pips",
                "take_profit": "30_pips"
            }
        }
        self.strategy_name = "Default"
    
    def _log_strategy_info(self):
        """Log loaded strategy information"""
        logging.info("="*60)
        logging.info(f"STRATEGY: {self.strategy_name}")
        
        params = self.strategy_config.get('parameters', {})
        if params:
            logging.info(f"Timeframes: {params.get('timeframes', 'Not specified')}")
            logging.info(f"Risk per trade: {params.get('risk_per_trade_range', 'Not specified')}")
            logging.info(f"Max leverage: {params.get('max_leverage', 'Not specified')}")
        
        performance = self.strategy_config.get('performance_targets', {})
        if performance:
            logging.info(f"Target winrate: {performance.get('winrate', 'Not specified')}")
            logging.info(f"Target profit factor: {performance.get('profit_factor', 'Not specified')}")
        
        logging.info("="*60)
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate indicators based on strategy configuration
        
        Args:
            df: OHLC dataframe
            
        Returns:
            DataFrame with indicators
        """
        try:
            df = df.copy()
            
            # Get entry conditions
            entry_config = self.strategy_config.get('entry_conditions', {})
            
            # Check for indicators in entry_conditions
            indicators = entry_config.get('indicators', {})
            momentum = entry_config.get('momentum_confirmation', {})
            
            # Merge indicators from both sources
            all_indicators = {**indicators, **momentum}
            
            # Calculate Moving Averages
            if 'ma_fast' in all_indicators:
                ma_fast = int(all_indicators['ma_fast'])
                df['ma_fast'] = df['close'].rolling(window=ma_fast).mean()
            
            if 'ma_slow' in all_indicators:
                ma_slow = int(all_indicators['ma_slow'])
                df['ma_slow'] = df['close'].rolling(window=ma_slow).mean()
            
            # Calculate RSI
            if 'rsi_period' in all_indicators:
                rsi_period = int(all_indicators['rsi_period'])
                df['rsi'] = self._calculate_rsi(df['close'], rsi_period)
            
            # Calculate MACD
            if 'macd_requirement' in all_indicators or 'use_macd' in all_indicators:
                macd_data = self._calculate_macd(df['close'])
                df['macd'] = macd_data['macd']
                df['macd_signal'] = macd_data['signal']
                df['macd_histogram'] = macd_data['histogram']
            
            # Calculate Bollinger Bands
            if 'bollinger_bands' in all_indicators or 'bb_period' in all_indicators:
                bb_period = all_indicators.get('bb_period', 20)
                bb_std = all_indicators.get('bb_std_dev', 2)
                bb_data = self._calculate_bollinger_bands(df['close'], bb_period, bb_std)
                df['bb_upper'] = bb_data['upper']
                df['bb_middle'] = bb_data['middle']
                df['bb_lower'] = bb_data['lower']
            
            # Calculate ATR (for volatility)
            if 'atr_period' in all_indicators:
                atr_period = int(all_indicators['atr_period'])
                df['atr'] = self._calculate_atr(df, atr_period)
            
            # Calculate Stochastic
            if 'stochastic_period' in all_indicators:
                stoch_period = int(all_indicators['stochastic_period'])
                stoch_data = self._calculate_stochastic(df, stoch_period)
                df['stoch_k'] = stoch_data['k']
                df['stoch_d'] = stoch_data['d']
            
            # Calculate momentum
            df['momentum'] = df['close'].pct_change(periods=5) * 100
            
            return df
            
        except Exception as e:
            logging.error(f"Error calculating indicators: {str(e)}")
            return df
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI indicator"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def _calculate_macd(self, prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
        """Calculate MACD indicator"""
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        macd_signal = macd.ewm(span=signal, adjust=False).mean()
        macd_histogram = macd - macd_signal
        
        return {
            'macd': macd,
            'signal': macd_signal,
            'histogram': macd_histogram
        }
    
    def _calculate_bollinger_bands(self, prices: pd.Series, period: int = 20, std_dev: float = 2) -> Dict:
        """Calculate Bollinger Bands"""
        middle = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        
        return {
            'upper': upper,
            'middle': middle,
            'lower': lower
        }
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range"""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()
        
        return atr
    
    def _calculate_stochastic(self, df: pd.DataFrame, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> Dict:
        """Calculate Stochastic Oscillator"""
        low_min = df['low'].rolling(window=period).min()
        high_max = df['high'].rolling(window=period).max()
        
        stoch_k = 100 * (df['close'] - low_min) / (high_max - low_min)
        stoch_k = stoch_k.rolling(window=smooth_k).mean()
        stoch_d = stoch_k.rolling(window=smooth_d).mean()
        
        return {
            'k': stoch_k,
            'd': stoch_d
        }
    
    def analyze(self, symbol: str, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Analyze market data and generate trading signal based on strategy
        
        Args:
            symbol: Trading symbol
            df: OHLC dataframe with indicators
            
        Returns:
            Signal dictionary with action, confidence, price, etc.
        """
        try:
            # Calculate indicators
            df = self.calculate_indicators(df)
            
            # Get latest data
            if len(df) < 2:
                return self._get_hold_signal(df.iloc[-1]['close'] if len(df) > 0 else 0)
            
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # Initialize signal
            signal = {
                'action': 'HOLD',
                'confidence': 0,
                'price': latest['close'],
                'timestamp': datetime.now(),
                'strategy': self.strategy_name,
                'indicators': {}
            }
            
            # Check if we have enough data
            if pd.isna(latest.get('ma_slow')) and pd.isna(latest.get('rsi')):
                return signal
            
            # Get entry conditions from strategy
            entry_config = self.strategy_config.get('entry_conditions', {})
            
            # Evaluate signal based on strategy configuration
            buy_score, sell_score = self._evaluate_conditions(df, latest, prev, entry_config)
            
            # Store indicator values
            signal['indicators'] = self._extract_indicator_values(latest)
            
            # Determine action based on scores
            min_confidence = self._get_min_confidence()
            
            if buy_score > sell_score and buy_score >= min_confidence:
                signal['action'] = 'BUY'
                signal['confidence'] = buy_score
                signal['stop_loss'], signal['take_profit'] = self._calculate_exit_levels(
                    latest['close'], 'BUY', symbol, latest
                )
                
            elif sell_score > buy_score and sell_score >= min_confidence:
                signal['action'] = 'SELL'
                signal['confidence'] = sell_score
                signal['stop_loss'], signal['take_profit'] = self._calculate_exit_levels(
                    latest['close'], 'SELL', symbol, latest
                )
            
            return signal
            
        except Exception as e:
            logging.error(f"Error in strategy analysis for {symbol}: {str(e)}")
            import traceback
            logging.error(traceback.format_exc())
            return self._get_hold_signal(0)
    
    def _evaluate_conditions(self, df: pd.DataFrame, latest: pd.Series, prev: pd.Series, 
                            entry_config: Dict) -> tuple:
        """
        Evaluate entry conditions and return buy/sell scores
        
        Returns:
            tuple: (buy_score, sell_score)
        """
        buy_score = 0
        sell_score = 0
        
        # Get configuration
        indicators = entry_config.get('indicators', {})
        momentum = entry_config.get('momentum_confirmation', {})
        
        # 1. Moving Average Analysis (30 points)
        if 'ma_fast' in latest and 'ma_slow' in latest:
            if not pd.isna(latest['ma_fast']) and not pd.isna(latest['ma_slow']):
                if latest['ma_fast'] > latest['ma_slow']:
                    buy_score += 30
                    if prev['ma_fast'] <= prev['ma_slow']:  # Golden cross
                        buy_score += 10
                else:
                    sell_score += 30
                    if prev['ma_fast'] >= prev['ma_slow']:  # Death cross
                        sell_score += 10
        
        # 2. RSI Analysis (25 points)
        if 'rsi' in latest and not pd.isna(latest['rsi']):
            rsi_oversold = momentum.get('rsi_range', [30, 70])[0] if 'rsi_range' in momentum else indicators.get('rsi_oversold', 30)
            rsi_overbought = momentum.get('rsi_range', [30, 70])[1] if 'rsi_range' in momentum else indicators.get('rsi_overbought', 70)
            
            if latest['rsi'] < rsi_oversold:
                buy_score += 25
            elif latest['rsi'] > rsi_overbought:
                sell_score += 25
            elif latest['rsi'] < 50:
                buy_score += 10
            else:
                sell_score += 10
        
        # 3. MACD Analysis (25 points)
        if 'macd' in latest and 'macd_signal' in latest:
            if not pd.isna(latest['macd']) and not pd.isna(latest['macd_signal']):
                macd_req = momentum.get('macd_requirement', '')
                
                if latest['macd'] > latest['macd_signal']:
                    buy_score += 25
                    if prev['macd'] <= prev['macd_signal']:  # MACD cross up
                        buy_score += 10
                else:
                    sell_score += 25
                    if prev['macd'] >= prev['macd_signal']:  # MACD cross down
                        sell_score += 10
        
        # 4. Bollinger Bands (20 points)
        if 'bb_upper' in latest and 'bb_lower' in latest:
            if not pd.isna(latest['bb_upper']) and not pd.isna(latest['bb_lower']):
                if latest['close'] < latest['bb_lower']:
                    buy_score += 20  # Oversold
                elif latest['close'] > latest['bb_upper']:
                    sell_score += 20  # Overbought
        
        # 5. Momentum (10 points)
        if 'momentum' in latest and not pd.isna(latest['momentum']):
            if latest['momentum'] > 0:
                buy_score += 10
            else:
                sell_score += 10
        
        # 6. Stochastic (bonus if available)
        if 'stoch_k' in latest and 'stoch_d' in latest:
            if not pd.isna(latest['stoch_k']) and not pd.isna(latest['stoch_d']):
                if latest['stoch_k'] < 20:
                    buy_score += 15
                elif latest['stoch_k'] > 80:
                    sell_score += 15
        
        return buy_score, sell_score
    
    def _calculate_exit_levels(self, entry_price: float, action: str, symbol: str, 
                               latest: pd.Series) -> tuple:
        """
        Calculate stop loss and take profit levels
        
        Returns:
            tuple: (stop_loss, take_profit)
        """
        exit_config = self.strategy_config.get('exit_strategy', {})
        
        # Get stop loss configuration
        sl_config = exit_config.get('stop_loss', '20_pips')
        tp_config = exit_config.get('take_profit', '30_pips')
        
        # Parse pip values
        sl_pips = self._parse_pips(sl_config, default=20)
        tp_pips = self._parse_pips(tp_config, default=30)
        
        # Calculate pip value based on symbol
        pip_value = 0.0001  # Default for most pairs
        if 'JPY' in symbol:
            pip_value = 0.01
        
        # Use ATR if available for dynamic SL/TP
        if 'atr' in latest and not pd.isna(latest['atr']):
            atr = latest['atr']
            # Use ATR multiplier if specified
            atr_multiplier_sl = exit_config.get('atr_multiplier_sl', 2.0)
            atr_multiplier_tp = exit_config.get('atr_multiplier_tp', 3.0)
            
            if action == 'BUY':
                stop_loss = entry_price - (atr * atr_multiplier_sl)
                take_profit = entry_price + (atr * atr_multiplier_tp)
            else:  # SELL
                stop_loss = entry_price + (atr * atr_multiplier_sl)
                take_profit = entry_price - (atr * atr_multiplier_tp)
        else:
            # Use fixed pips
            if action == 'BUY':
                stop_loss = entry_price - (sl_pips * pip_value)
                take_profit = entry_price + (tp_pips * pip_value)
            else:  # SELL
                stop_loss = entry_price + (sl_pips * pip_value)
                take_profit = entry_price - (tp_pips * pip_value)
        
        return stop_loss, take_profit
    
    def _parse_pips(self, config_str: str, default: int = 20) -> int:
        """Parse pip value from configuration string"""
        try:
            if isinstance(config_str, (int, float)):
                return int(config_str)
            
            # Extract number from string like "20_pips" or "2%_from_entry"
            import re
            numbers = re.findall(r'\d+', str(config_str))
            if numbers:
                return int(numbers[0])
            return default
        except:
            return default
    
    def _get_min_confidence(self) -> float:
        """Get minimum confidence threshold from strategy config"""
        # Check in multiple possible locations
        params = self.strategy_config.get('parameters', {})
        entry_config = self.strategy_config.get('entry_conditions', {})
        
        # Look for confidence threshold
        confidence = params.get('min_confidence', 60)
        confidence = entry_config.get('min_confidence', confidence)
        
        # Convert to percentage if it's a decimal (like 0.92)
        if confidence < 1:
            confidence = confidence * 100
        
        return confidence
    
    def _extract_indicator_values(self, latest: pd.Series) -> Dict:
        """Extract indicator values from latest data"""
        indicators = {}
        
        indicator_keys = ['ma_fast', 'ma_slow', 'rsi', 'macd', 'macd_signal', 
                         'bb_upper', 'bb_lower', 'momentum', 'atr', 'stoch_k', 'stoch_d']
        
        for key in indicator_keys:
            if key in latest and not pd.isna(latest[key]):
                indicators[key] = float(latest[key])
        
        return indicators
    
    def _get_hold_signal(self, price: float) -> Dict:
        """Return a HOLD signal"""
        return {
            'action': 'HOLD',
            'confidence': 0,
            'price': price,
            'timestamp': datetime.now(),
            'strategy': self.strategy_name,
            'indicators': {}
        }
    
    def get_risk_parameters(self) -> Dict:
        """Get risk management parameters from strategy"""
        params = self.strategy_config.get('parameters', {})
        kelly_config = self.strategy_config.get('unique_features', {}).get('kelly_optimization', {})
        
        return {
            'risk_per_trade_min': params.get('risk_per_trade_range', [0.01, 0.02])[0],
            'risk_per_trade_max': params.get('risk_per_trade_range', [0.01, 0.02])[1],
            'max_leverage': params.get('max_leverage', 100),
            'max_drawdown_limit': params.get('max_drawdown_limit', 0.15),
            'kelly_base': kelly_config.get('base_kelly', 0.25) if kelly_config else 0.25
        }
    
    def get_strategy_info(self) -> Dict:
        """Get strategy information for display"""
        return {
            'name': self.strategy_name,
            'philosophy': self.strategy_config.get('core_philosophy', 'N/A'),
            'timeframes': self.strategy_config.get('parameters', {}).get('timeframes', ['M1']),
            'pairs': self.strategy_config.get('parameters', {}).get('trading_pairs', []),
            'performance_targets': self.strategy_config.get('performance_targets', {})
        }