from flask import Flask, render_template, jsonify, request, send_from_directory
import MetaTrader5 as mt5
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import json
import os
from werkzeug.utils import secure_filename
import threading
import time

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = 'config/strategies'
ALLOWED_EXTENSIONS = {'json'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Create upload folder if not exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# MT5 Configuration
MT5_ACCOUNT = 356000
MT5_PASSWORD = "nag#IS5R1"
MT5_SERVER = "FinexBisnisSolusi-Demo"
DB_PATH = "data/database/trading_data.db"

# Global variables for real-time data
realtime_tick_data = {}
symbols_to_track = ["EURUSD.s", "GBPUSD.s", "USDJPY.s"]

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
        'balance': float(account.balance),
        'equity': float(account.equity),
        'margin': float(account.margin),
        'free_margin': float(account.margin_free),
        'margin_level': float(account.margin_level) if account.margin > 0 else 0,
        'profit': float(account.profit),
        'leverage': int(account.leverage)
    }

def calculate_drawdown():
    """Calculate current drawdown"""
    if not init_mt5():
        return {'current_drawdown': 0.0, 'max_drawdown': 0.0, 'drawdown_percent': 0.0}
    
    account = mt5.account_info()
    if not account:
        return {'current_drawdown': 0.0, 'max_drawdown': 0.0, 'drawdown_percent': 0.0}
    
    # Get historical equity data from database
    try:
        conn = sqlite3.connect(DB_PATH)
        query = '''
            SELECT MAX(equity) as max_equity 
            FROM (
                SELECT timestamp, 
                       (SELECT balance FROM account_history WHERE timestamp <= t.timestamp ORDER BY timestamp DESC LIMIT 1) + 
                       COALESCE((SELECT SUM(profit) FROM ticks WHERE timestamp <= t.timestamp), 0) as equity
                FROM ticks t
                WHERE timestamp >= datetime('now', '-7 days')
            )
        '''
        
        # Simple calculation based on current data
        current_equity = float(account.equity)
        balance = float(account.balance)
        
        # Current drawdown is the difference between balance and equity if negative
        current_drawdown = float(max(0, balance - current_equity))
        drawdown_percent = float((current_drawdown / balance * 100)) if balance > 0 else 0.0
        
        # For max drawdown, we'll use a simple heuristic
        # In production, you'd track this over time
        max_drawdown = float(current_drawdown)  # Simplified
        
        conn.close()
        
        return {
            'current_drawdown': float(current_drawdown),
            'max_drawdown': float(max_drawdown),
            'drawdown_percent': float(drawdown_percent)
        }
    except Exception as e:
        print(f"Error calculating drawdown: {e}")
        return {'current_drawdown': 0.0, 'max_drawdown': 0.0, 'drawdown_percent': 0.0}

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
            'ticket': int(pos.ticket),
            'symbol': str(pos.symbol),
            'type': 'BUY' if pos.type == 0 else 'SELL',
            'volume': float(pos.volume),
            'open_price': float(pos.price_open),
            'current_price': float(pos.price_current),
            'sl': float(pos.sl),
            'tp': float(pos.tp),
            'profit': float(pos.profit),
            'open_time': datetime.fromtimestamp(pos.time).strftime('%Y-%m-%d %H:%M:%S'),
            'comment': str(pos.comment),
            'swap': float(pos.swap),
            'magic': int(pos.magic)
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
                'ticket': int(deal.ticket),
                'symbol': str(deal.symbol),
                'type': 'BUY' if deal.type == 0 else 'SELL',
                'volume': float(deal.volume),
                'price': float(deal.price),
                'profit': float(deal.profit),
                'time': datetime.fromtimestamp(deal.time).strftime('%Y-%m-%d %H:%M:%S'),
                'commission': float(deal.commission),
                'swap': float(deal.swap)
            })
    
    return trades

