"""
Test News Collector Functionality
"""
import MetaTrader5 as mt5
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.news_collector import NewsCollector
from src.utils import setup_logging

def test_mt5_connection():
    """Test MT5 connection"""
    print("="*80)
    print("TEST 1: MT5 CONNECTION")
    print("="*80)
    
    if not mt5.initialize():
        print("❌ MT5 initialization failed")
        return False
    
    # Login
    account = 356000
    password = "nag#IS5R1"
    server = "FinexBisnisSolusi-Demo"
    
    if not mt5.login(account, password=password, server=server):
        print(f"❌ Login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False
    
    print("✓ MT5 connected successfully")
    
    account_info = mt5.account_info()
    if account_info:
        print(f"  Account: {account_info.login}")
        print(f"  Server: {account_info.server}")
        print(f"  Balance: ${account_info.balance:.2f}")
    
    print()
    return True

def test_news_database_init():
    """Test news database initialization"""
    print("="*80)
    print("TEST 2: NEWS DATABASE INITIALIZATION")
    print("="*80)
    
    try:
        nc = NewsCollector()
        print("✓ NewsCollector initialized")
        print(f"  Database path: {nc.db_path}")
        
        # Check if database exists
        if os.path.exists(nc.db_path):
            print("✓ Database file exists")
        else:
            print("❌ Database file not found")
            return False
        
        # Check if news table exists
        import sqlite3
        conn = sqlite3.connect(nc.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='news'")
        result = cursor.fetchone()
        conn.close()
        
        if result:
            print("✓ News table exists")
        else:
            print("❌ News table not found")
            return False
        
        print()
        return True
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        print()
        return False

def test_fetch_mt5_calendar():
    """Test fetching news from MT5 calendar"""
    print("="*80)
    print("TEST 3: FETCH MT5 ECONOMIC CALENDAR")
    print("="*80)
    
    try:
        nc = NewsCollector()
        
        print("Fetching news from MT5 calendar...")
        count = nc.update_news(days_ahead=7)
        
        if count > 0:
            print(f"✓ Successfully fetched {count} news items from MT5")
        else:
            print("⚠ No news items fetched (this might be normal if no events scheduled)")
        
        print()
        return True
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print()
        return False

def test_get_upcoming_news():
    """Test getting upcoming news"""
    print("="*80)
    print("TEST 4: GET UPCOMING NEWS")
    print("="*80)
    
    try:
        nc = NewsCollector()
        
        # Get upcoming high-impact news
        print("Getting upcoming HIGH impact news (next 48 hours)...")
        news = nc.get_upcoming_news(hours=48, impact='High')
        
        if news:
            print(f"✓ Found {len(news)} upcoming high-impact events")
            print("\nTop 5 upcoming events:")
            print("-"*80)
            for item in news[:5]:
                print(f"  {item['event_time']} | {item['currency']:3} | {item['title']}")
                if item['forecast']:
                    print(f"    Forecast: {item['forecast']} | Previous: {item['previous']}")
        else:
            print("⚠ No upcoming high-impact news found")
        
        print()
        
        # Get all upcoming news
        print("Getting ALL upcoming news (next 24 hours)...")
        all_news = nc.get_upcoming_news(hours=24)
        
        if all_news:
            print(f"✓ Found {len(all_news)} total upcoming events")
            
            # Count by impact
            high_count = sum(1 for n in all_news if n['impact'] == 'High')
            medium_count = sum(1 for n in all_news if n['impact'] == 'Medium')
            low_count = sum(1 for n in all_news if n['impact'] == 'Low')
            
            print(f"  High impact:   {high_count}")
            print(f"  Medium impact: {medium_count}")
            print(f"  Low impact:    {low_count}")
        else:
            print("⚠ No upcoming news found")
        
        print()
        return True
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print()
        return False

def test_get_recent_news():
    """Test getting recent news"""
    print("="*80)
    print("TEST 5: GET RECENT NEWS")
    print("="*80)
    
    try:
        nc = NewsCollector()
        
        # Get recent high-impact news
        print("Getting recent HIGH impact news (last 24 hours)...")
        news = nc.get_recent_news(hours=24, impact='High')
        
        if news:
            print(f"✓ Found {len(news)} recent high-impact events")
            print("\nRecent events:")
            print("-"*80)
            for item in news[:5]:
                print(f"  {item['event_time']} | {item['currency']:3} | {item['title']}")
                if item['actual']:
                    print(f"    Actual: {item['actual']} | Forecast: {item['forecast']} | Previous: {item['previous']}")
        else:
            print("⚠ No recent high-impact news found")
        
        print()
        return True
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print()
        return False

def test_database_content():
    """Test database content"""
    print("="*80)
    print("TEST 6: DATABASE CONTENT")
    print("="*80)
    
    try:
        import sqlite3
        
        db_path = 'data/database/trading_data.db'
        if not os.path.exists(db_path):
            print("❌ Database not found")
            return False
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Count total news
        cursor.execute("SELECT COUNT(*) FROM news")
        total_count = cursor.fetchone()[0]
        print(f"Total news items in database: {total_count}")
        
        # Count by impact
        cursor.execute("SELECT impact, COUNT(*) FROM news GROUP BY impact")
        impact_counts = cursor.fetchall()
        
        print("\nNews by impact level:")
        for impact, count in impact_counts:
            print(f"  {impact}: {count}")
        
        # Count by currency
        cursor.execute("SELECT currency, COUNT(*) FROM news GROUP BY currency ORDER BY COUNT(*) DESC LIMIT 10")
        currency_counts = cursor.fetchall()
        
        print("\nTop 10 currencies:")
        for currency, count in currency_counts:
            print(f"  {currency}: {count}")
        
        # Get date range
        cursor.execute("SELECT MIN(event_time), MAX(event_time) FROM news")
        min_date, max_date = cursor.fetchone()
        
        print(f"\nDate range:")
        print(f"  Earliest: {min_date}")
        print(f"  Latest:   {max_date}")
        
        conn.close()
        
        print()
        return True
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        print()
        return False

def main():
    """Run all tests"""
    print("\n")
    print("╔" + "="*78 + "╗")
    print("║" + " "*20 + "NEWS COLLECTOR TEST SUITE" + " "*33 + "║")
    print("╚" + "="*78 + "╝")
    print()
    
    # Setup logging
    setup_logging(log_level='WARNING')
    
    tests = [
        ("MT5 Connection", test_mt5_connection),
        ("Database Init", test_news_database_init),
        ("Fetch MT5 Calendar", test_fetch_mt5_calendar),
        ("Get Upcoming News", test_get_upcoming_news),
        ("Get Recent News", test_get_recent_news),
        ("Database Content", test_database_content),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"❌ CRITICAL ERROR in {name}: {str(e)}")
            results.append((name, False))
    
    # Summary
    print("="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status} | {name}")
    
    print("-"*80)
    print(f"Results: {passed}/{total} tests passed ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("\n🎉 All tests passed! News collector is working correctly.")
    else:
        print(f"\n⚠ {total - passed} test(s) failed. Please check the errors above.")
    
    print("="*80)
    print()
    
    # Cleanup
    mt5.shutdown()

if __name__ == "__main__":
    main()