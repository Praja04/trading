import MetaTrader5 as mt5
import pandas as pd
import logging
from datetime import datetime
import os
import sqlite3

class DataCollector:
    """Collect and store real-time tick and OHLC data from MT5"""
    
    def __init__(self, config):
        self.config = config
        self.data_dir = config.get('data_directory', 'data')
        self.save_to_csv = config.get('save_to_csv', True)
        
        # Create data directory if not exists
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(f"{self.data_dir}/ticks", exist_ok=True)
        os.makedirs(f"{self.data_dir}/ohlc", exist_ok=True)
        os.makedirs(f"{self.data_dir}/database", exist_ok=True)
        
        # Initialize database
        self.db_path = f"{self.data_dir}/database/trading_data.db"
        self.init_database()
        
    def init_database(self):
        """Initialize SQLite database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create ticks table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    symbol TEXT,
                    bid REAL,
                    ask REAL,
                    spread REAL,
                    volume INTEGER,
                    time_msc INTEGER
                )
            ''')
            
            # Create OHLC table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ohlc (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    symbol TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    spread INTEGER
                )
            ''')
            
            # Create index for faster queries
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON ticks(symbol, timestamp)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_ohlc_symbol_time ON ohlc(symbol, timestamp)')
            
            conn.commit()
            conn.close()
            logging.info("Database initialized successfully")
            
        except Exception as e:
            logging.error(f"Error initializing database: {str(e)}")
    
    def save_tick_to_db(self, tick_data):
        """Save tick data to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Convert datetime to string format
            timestamp_str = tick_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S.%f')
            
            cursor.execute('''
                INSERT INTO ticks (timestamp, symbol, bid, ask, spread, volume, time_msc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                timestamp_str,
                tick_data['symbol'],
                float(tick_data['bid']),
                float(tick_data['ask']),
                float(tick_data['spread']),
                int(tick_data['volume']),
                int(tick_data['time_msc'])
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logging.error(f"Error saving tick to database: {str(e)}")
    
    def save_ohlc_to_db(self, symbol, ohlc_data):
        """Save OHLC data to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get the latest candle
            latest = ohlc_data.iloc[-1]
            
            # Convert timestamp to string format for SQLite
            timestamp_str = latest['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Check if this timestamp already exists
            cursor.execute('''
                SELECT id FROM ohlc WHERE symbol = ? AND timestamp = ?
            ''', (symbol, timestamp_str))
            
            if cursor.fetchone() is None:
                cursor.execute('''
                    INSERT INTO ohlc (timestamp, symbol, open, high, low, close, volume, spread)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp_str,
                    symbol,
                    float(latest['open']),
                    float(latest['high']),
                    float(latest['low']),
                    float(latest['close']),
                    int(latest['volume']),
                    int(latest['spread'])
                ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logging.error(f"Error saving OHLC to database: {str(e)}")
    
    def get_tick_data(self, symbol):
        """Get current tick data: bid, ask, spread, volume"""
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                logging.warning(f"Failed to get tick for {symbol}")
                return None
            
            tick_data = {
                'timestamp': datetime.fromtimestamp(tick.time),
                'symbol': symbol,
                'bid': tick.bid,
                'ask': tick.ask,
                'spread': tick.ask - tick.bid,
                'volume': tick.volume,
                'time_msc': tick.time_msc
            }
            
            # Save to database
            self.save_tick_to_db(tick_data)
            
            return tick_data
            
        except Exception as e:
            logging.error(f"Error getting tick data for {symbol}: {str(e)}")
            return None
    
    def get_ohlc_data(self, symbol, timeframe=mt5.TIMEFRAME_M1, bars=100):
        """Get OHLC data (Open, High, Low, Close) for analysis"""
        try:
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
            if rates is None or len(rates) == 0:
                logging.warning(f"Failed to get OHLC data for {symbol}")
                return None
            
            # Convert to DataFrame
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            
            # Rename columns for clarity
            df = df.rename(columns={
                'time': 'timestamp',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'tick_volume': 'volume',
                'spread': 'spread'
            })
            
            df['symbol'] = symbol
            
            # Save to database
            self.save_ohlc_to_db(symbol, df)
            
            return df
            
        except Exception as e:
            logging.error(f"Error getting OHLC data for {symbol}: {str(e)}")
            return None
    
    def save_tick_data(self, symbol, tick_data):
        """Save tick data to CSV file"""
        if not self.save_to_csv or tick_data is None:
            return
        
        try:
            today = datetime.now().strftime('%Y%m%d')
            filename = f"{self.data_dir}/ticks/{symbol}_{today}_ticks.csv"
            
            # Convert to DataFrame
            df = pd.DataFrame([tick_data])
            
            # Append to CSV
            if os.path.exists(filename):
                df.to_csv(filename, mode='a', header=False, index=False)
            else:
                df.to_csv(filename, index=False)
                
        except Exception as e:
            logging.error(f"Error saving tick data for {symbol}: {str(e)}")
    
    def save_ohlc_data(self, symbol, ohlc_df):
        """Save OHLC data to CSV file"""
        if not self.save_to_csv or ohlc_df is None:
            return
        
        try:
            today = datetime.now().strftime('%Y%m%d')
            filename = f"{self.data_dir}/ohlc/{symbol}_{today}_ohlc.csv"
            
            # Save only the latest candle
            latest_candle = ohlc_df.iloc[-1:].copy()
            
            # Append to CSV
            if os.path.exists(filename):
                # Read existing data to avoid duplicates
                existing_df = pd.read_csv(filename)
                existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'])
                latest_time = latest_candle['timestamp'].iloc[0]
                
                # Only append if this timestamp doesn't exist
                if latest_time not in existing_df['timestamp'].values:
                    latest_candle.to_csv(filename, mode='a', header=False, index=False)
            else:
                latest_candle.to_csv(filename, index=False)
                
        except Exception as e:
            logging.error(f"Error saving OHLC data for {symbol}: {str(e)}")
    
    def get_minute_data_from_db(self, symbol, minutes=1):
        """Get data from database for the last N minutes"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = f'''
                SELECT * FROM ticks 
                WHERE symbol = ? 
                AND timestamp >= datetime('now', '-{minutes} minutes')
                ORDER BY timestamp DESC
            '''
            
            df = pd.read_sql_query(query, conn, params=(symbol,))
            conn.close()
            
            return df
            
        except Exception as e:
            logging.error(f"Error getting minute data from database: {str(e)}")
            return pd.DataFrame()
    
    def get_historical_data(self, symbol, days=7):
        """Get historical data for backtesting or analysis"""
        try:
            from_date = datetime.now()
            rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M1, from_date, days * 1440)
            
            if rates is None or len(rates) == 0:
                return None
            
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df['symbol'] = symbol
            
            return df
            
        except Exception as e:
            logging.error(f"Error getting historical data for {symbol}: {str(e)}")
            return None