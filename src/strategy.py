import pandas as pd
import numpy as np
import logging
from datetime import datetime

class TradingStrategy:
    """
    Trading strategy with multiple indicators:
    - Moving Average Crossover
    - RSI (Relative Strength Index)
    - MACD (Moving Average Convergence Divergence)
    - Support/Resistance levels
    """
    
    def __init__(self, config):
        self.config = config
        self.strategy_config = config.get('strategy', {})
        
        # Strategy parameters
        self.ma_fast = self.strategy_config.get('ma_fast', 10)
        self.ma_slow = self.strategy_config.get('ma_slow', 30)
        self.rsi_period = self.strategy_config.get('rsi_period', 14)
        self.rsi_overbought = self.strategy_config.get('rsi_overbought', 70)
        self.rsi_oversold = self.strategy_config.get('rsi_oversold', 30)
        
        # Risk management
        self.min_profit_pips = self.strategy_config.get('min_profit_pips', 10)
        self.stop_loss_pips = self.strategy_config.get('stop_loss_pips', 20)
        self.take_profit_pips = self.strategy_config.get('take_profit_pips', 30)
        
    def calculate_indicators(self, df):
        """Calculate all technical indicators"""
        try:
            df = df.copy()
            
            # Moving Averages
            df['ma_fast'] = df['close'].rolling(window=self.ma_fast).mean()
            df['ma_slow'] = df['close'].rolling(window=self.ma_slow).mean()
            
            # RSI
            df['rsi'] = self.calculate_rsi(df['close'], self.rsi_period)
            
            # MACD
            macd_data = self.calculate_macd(df['close'])
            df['macd'] = macd_data['macd']
            df['macd_signal'] = macd_data['signal']
            df['macd_histogram'] = macd_data['histogram']
            
            # Bollinger Bands
            bb_data = self.calculate_bollinger_bands(df['close'])
            df['bb_upper'] = bb_data['upper']
            df['bb_middle'] = bb_data['middle']
            df['bb_lower'] = bb_data['lower']
            
            # Price momentum
            df['momentum'] = df['close'].pct_change(periods=5) * 100
            
            return df
            
        except Exception as e:
            logging.error(f"Error calculating indicators: {str(e)}")
            return df
    
    def calculate_rsi(self, prices, period=14):
        """Calculate Relative Strength Index"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def calculate_macd(self, prices, fast=12, slow=26, signal=9):
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
    
    def calculate_bollinger_bands(self, prices, period=20, std_dev=2):
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
    
    def analyze(self, symbol, df):
        """
        Main analysis function to generate trading signals
        Returns: {'action': 'BUY'/'SELL'/'HOLD', 'confidence': 0-100, 'price': float}
        """
        try:
            # Calculate indicators
            df = self.calculate_indicators(df)
            
            # Get latest data
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            
            # Initialize signal
            signal = {
                'action': 'HOLD',
                'confidence': 0,
                'price': latest['close'],
                'timestamp': datetime.now(),
                'indicators': {}
            }
            
            # Check if we have enough data
            if pd.isna(latest['ma_slow']) or pd.isna(latest['rsi']):
                return signal
            
            # Store indicator values
            signal['indicators'] = {
                'ma_fast': latest['ma_fast'],
                'ma_slow': latest['ma_slow'],
                'rsi': latest['rsi'],
                'macd': latest['macd'],
                'macd_signal': latest['macd_signal'],
                'bb_upper': latest['bb_upper'],
                'bb_lower': latest['bb_lower'],
                'momentum': latest['momentum']
            }
            
            # Scoring system
            buy_score = 0
            sell_score = 0
            
            # 1. Moving Average Crossover (Weight: 30%)
            if latest['ma_fast'] > latest['ma_slow']:
                buy_score += 30
                if prev['ma_fast'] <= prev['ma_slow']:  # Just crossed
                    buy_score += 10
            else:
                sell_score += 30
                if prev['ma_fast'] >= prev['ma_slow']:  # Just crossed
                    sell_score += 10
            
            # 2. RSI Analysis (Weight: 25%)
            if latest['rsi'] < self.rsi_oversold:
                buy_score += 25
            elif latest['rsi'] > self.rsi_overbought:
                sell_score += 25
            elif latest['rsi'] < 50:
                buy_score += 10
            else:
                sell_score += 10
            
            # 3. MACD Analysis (Weight: 25%)
            if latest['macd'] > latest['macd_signal']:
                buy_score += 25
                if prev['macd'] <= prev['macd_signal']:  # Just crossed
                    buy_score += 10
            else:
                sell_score += 25
                if prev['macd'] >= prev['macd_signal']:  # Just crossed
                    sell_score += 10
            
            # 4. Bollinger Bands (Weight: 20%)
            if latest['close'] < latest['bb_lower']:
                buy_score += 20  # Oversold
            elif latest['close'] > latest['bb_upper']:
                sell_score += 20  # Overbought
            
            # 5. Momentum (Weight: 10%)
            if latest['momentum'] > 0:
                buy_score += 10
            else:
                sell_score += 10
            
            # Determine final signal
            min_confidence = self.strategy_config.get('min_confidence', 60)
            
            if buy_score > sell_score and buy_score >= min_confidence:
                signal['action'] = 'BUY'
                signal['confidence'] = buy_score
                signal['stop_loss'] = latest['close'] - (self.stop_loss_pips * 0.0001)
                signal['take_profit'] = latest['close'] + (self.take_profit_pips * 0.0001)
                
            elif sell_score > buy_score and sell_score >= min_confidence:
                signal['action'] = 'SELL'
                signal['confidence'] = sell_score
                signal['stop_loss'] = latest['close'] + (self.stop_loss_pips * 0.0001)
                signal['take_profit'] = latest['close'] - (self.take_profit_pips * 0.0001)
            
            return signal
            
        except Exception as e:
            logging.error(f"Error in strategy analysis for {symbol}: {str(e)}")
            return {
                'action': 'HOLD',
                'confidence': 0,
                'price': 0,
                'timestamp': datetime.now(),
                'indicators': {}
            }