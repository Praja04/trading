"""
View Database Contents - Lihat isi database
"""
import sqlite3
import pandas as pd
from datetime import datetime

DB_PATH = "data/database/trading_data.db"

def view_database():
    """View database contents"""
    try:
        conn = sqlite3.connect(DB_PATH)
        
        print("="*80)
        print("📊 DATABASE CONTENTS")
        print("="*80)
        
        # Get ticks count
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ticks")
        ticks_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM ohlc")
        ohlc_count = cursor.fetchone()[0]
        
        print(f"\n📈 Total Ticks: {ticks_count:,}")
        print(f"📊 Total OHLC: {ohlc_count:,}")
        
        # Get ticks by symbol
        print("\n" + "-"*80)
        print("TICKS BY SYMBOL:")
        print("-"*80)
        
        cursor.execute("""
            SELECT symbol, COUNT(*) as count, 
                   MIN(timestamp) as first_tick, 
                   MAX(timestamp) as last_tick
            FROM ticks 
            GROUP BY symbol
            ORDER BY symbol
        """)
        
        for row in cursor.fetchall():
            symbol, count, first, last = row
            print(f"{symbol:15} | Count: {count:6,} | First: {first} | Last: {last}")
        
        # Get OHLC by symbol
        print("\n" + "-"*80)
        print("OHLC BY SYMBOL:")
        print("-"*80)
        
        cursor.execute("""
            SELECT symbol, COUNT(*) as count,
                   MIN(timestamp) as first_candle,
                   MAX(timestamp) as last_candle
            FROM ohlc
            GROUP BY symbol
            ORDER BY symbol
        """)
        
        for row in cursor.fetchall():
            symbol, count, first, last = row
            print(f"{symbol:15} | Count: {count:6,} | First: {first} | Last: {last}")
        
        # Show recent ticks
        print("\n" + "-"*80)
        print("RECENT TICKS (Last 10):")
        print("-"*80)
        
        df_ticks = pd.read_sql_query("""
            SELECT timestamp, symbol, bid, ask, spread, volume
            FROM ticks
            ORDER BY timestamp DESC
            LIMIT 10
        """, conn)
        
        if not df_ticks.empty:
            print(df_ticks.to_string(index=False))
        else:
            print("No ticks data")
        
        # Show recent OHLC
        print("\n" + "-"*80)
        print("RECENT OHLC (Last 10):")
        print("-"*80)
        
        df_ohlc = pd.read_sql_query("""
            SELECT timestamp, symbol, open, high, low, close, volume
            FROM ohlc
            ORDER BY timestamp DESC
            LIMIT 10
        """, conn)
        
        if not df_ohlc.empty:
            print(df_ohlc.to_string(index=False))
        else:
            print("No OHLC data")
        
        conn.close()
        
        print("\n" + "="*80)
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")

def export_to_csv():
    """Export database to CSV"""
    try:
        conn = sqlite3.connect(DB_PATH)
        
        # Export ticks
        df_ticks = pd.read_sql_query("SELECT * FROM ticks ORDER BY timestamp DESC", conn)
        if not df_ticks.empty:
            filename = f"database_ticks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            df_ticks.to_csv(filename, index=False)
            print(f"✅ Ticks exported: {filename}")
        
        # Export OHLC
        df_ohlc = pd.read_sql_query("SELECT * FROM ohlc ORDER BY timestamp DESC", conn)
        if not df_ohlc.empty:
            filename = f"database_ohlc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            df_ohlc.to_csv(filename, index=False)
            print(f"✅ OHLC exported: {filename}")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        export_to_csv()
    else:
        view_database()
        
    print("\n💡 Tip: Jalankan 'python view_database.py export' untuk export ke CSV")