def get_realtime_tick(symbol):
    """Get real-time tick data for a symbol"""
    if not init_mt5():
        return None
    
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    
    return {
        'symbol': symbol,
        'bid': float(tick.bid),
        'ask': float(tick.ask),
        'spread': float(tick.ask - tick.bid),
        'volume': int(tick.volume),
        'time': datetime.fromtimestamp(tick.time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
        'time_msc': int(tick.time_msc)
    }

def get_realtime_ohlc(symbol):
    """Get current candle OHLC data"""
    if not init_mt5():
        return None
    
    # Get the latest candle
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 1)
    if rates is None or len(rates) == 0:
        return None
    
    candle = rates[0]
    
    return {
        'symbol': symbol,
        'open': float(candle['open']),
        'high': float(candle['high']),
        'low': float(candle['low']),
        'close': float(candle['close']),
        'volume': int(candle['tick_volume']),
        'time': datetime.fromtimestamp(candle['time']).strftime('%Y-%m-%d %H:%M:%S')
    }

def get_all_symbols_realtime():
    """Get real-time data for all tracked symbols"""
    data = {}
    
    for symbol in symbols_to_track:
        tick_data = get_realtime_tick(symbol)
        ohlc_data = get_realtime_ohlc(symbol)
        
        if tick_data and ohlc_data:
            data[symbol] = {
                'tick': tick_data,
                'ohlc': ohlc_data
            }
    
    return data

