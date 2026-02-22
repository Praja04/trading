"""
news_collector.py
------------------
Ambil economic calendar dari Forex Factory (gratis, tanpa API key).
Interface sama dengan versi lama sehingga app.py & dashboard.py tidak perlu diubah.

Forex Factory menyediakan JSON publik:
  https://nfs.faireconomy.media/ff_calendar_thisweek.json
  https://nfs.faireconomy.media/ff_calendar_nextweek.json
"""

import sqlite3
import logging
import requests
import threading
import time
from datetime import datetime, timedelta

# ======================================================================
# FOREX FACTORY URLS
# ======================================================================
FF_THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Impact mapping dari Forex Factory ke label kita
IMPACT_MAP = {
    "High":   "High",
    "Medium": "Medium",
    "Low":    "Low",
    "Non-Economic": "Low",
    "Holiday": "Low",
}


# ======================================================================
# NewsCollector CLASS
# ======================================================================

class NewsCollector:
    
    def __init__(self, db_path="data/database/trading_data.db"):
        self.db_path = db_path
        self.init_news_table()

    # ------------------------------------------------------------------
    # DATABASE
    # ------------------------------------------------------------------

    def init_news_table(self):
        """Buat table news jika belum ada"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS news (
                    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
                    timestamp  DATETIME,
                    event_id   TEXT     UNIQUE,
                    title      TEXT,
                    country    TEXT,
                    currency   TEXT,
                    impact     TEXT,
                    forecast   TEXT,
                    previous   TEXT,
                    actual     TEXT,
                    event_time DATETIME,
                    source     TEXT DEFAULT 'ForexFactory'
                )
            ''')

            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_time       ON news(event_time)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_currency   ON news(currency)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_impact     ON news(impact)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_news_event_id   ON news(event_id)')

            conn.commit()
            conn.close()
            logging.info("News table initialized successfully")

        except Exception as e:
            logging.error(f"Error initializing news table: {str(e)}")

    def save_news_to_db(self, news_items):
        """Simpan list news ke DB, skip duplikat berdasarkan event_id"""
        if not news_items:
            return 0

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            saved = 0

            for item in news_items:
                try:
                    cursor.execute('''
                        INSERT OR REPLACE INTO news
                        (timestamp, event_id, title, country, currency, impact,
                         forecast, previous, actual, event_time, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        item['event_id'],
                        item['title'],
                        item['country'],
                        item['currency'],
                        item['impact'],
                        item.get('forecast', ''),
                        item.get('previous', ''),
                        item.get('actual', ''),
                        item['event_time'].strftime('%Y-%m-%d %H:%M:%S'),
                        'ForexFactory'
                    ))
                    if cursor.rowcount > 0:
                        saved += 1
                except Exception as e:
                    logging.error(f"Error saving news item: {str(e)}")
                    continue

            conn.commit()
            conn.close()
            return saved

        except Exception as e:
            logging.error(f"Error saving news to database: {str(e)}")
            return 0

    # ------------------------------------------------------------------
    # FETCH DARI FOREX FACTORY
    # ------------------------------------------------------------------

    def fetch_forex_factory(self, url):
        """Fetch dan parse JSON dari Forex Factory"""
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            logging.warning("Tidak bisa konek ke Forex Factory (no internet?)")
            return []
        except requests.exceptions.Timeout:
            logging.warning("Forex Factory request timeout")
            return []
        except Exception as e:
            logging.error(f"Error fetching Forex Factory: {str(e)}")
            return []

    def parse_forex_factory(self, raw_events):
        """
        Parse response JSON Forex Factory ke format standar kita.
        
        Format FF JSON per event:
        {
          "title": "CPI y/y",
          "country": "USD",
          "date": "01-15-2026",
          "time": "08:30am",
          "impact": "High",
          "forecast": "0.3%",
          "previous": "0.3%"
        }
        """
        parsed = []

        for i, event in enumerate(raw_events):
            try:
                # Parse tanggal & waktu
                date_str = event.get('date', '')
                time_str = event.get('time', '').strip()

                # Handle "Tentative", "All Day", kosong
                if not time_str or time_str.lower() in ('tentative', 'all day', ''):
                    time_str = '12:00am'

                try:
                    event_time = datetime.strptime(
                        f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p"
                    )
                except ValueError:
                    # Fallback jika format waktu aneh
                    event_time = datetime.strptime(date_str, "%m-%d-%Y")

                currency = event.get('country', '').upper()
                title    = event.get('title', 'Unknown Event')
                impact   = IMPACT_MAP.get(event.get('impact', 'Low'), 'Low')

                # Buat event_id unik dari tanggal + currency + title
                event_id = f"FF_{date_str}_{currency}_{title}".replace(' ', '_')[:100]

                parsed.append({
                    'event_id':   event_id,
                    'title':      title,
                    'country':    currency,
                    'currency':   currency,
                    'impact':     impact,
                    'forecast':   event.get('forecast', '') or '',
                    'previous':   event.get('previous', '') or '',
                    'actual':     event.get('actual', '')   or '',
                    'event_time': event_time,
                })

            except Exception as e:
                logging.debug(f"Skip event #{i}: {str(e)}")
                continue

        return parsed

    # ------------------------------------------------------------------
    # UPDATE (dipanggil dari luar)
    # ------------------------------------------------------------------

    def update_news(self, days_ahead=7):
        """
        Fetch news dari Forex Factory dan simpan ke DB.
        Selalu ambil minggu ini + minggu depan.
        Kompatibel dengan pemanggilan di main.py dan app.py.
        """
        all_news = []

        # Ambil minggu ini
        raw_this = self.fetch_forex_factory(FF_THIS_WEEK)
        if raw_this:
            all_news.extend(self.parse_forex_factory(raw_this))

        # Ambil minggu depan
        raw_next = self.fetch_forex_factory(FF_NEXT_WEEK)
        if raw_next:
            all_news.extend(self.parse_forex_factory(raw_next))

        if not all_news:
            logging.warning("No news fetched from Forex Factory")
            return 0

        saved = self.save_news_to_db(all_news)
        logging.info(f"Forex Factory: {len(all_news)} events fetched, {saved} baru disimpan")
        return saved

    def clean_old_news(self, days=30):
        """Hapus news lebih dari N hari yang lalu"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM news WHERE event_time < datetime('now', ? )",
                (f'-{days} days',)
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted > 0:
                logging.info(f"Cleaned {deleted} old news items")
            return deleted
        except Exception as e:
            logging.error(f"Error cleaning old news: {str(e)}")
            return 0

    # ------------------------------------------------------------------
    # QUERY (dipanggil dari app.py & dashboard)
    # ------------------------------------------------------------------

    def _query_news(self, where_clause, params=()):
        """Helper query ke table news"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(f'''
                SELECT id, timestamp, event_id, title, country, currency,
                       impact, forecast, previous, actual, event_time, source
                FROM news
                WHERE {where_clause}
                ORDER BY event_time ASC
            ''', params)
            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    'id': r[0], 'timestamp': r[1], 'event_id': r[2],
                    'title': r[3], 'country': r[4], 'currency': r[5],
                    'impact': r[6], 'forecast': r[7], 'previous': r[8],
                    'actual': r[9], 'event_time': r[10], 'source': r[11]
                }
                for r in rows
            ]
        except Exception as e:
            logging.error(f"Error querying news: {str(e)}")
            return []

    def get_recent_news(self, hours=24, impact=None):
        """News yang sudah terjadi dalam N jam terakhir"""
        where = f"event_time >= datetime('now', '-{hours} hours') AND event_time <= datetime('now')"
        if impact:
            where += f" AND impact = '{impact}'"
        return self._query_news(where)

    def get_upcoming_news(self, hours=48, impact=None):
        """News yang akan datang dalam N jam ke depan"""
        where = f"event_time >= datetime('now') AND event_time <= datetime('now', '+{hours} hours')"
        if impact:
            where += f" AND impact = '{impact}'"
        return self._query_news(where)

    def get_high_impact_news_today(self):
        """High impact news hari ini"""
        return self._query_news(
            "date(event_time) = date('now') AND impact = 'High'"
        )


# ======================================================================
# BACKGROUND UPDATER
# ======================================================================

def start_news_updater(interval_minutes=30):
    """
    Jalankan background thread yang update news dari Forex Factory
    setiap N menit. Kompatibel dengan pemanggilan di main.py & app.py.
    """
    def updater():
        collector = NewsCollector()

        # Update pertama saat start
        try:
            count = collector.update_news()
            if count > 0:
                logging.info(f"Initial news fetch: {count} items saved")
        except Exception as e:
            logging.error(f"Initial news fetch failed: {str(e)}")

        while True:
            time.sleep(interval_minutes * 60)
            try:
                count = collector.update_news()
                if count > 0:
                    logging.info(f"News updated: {count} new items")
                else:
                    logging.info("News update: no new items")

                # Bersihkan berita lama sekali per hari (peluang 1/48 per run)
                import random
                if random.random() < (1 / 48):
                    collector.clean_old_news(days=30)

            except Exception as e:
                logging.error(f"Error in news updater loop: {str(e)}")

    thread = threading.Thread(target=updater, daemon=True)
    thread.start()
    logging.info(f"News updater started (Forex Factory, interval: {interval_minutes} min)")
    return thread