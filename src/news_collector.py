import MetaTrader5 as mt5
import sqlite3
import logging
from datetime import datetime, timedelta
import time

class NewsCollector:
    """Collect and store forex news from MT5 built-in Economic Calendar"""
    
    def __init__(self, db_path="data/database/trading_data.db"):
        self.db_path = db_path
        self.init_news_table()
        
    def init_news_table(self):
        """Initialize news table in database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    event_id INTEGER UNIQUE,
                    title TEXT,
                    country TEXT,
                    currency TEXT,
                    impact TEXT,
                    forecast TEXT,
                    previous TEXT,
                    actual TEXT,
                    event_time DATETIME,
                    source TEXT DEFAULT 'MT5'
                )
            ''')
            
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_time ON news(event_time)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_currency ON news(currency)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_impact ON news(impact)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_event_id ON news(event_id)')
            
            conn.commit()
            conn.close()
            logging.info("News table initialized successfully")
            
        except Exception as e:
            logging.error(f"Error initializing news table: {str(e)}")
    
    def fetch_mt5_calendar(self, date_from=None, date_to=None):
        """Fetch economic calendar from MT5"""
        try:
            # Set default date range if not provided
            if date_from is None:
                date_from = datetime.now()
            if date_to is None:
                date_to = datetime.now() + timedelta(days=7)
            
            # Get calendar from MT5
            calendar = mt5.calendar_all(date_from, date_to)
            
            if calendar is None:
                logging.warning("MT5 calendar returned None")
                return []
            
            logging.info(f"Fetched {len(calendar)} calendar events from MT5")
            return self.parse_mt5_calendar(calendar)
            
        except Exception as e:
            logging.error(f"Error fetching MT5 calendar: {str(e)}")
            return []
    
    def parse_mt5_calendar(self, calendar):
        """Parse MT5 calendar data"""
        parsed_news = []
        
        try:
            for event in calendar:
                # Get event details
                event_id = event.id
                time_value = datetime.fromtimestamp(event.time)
                
                # Get country info
                country_info = mt5.calendar_country_by_id(event.country_id)
                country_code = country_info.code if country_info else ""
                country_name = country_info.name if country_info else ""
                currency = country_info.currency if country_info else ""
                
                # Get event info
                event_info = mt5.calendar_event_by_id(event.event_id)
                event_name = event_info.name if event_info else "Unknown Event"
                event_type = event_info.type if event_info else 0
                
                # Determine impact level based on importance
                importance = event_info.importance if event_info else 0
                if importance == 3:
                    impact = "High"
                elif importance == 2:
                    impact = "Medium"
                else:
                    impact = "Low"
                
                # Get forecast, actual, and previous values
                forecast_value = str(event.forecast_value) if hasattr(event, 'forecast_value') and event.forecast_value else ""
                actual_value = str(event.actual_value) if hasattr(event, 'actual_value') and event.actual_value else ""
                prev_value = str(event.prev_value) if hasattr(event, 'prev_value') and event.prev_value else ""
                
                news_item = {
                    'timestamp': datetime.now(),
                    'event_id': event_id,
                    'title': event_name,
                    'country': country_code,
                    'currency': currency,
                    'impact': impact,
                    'forecast': forecast_value,
                    'previous': prev_value,
                    'actual': actual_value,
                    'event_time': time_value,
                    'source': 'MT5'
                }
                
                parsed_news.append(news_item)
                
        except Exception as e:
            logging.error(f"Error parsing calendar event: {str(e)}")
        
        return parsed_news
    
    def save_news_to_db(self, news_items):
        """Save news items to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            saved_count = 0
            for item in news_items:
                try:
                    cursor.execute('''
                        INSERT OR REPLACE INTO news 
                        (timestamp, event_id, title, country, currency, impact, 
                         forecast, previous, actual, event_time, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        item['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                        item['event_id'],
                        item['title'],
                        item['country'],
                        item['currency'],
                        item['impact'],
                        item['forecast'],
                        item['previous'],
                        item['actual'],
                        item['event_time'].strftime('%Y-%m-%d %H:%M:%S'),
                        item['source']
                    ))
                    
                    if cursor.rowcount > 0:
                        saved_count += 1
                        
                except Exception as e:
                    logging.error(f"Error saving news item: {str(e)}")
                    continue
            
            conn.commit()
            conn.close()
            
            if saved_count > 0:
                logging.info(f"Saved {saved_count} news items to database")
            
            return saved_count
            
        except Exception as e:
            logging.error(f"Error saving news to database: {str(e)}")
            return 0
    
    def get_recent_news(self, hours=24, impact=None):
        """Get recent news from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = '''
                SELECT id, timestamp, event_id, title, country, currency, impact, 
                       forecast, previous, actual, event_time, source
                FROM news
                WHERE event_time >= datetime('now', '-{} hours')
                AND event_time <= datetime('now')
            '''.format(hours)
            
            if impact:
                query += f" AND impact = '{impact}'"
            
            query += " ORDER BY event_time DESC LIMIT 100"
            
            cursor = conn.cursor()
            cursor.execute(query)
            
            news_list = []
            for row in cursor.fetchall():
                news_list.append({
                    'id': row[0],
                    'timestamp': row[1],
                    'event_id': row[2],
                    'title': row[3],
                    'country': row[4],
                    'currency': row[5],
                    'impact': row[6],
                    'forecast': row[7],
                    'previous': row[8],
                    'actual': row[9],
                    'event_time': row[10],
                    'source': row[11]
                })
            
            conn.close()
            return news_list
            
        except Exception as e:
            logging.error(f"Error getting recent news: {str(e)}")
            return []
    
    def get_upcoming_news(self, hours=48, impact=None):
        """Get upcoming news from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = '''
                SELECT id, timestamp, event_id, title, country, currency, impact, 
                       forecast, previous, actual, event_time, source
                FROM news
                WHERE event_time >= datetime('now')
                AND event_time <= datetime('now', '+{} hours')
            '''.format(hours)
            
            if impact:
                query += f" AND impact = '{impact}'"
            
            query += " ORDER BY event_time ASC LIMIT 50"
            
            cursor = conn.cursor()
            cursor.execute(query)
            
            news_list = []
            for row in cursor.fetchall():
                news_list.append({
                    'id': row[0],
                    'timestamp': row[1],
                    'event_id': row[2],
                    'title': row[3],
                    'country': row[4],
                    'currency': row[5],
                    'impact': row[6],
                    'forecast': row[7],
                    'previous': row[8],
                    'actual': row[9],
                    'event_time': row[10],
                    'source': row[11]
                })
            
            conn.close()
            return news_list
            
        except Exception as e:
            logging.error(f"Error getting upcoming news: {str(e)}")
            return []
    
    def update_news(self, days_ahead=7):
        """Fetch and update news from MT5 calendar"""
        if not mt5.initialize():
            logging.error("MT5 not initialized for news update")
            return 0
        
        try:
            # Fetch calendar for next N days
            date_from = datetime.now() - timedelta(days=1)  # Include yesterday for recent news
            date_to = datetime.now() + timedelta(days=days_ahead)
            
            news = self.fetch_mt5_calendar(date_from, date_to)
            
            if news:
                saved = self.save_news_to_db(news)
                logging.info(f"MT5 Calendar: Updated {saved} news items")
                return saved
            else:
                logging.warning("No news fetched from MT5 calendar")
                return 0
                
        except Exception as e:
            logging.error(f"Error updating news from MT5: {str(e)}")
            return 0
    
    def clean_old_news(self, days=30):
        """Remove news older than specified days"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM news
                WHERE event_time < datetime('now', '-{} days')
            '''.format(days))
            
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            
            if deleted > 0:
                logging.info(f"Cleaned {deleted} old news items")
            
            return deleted
            
        except Exception as e:
            logging.error(f"Error cleaning old news: {str(e)}")
            return 0
    
    def get_high_impact_news_today(self):
        """Get high impact news for today"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = '''
                SELECT title, currency, event_time, forecast, previous, actual
                FROM news
                WHERE date(event_time) = date('now')
                AND impact = 'High'
                ORDER BY event_time ASC
            '''
            
            cursor = conn.cursor()
            cursor.execute(query)
            
            news_list = []
            for row in cursor.fetchall():
                news_list.append({
                    'title': row[0],
                    'currency': row[1],
                    'event_time': row[2],
                    'forecast': row[3],
                    'previous': row[4],
                    'actual': row[5]
                })
            
            conn.close()
            return news_list
            
        except Exception as e:
            logging.error(f"Error getting high impact news: {str(e)}")
            return []

# Background news updater
def start_news_updater(interval_minutes=30):
    """Start background news updater from MT5 calendar"""
    import threading
    
    def updater():
        news_collector = NewsCollector()
        
        while True:
            try:
                # Check if MT5 is initialized
                if not mt5.terminal_info():
                    logging.warning("MT5 not connected, skipping news update")
                    time.sleep(interval_minutes * 60)
                    continue
                
                logging.info("Updating forex news from MT5 calendar...")
                count = news_collector.update_news(days_ahead=7)
                
                if count > 0:
                    logging.info(f"Updated {count} news items from MT5")
                else:
                    logging.info("No new news items from MT5")
                
                # Clean old news once per day (10% chance each run)
                import random
                if random.random() < 0.1:
                    news_collector.clean_old_news(days=30)
                
            except Exception as e:
                logging.error(f"Error in news updater: {str(e)}")
            
            time.sleep(interval_minutes * 60)
    
    thread = threading.Thread(target=updater, daemon=True)
    thread.start()
    logging.info(f"MT5 News updater started (interval: {interval_minutes} minutes)")
    
    return thread

# Helper function to display news
def print_upcoming_news(hours=24):
    """Print upcoming high impact news"""
    nc = NewsCollector()
    news = nc.get_upcoming_news(hours=hours, impact='High')
    
    print("\n" + "="*80)
    print(f"HIGH IMPACT NEWS - Next {hours} Hours")
    print("="*80)
    
    if not news:
        print("No high impact news scheduled")
    else:
        for item in news:
            print(f"\n{item['event_time']} | {item['currency']}")
            print(f"  {item['title']}")
            if item['forecast']:
                print(f"  Forecast: {item['forecast']} | Previous: {item['previous']}")
    
    print("="*80 + "\n")