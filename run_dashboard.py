"""
Run Flask Dashboard
Start this separately from main.py trading bot
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app

if __name__ == '__main__':
    print("="*70)
    print("🌐 MT5 TRADING DASHBOARD")
    print("="*70)
    print("\n📍 Dashboard URL: http://localhost:5000")
    print("📍 Dashboard URL (Network): http://0.0.0.0:5000")
    print("\n💡 Tips:")
    print("   - Open http://localhost:5000 in your browser")
    print("   - Make sure main.py is running to see live data")
    print("   - Press Ctrl+C to stop the dashboard")
    print("\n" + "="*70 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5000)