def get_recent_ticks(symbol, limit=50):
    """Get recent tick data from database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        query = '''
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
        query = '''
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
            'win_rate': 0.0,
            'total_profit': 0.0,
            'total_loss': 0.0,
            'net_profit': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'profit_factor': 0.0
        }
    
    winning = [t for t in trades if t['profit'] > 0]
    losing = [t for t in trades if t['profit'] < 0]
    
    total_profit = float(sum(t['profit'] for t in winning))
    total_loss = float(abs(sum(t['profit'] for t in losing)))
    
    avg_win = float(total_profit / len(winning)) if winning else 0.0
    avg_loss = float(total_loss / len(losing)) if losing else 0.0
    profit_factor = float(total_profit / total_loss) if total_loss > 0 else 0.0
    
    return {
        'total_trades': int(len(trades)),
        'winning_trades': int(len(winning)),
        'losing_trades': int(len(losing)),
        'win_rate': float((len(winning) / len(trades) * 100)) if trades else 0.0,
        'total_profit': float(total_profit),
        'total_loss': float(total_loss),
        'net_profit': float(total_profit - total_loss),
        'avg_win': float(avg_win),
        'avg_loss': float(avg_loss),
        'profit_factor': float(profit_factor)
    }

def get_active_strategy():
    """Get currently active strategy information"""
    try:
        strategy_files = [f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.json')]
        
        if not strategy_files:
            return {
                'name': 'Default Strategy',
                'file': None,
                'loaded': False
            }
        
        latest_file = max(
            [os.path.join(UPLOAD_FOLDER, f) for f in strategy_files],
            key=os.path.getmtime
        )
        
        with open(latest_file, 'r') as f:
            strategy_data = json.load(f)
        
        if len(strategy_data) == 1:
            strategy_key = list(strategy_data.keys())[0]
            strategy_name = strategy_data[strategy_key].get('strategy_name', strategy_key)
        else:
            strategy_name = strategy_data.get('strategy_name', 'Custom Strategy')
        
        return {
            'name': strategy_name,
            'file': os.path.basename(latest_file),
            'loaded': True,
            'upload_time': datetime.fromtimestamp(os.path.getmtime(latest_file)).strftime('%Y-%m-%d %H:%M:%S')
        }
        
    except Exception as e:
        print(f"Error getting active strategy: {e}")
        return {
            'name': 'Error loading strategy',
            'file': None,
            'loaded': False,
            'error': str(e)
        }

def validate_strategy_json(data):
    """Validate strategy JSON structure"""
    if not isinstance(data, dict):
        return False, "Strategy must be a JSON object"
    
    if len(data) == 1:
        parent_key = list(data.keys())[0]
        inner_data = data[parent_key]
        if not isinstance(inner_data, dict):
            return False, "Invalid strategy structure"
    
    return True, "Valid"

# API Routes
@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/account')
def api_account():
    """API endpoint for account info"""
    account = get_account_info()
    return jsonify(account if account else {})

@app.route('/api/drawdown')
def api_drawdown():
    """API endpoint for drawdown data"""
    drawdown = calculate_drawdown()
    return jsonify(drawdown)

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

@app.route('/api/realtime/tick/<symbol>')
def api_realtime_tick(symbol):
    """API endpoint for real-time tick data"""
    tick = get_realtime_tick(symbol)
    return jsonify(tick if tick else {})

@app.route('/api/realtime/ohlc/<symbol>')
def api_realtime_ohlc(symbol):
    """API endpoint for real-time OHLC data"""
    ohlc = get_realtime_ohlc(symbol)
    return jsonify(ohlc if ohlc else {})

@app.route('/api/realtime/all')
def api_realtime_all():
    """API endpoint for all symbols real-time data"""
    data = get_all_symbols_realtime()
    return jsonify(data)

@app.route('/api/ticks/<symbol>')
def api_ticks(symbol):
    """API endpoint for historical tick data"""
    limit = request.args.get('limit', 100, type=int)
    ticks = get_recent_ticks(symbol, limit)
    return jsonify(ticks)

@app.route('/api/ohlc/<symbol>')
def api_ohlc(symbol):
    """API endpoint for historical OHLC data"""
    limit = request.args.get('limit', 100, type=int)
    ohlc = get_recent_ohlc(symbol, limit)
    return jsonify(ohlc)

@app.route('/api/strategy')
def api_strategy():
    """API endpoint to get active strategy info"""
    strategy = get_active_strategy()
    return jsonify(strategy)

@app.route('/api/strategy/upload', methods=['POST'])
def upload_strategy():
    """Upload new strategy JSON file"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Only JSON files are allowed'}), 400
        
        try:
            content = file.read()
            strategy_data = json.loads(content)
            
            is_valid, message = validate_strategy_json(strategy_data)
            if not is_valid:
                return jsonify({'success': False, 'error': message}), 400
            
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': f'Invalid JSON: {str(e)}'}), 400
        
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        with open(filepath, 'w') as f:
            json.dump(strategy_data, f, indent=2)
        
        if len(strategy_data) == 1:
            strategy_key = list(strategy_data.keys())[0]
            strategy_name = strategy_data[strategy_key].get('strategy_name', strategy_key)
        else:
            strategy_name = strategy_data.get('strategy_name', 'Custom Strategy')
        
        return jsonify({
            'success': True,
            'message': f'Strategy "{strategy_name}" uploaded successfully',
            'filename': filename,
            'strategy_name': strategy_name
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/list')
def list_strategies():
    """List all uploaded strategy files"""
    try:
        strategy_files = []
        
        for filename in os.listdir(UPLOAD_FOLDER):
            if filename.endswith('.json'):
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                
                try:
                    with open(filepath, 'r') as f:
                        strategy_data = json.load(f)
                    
                    if len(strategy_data) == 1:
                        strategy_key = list(strategy_data.keys())[0]
                        strategy_name = strategy_data[strategy_key].get('strategy_name', strategy_key)
                    else:
                        strategy_name = strategy_data.get('strategy_name', filename)
                    
                    strategy_files.append({
                        'filename': filename,
                        'name': strategy_name,
                        'upload_time': datetime.fromtimestamp(os.path.getmtime(filepath)).strftime('%Y-%m-%d %H:%M:%S'),
                        'size': os.path.getsize(filepath)
                    })
                except:
                    pass
        
        strategy_files.sort(key=lambda x: x['upload_time'], reverse=True)
        
        return jsonify(strategy_files)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/strategy/delete/<filename>', methods=['DELETE'])
def delete_strategy(filename):
    """Delete a strategy file"""
    try:
        filename = secure_filename(filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        os.remove(filepath)
        
        return jsonify({
            'success': True,
            'message': f'Strategy "{filename}" deleted successfully'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("="*60)
    print("Starting Enhanced Flask Dashboard on http://localhost:5000")
    print("Real-time tick updates enabled")
    print("Strategy upload folder:", UPLOAD_FOLDER)
    print("="*60)
    app.run(debug=True, host='0.0.0.0', port=5000)