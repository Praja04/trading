"""
Reset Database - Hapus dan buat ulang database
"""
import os
import sqlite3

DB_PATH = "data/database/trading_data.db"

def reset_database():
    """Delete and recreate database"""
    print("="*60)
    print("🗑️  RESET DATABASE")
    print("="*60)
    
    # Delete old database
    if os.path.exists(DB_PATH):
        confirm = input(f"\n⚠️  Database ditemukan: {DB_PATH}\nHapus dan buat ulang? (yes/no): ")
        if confirm.lower() not in ['yes', 'y']:
            print("❌ Operasi dibatalkan")
            return
        
        os.remove(DB_PATH)
        print(f"✅ Database lama dihapus: {DB_PATH}")
    else:
        print("ℹ️  Database tidak ditemukan, membuat baru...")
    
    # Create new database
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Create ticks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
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
                timestamp TEXT,
                symbol TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                spread INTEGER
            )
        ''')
        
        # Create indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON ticks(symbol, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ohlc_symbol_time ON ohlc(symbol, timestamp)')
        
        conn.commit()
        conn.close()
        
        print(f"✅ Database baru dibuat: {DB_PATH}")
        print("\n📊 Struktur database:")
        print("  - Table: ticks (tick data)")
        print("  - Table: ohlc (candle data)")
        print("  - Indexes: symbol + timestamp")
        
    except Exception as e:
        print(f"❌ Error membuat database: {str(e)}")
        return
    
    print("\n" + "="*60)
    print("✅ RESET DATABASE SELESAI")
    print("="*60)
    print("\nAnda sekarang bisa menjalankan: python main.py")

if __name__ == "__main__":
    reset_database()