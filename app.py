from flask import Flask, render_template, jsonify, request
import MetaTrader5 as mt5
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import json

app = Flask(__name__)

# MT5 Configuration
MT5_ACCOUNT = 356000
MT5_PASSWORD = "nag#IS5R1"
MT5_SERVER = "FinexBisnisSolusi-Demo"
DB_PATH = "data/database/trading_data.db"

def init_mt5():
    """Initialize MT5 connection"""
    if not mt5.initialize():
        return False
    if not mt5.login(MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER):
        return False
    return True

def get_account_info():
    """Get current account information"""
    if not init_mt5():
        return None
    
    account = mt5.account_info()
    if account is None:
        return None
    
    return {
        'balance': account.balance,
        'equity': account.equity,
        'margin': account.margin,
        'free_margin': account.margin_free,
        'margin_level': account.margin_level if account.margin > 0 else 0,
        'profit': account.profit,
        'leverage': account.leverage
    }

def get_open_positions():
    """Get all open positions"""
    if not init_mt5():
        return []
    
    positions = mt5.positions_get()
    if positions is None:
        return []
    
    position_list = []
    for pos in positions:
        position_list.append({
            'ticket': pos.ticket,
            'symbol': pos.symbol,
            'type': 'BUY' if pos.type == 0 else 'SELL',
            'volume': pos.volume,
            'open_price': pos.price_open,
            'current_price': pos.price_current,
            'sl': pos.sl,
            'tp': pos.tp,
            'profit': pos.profit,
            'open_time': datetime.fromtimestamp(pos.time).strftime('%Y-%m-%d %H:%M:%S'),
            'comment': pos.comment
        })
    
    return position_list

def get_closed_trades_today():
    """Get closed trades for today"""
    if not init_mt5():
        return []
    
    from_date = datetime.now().replace(hour=0, minute=0, second=0)
    to_date = datetime.now()
    
    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None:
        return []
    
    trades = []
    for deal in deals:
        if deal.entry == 1:  # Closing deal
            trades.append({
                'ticket': deal.ticket,
                'symbol': deal.symbol,
                'type': 'BUY' if deal.type == 0 else 'SELL',
                'volume': deal.volume,
                'price': deal.price,
                'profit': deal.profit,
                'time': datetime.fromtimestamp(deal.time).strftime('%Y-%m-%d %H:%M:%S')
            })
    
    return trades

def get_recent_ticks(symbol, limit=50):
    """Get recent tick data from database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        query = f'''
            SELECT timestamp, bid, ask, spread, volume 
            FROM ticks 
            WHERE symbol = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        '''
        df = pd.read_sql_query(query, conn, params=(symbol, limit))
        conn.close()
        
        return df.to_dict('records')
    except Exception as e:
        print(f"Error getting ticks: {e}")
        return []

def get_recent_ohlc(symbol, limit=50):
    """Get recent OHLC data from database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        query = f'''
            SELECT timestamp, open, high, low, close, volume 
            FROM ohlc 
            WHERE symbol = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        '''
        df = pd.read_sql_query(query, conn, params=(symbol, limit))
        conn.close()
        
        return df.to_dict('records')
    except Exception as e:
        print(f"Error getting OHLC: {e}")
        return []

def calculate_stats():
    """Calculate trading statistics"""
    trades = get_closed_trades_today()
    
    if not trades:
        return {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0,
            'total_profit': 0,
            'total_loss': 0,
            'net_profit': 0
        }
    
    winning = [t for t in trades if t['profit'] > 0]
    losing = [t for t in trades if t['profit'] < 0]
    
    total_profit = sum(t['profit'] for t in winning)
    total_loss = sum(t['profit'] for t in losing)
    
    return {
        'total_trades': len(trades),
        'winning_trades': len(winning),
        'losing_trades': len(losing),
        'win_rate': (len(winning) / len(trades) * 100) if trades else 0,
        'total_profit': total_profit,
        'total_loss': total_loss,
        'net_profit': total_profit + total_loss
    }

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/account')
def api_account():
    """API endpoint for account info"""
    account = get_account_info()
    return jsonify(account if account else {})

@app.route('/api/positions')
def api_positions():
    """API endpoint for open positions"""
    positions = get_open_positions()
    return jsonify(positions)

@app.route('/api/trades')
def api_trades():
    """API endpoint for closed trades"""
    trades = get_closed_trades_today()
    return jsonify(trades)

@app.route('/api/stats')
def api_stats():
    """API endpoint for trading statistics"""
    stats = calculate_stats()
    return jsonify(stats)

@app.route('/api/ticks/<symbol>')
def api_ticks(symbol):
    """API endpoint for tick data"""
    limit = request.args.get('limit', 100, type=int)
    ticks = get_recent_ticks(symbol, limit)
    return jsonify(ticks)

@app.route('/api/ohlc/<symbol>')
def api_ohlc(symbol):
    """API endpoint for OHLC data"""
    limit = request.args.get('limit', 100, type=int)
    ohlc = get_recent_ohlc(symbol, limit)
    return jsonify(ohlc)

if __name__ == '__main__':
    print("="*60)
    print("Starting Flask Dashboard on http://localhost:5000")
    print("="*60)
    app.run(debug=True, host='0.0.0.0', port=5000)