import MetaTrader5 as mt5
import pandas as pd
import logging
from datetime import datetime
import os
import sqlite3

class DataCollector:
    """Collect and store real-time tick and OHLC data from MT5 — DB only"""
    
    def __init__(self, config):
        self.config = config
        self.data_dir = config.get('data_directory', 'data')
        
        # Hanya buat folder database
        os.makedirs(f"{self.data_dir}/database", exist_ok=True)
        
        self.db_path = f"{self.data_dir}/database/trading_data.db"
        self.init_database()
        
    def init_database(self):
        """Initialize SQLite database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
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
            
            latest = ohlc_data.iloc[-1]
            timestamp_str = latest['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            
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
        """Get current tick data dan simpan ke DB"""
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
            
            self.save_tick_to_db(tick_data)
            return tick_data
            
        except Exception as e:
            logging.error(f"Error getting tick data for {symbol}: {str(e)}")
            return None
    
    def save_tick_data(self, symbol, tick_data):
        """Wrapper untuk kompatibilitas dengan main.py — hanya simpan ke DB"""
        # CSV dihapus, langsung ke DB (sudah dilakukan di get_tick_data)
        pass

    def get_ohlc_data(self, symbol, timeframe=mt5.TIMEFRAME_M1, bars=100):
        """Get OHLC data dan simpan ke DB"""
        try:
            rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
            if rates is None or len(rates) == 0:
                logging.warning(f"Failed to get OHLC data for {symbol}")
                return None
            
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            df = df.rename(columns={
                'time': 'timestamp',
                'tick_volume': 'volume'
            })
            df['symbol'] = symbol
            
            self.save_ohlc_to_db(symbol, df)
            return df
            
        except Exception as e:
            logging.error(f"Error getting OHLC data for {symbol}: {str(e)}")
            return None
    
    def get_minute_data_from_db(self, symbol, minutes=1):
        """Get data dari DB untuk N menit terakhir"""
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