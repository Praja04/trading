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
import sys
import yaml
sys.path.append('.')
sys.path.append('src')
from news_collector import NewsCollector, start_news_updater
from trade_history import TradeHistoryManager

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = 'config/strategies'
ALLOWED_EXTENSIONS = {'json'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Create upload folder if not exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# MT5 Configuration — read from broker.yaml (NOT hard-coded)
DB_PATH = "data/database/trading_data.db"

def _load_broker_config():
    try:
        with open('config/broker.yaml', 'r') as f:
            cfg = yaml.safe_load(f)
        broker = cfg.get('broker', {})
        return (
            int(broker.get('account', 0)),
            str(broker.get('password', '')),
            str(broker.get('server', ''))
        )
    except Exception as e:
        print(f"[WARNING] Could not load broker.yaml: {e}")
        return (0, '', '')

MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER = _load_broker_config()

# Initialize collectors
news_collector = NewsCollector(DB_PATH)
trade_history_manager = TradeHistoryManager(DB_PATH)
news_updater_thread = start_news_updater(interval_minutes=30)
print("✓ News updater started via Forex Factory (updates every 30 minutes)")

# Global variables for real-time data
symbols_to_track = []  # Will be populated by load_symbols_from_config()

# ======================================================================
# CORE FUNCTIONS
# ======================================================================

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_symbols_from_config():
    """Load trading symbols from strategy state file or config"""
    try:
        # Priority 1: Strategy state file
        state_file = 'config/.current_strategy_state.json'
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
                
                strategy_info = state.get('strategy_info', {})
                if 'pairs' in strategy_info and strategy_info['pairs']:
                    symbols = strategy_info['pairs']
                    print(f"✓ Loaded {len(symbols)} symbols from strategy state")
                    return symbols
            except Exception as e:
                print(f"⚠ Error reading state file: {e}")
                # Fall through to config file
        
        # Priority 2: Config file
        config_path = 'config/trading_config.yaml'
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                symbols = config.get('symbols', ["EURUSD.s", "GBPUSD.s", "USDJPY.s"])
                print(f"✓ Loaded {len(symbols)} symbols from config")
                return symbols
        
        # Priority 3: Default
        print("⚠ No config files found, using default symbols")
        return ["EURUSD.s", "GBPUSD.s", "USDJPY.s"]
        
    except Exception as e:
        print(f"Error loading symbols: {e}")
        return ["EURUSD.s", "GBPUSD.s", "USDJPY.s"]

def refresh_global_symbols():
    """Refresh symbols from current strategy state - call this after strategy upload"""
    global symbols_to_track
    
    try:
        symbols_to_track = load_symbols_from_config()
        print(f"🔄 SYMBOLS REFRESHED: {len(symbols_to_track)} pairs -> {symbols_to_track}")
        return symbols_to_track
    except Exception as e:
        print(f"✗ Error refreshing symbols: {e}")
        return []

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
    
    try:
        current_equity = float(account.equity)
        balance = float(account.balance)
        
        current_drawdown = float(max(0, balance - current_equity))
        drawdown_percent = float((current_drawdown / balance * 100)) if balance > 0 else 0.0
        
        return {
            'current_drawdown': float(current_drawdown),
            'max_drawdown': float(current_drawdown),
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

def get_tick_history_from_db(symbol, minutes=60):
    """Get tick history from database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        
        query = f'''
            SELECT timestamp, bid, ask, spread, volume
            FROM ticks
            WHERE symbol = ?
            AND timestamp >= datetime('now', '-{minutes} minutes')
            ORDER BY timestamp ASC
        '''
        
        df = pd.read_sql_query(query, conn, params=(symbol,))
        conn.close()
        
        if df.empty:
            return []
        
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df.to_dict('records')
        
    except Exception as e:
        print(f"Error getting tick history: {e}")
        return []

# ======================================================================
# STRATEGY MANAGEMENT FUNCTIONS
# ======================================================================

def extract_strategy_info(strategy_data):
    """Extract key information from strategy JSON"""
    try:
        strategy = None
        
        if isinstance(strategy_data, dict):
            if len(strategy_data) == 1:
                strategy_key = list(strategy_data.keys())[0]
                strategy = strategy_data[strategy_key]
            else:
                strategy = strategy_data
        else:
            raise ValueError("Strategy data must be a dictionary")
        
        # Extract symbols/pairs from multiple possible locations
        pairs = []
        
        if 'pairs' in strategy:
            pairs = strategy['pairs']
        elif 'trading_pairs' in strategy:
            pairs = strategy['trading_pairs']
        elif 'symbols' in strategy:
            pairs = strategy['symbols']
        elif 'parameters' in strategy and 'trading_pairs' in strategy['parameters']:
            pairs = strategy['parameters']['trading_pairs']
        elif 'parameters' in strategy and 'symbols' in strategy['parameters']:
            pairs = strategy['parameters']['symbols']
        
        if not isinstance(pairs, list):
            pairs = []
        
        return {
            'name': strategy.get('strategy_name', strategy.get('name', 'Unknown Strategy')),
            'philosophy': strategy.get('core_philosophy', strategy.get('philosophy', 'N/A')),
            'timeframes': strategy.get('parameters', {}).get('timeframes', strategy.get('timeframes', [])),
            'pairs': pairs,
            'risk_per_trade': strategy.get('parameters', {}).get('risk_per_trade_range', []),
            'performance_targets': strategy.get('performance_targets', {})
        }
    except Exception as e:
        print(f"Error extracting strategy info: {e}")
        import traceback
        print(traceback.format_exc())
        return {
            'name': 'Unknown',
            'philosophy': 'Error parsing strategy',
            'timeframes': [],
            'pairs': [],
            'risk_per_trade': [],
            'performance_targets': {}
        }

# ======================================================================
# INITIALIZE SYMBOLS
# ======================================================================

symbols_to_track = refresh_global_symbols()
print(f"📊 Initialized with {len(symbols_to_track)} trading symbols")

# ======================================================================
# MAIN ROUTES
# ======================================================================

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/account')
def api_account():
    """Get account information"""
    account = get_account_info()
    if account is None:
        return jsonify({'error': 'MT5 not connected'}), 500
    
    return jsonify(account)

@app.route('/api/positions')
def api_positions():
    """Get all open positions"""
    positions = get_open_positions()
    return jsonify(positions)

@app.route('/api/trades/today')
def api_trades_today():
    """Get today's closed trades"""
    trades = get_closed_trades_today()
    return jsonify(trades)

@app.route('/api/realtime')
def api_realtime():
    """Get real-time data for all symbols"""
    data = get_all_symbols_realtime()
    return jsonify(data)

@app.route('/api/realtime/<symbol>')
def api_realtime_symbol(symbol):
    """Get real-time data for a specific symbol"""
    tick = get_realtime_tick(symbol)
    ohlc = get_realtime_ohlc(symbol)
    
    if tick is None or ohlc is None:
        return jsonify({'error': 'Symbol not found or MT5 not connected'}), 404
    
    return jsonify({
        'tick': tick,
        'ohlc': ohlc
    })

@app.route('/api/realtime/all')
def api_realtime_all():
    """Get real-time data for all tracked symbols"""
    try:
        print(f"📊 API realtime/all called - Using {len(symbols_to_track)} symbols")
        
        data = {}
        for symbol in symbols_to_track:
            tick_data = get_realtime_tick(symbol)
            ohlc_data = get_realtime_ohlc(symbol)
            
            if tick_data and ohlc_data:
                data[symbol] = {
                    'tick': tick_data,
                    'ohlc': ohlc_data
                }
        
        return jsonify({
            'success': True,
            'data': data,
            'symbols': symbols_to_track,
            'count': len(data),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })
    except Exception as e:
        print(f"Error in api_realtime_all: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/tick-history/<symbol>')
def api_tick_history(symbol):
    """Get tick history for a symbol"""
    minutes = request.args.get('minutes', 60, type=int)
    history = get_tick_history_from_db(symbol, minutes)
    
    return jsonify(history)

@app.route('/api/trades')
def api_trades():
    """Get today's closed trades (alias for /api/trades/today)"""
    return api_trades_today()

@app.route('/api/stats')
def api_stats():
    """Get trading statistics"""
    try:
        trades = get_closed_trades_today()
        
        winning_trades = [t for t in trades if t['profit'] > 0]
        losing_trades = [t for t in trades if t['profit'] < 0]
        
        total_profit = sum(t['profit'] for t in winning_trades)
        total_loss = abs(sum(t['profit'] for t in losing_trades))
        net_profit = sum(t['profit'] for t in trades)
        
        stats = {
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': (len(winning_trades) / len(trades) * 100) if trades else 0,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'net_profit': net_profit,
            'avg_win': (total_profit / len(winning_trades)) if winning_trades else 0,
            'avg_loss': (total_loss / len(losing_trades)) if losing_trades else 0,
            'profit_factor': (total_profit / total_loss) if total_loss > 0 else 0
        }
        
        return jsonify({
            'success': True,
            'stats': stats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy')
def api_strategy():
    """Get current strategy info (alias for /api/strategy/current)"""
    return api_current_strategy()

@app.route('/api/drawdown')
def api_drawdown():
    """Get drawdown information"""
    dd = calculate_drawdown()
    return jsonify(dd)

# ======================================================================
# NEWS API ENDPOINTS
# ======================================================================

@app.route('/api/news/recent')
def api_news_recent():
    """Get recent news"""
    try:
        hours = request.args.get('hours', 24, type=int)
        impact = request.args.get('impact', None)
        
        news = news_collector.get_recent_news(hours=hours, impact=impact)
        
        return jsonify({
            'success': True,
            'count': len(news),
            'news': news
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/news/upcoming')
def api_news_upcoming():
    """Get upcoming news"""
    try:
        hours = request.args.get('hours', 48, type=int)
        impact = request.args.get('impact', None)
        
        news = news_collector.get_upcoming_news(hours=hours, impact=impact)
        
        return jsonify({
            'success': True,
            'count': len(news),
            'news': news
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/news/high-impact-today')
def api_news_high_impact_today():
    """Get high impact news for today"""
    try:
        news = news_collector.get_high_impact_news_today()
        
        return jsonify({
            'success': True,
            'count': len(news),
            'news': news
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/news/update', methods=['POST'])
def api_news_update():
    """Manually trigger news update from Forex Factory"""
    try:
        count = news_collector.update_news()
        return jsonify({
            'success': True,
            'message': f'Updated {count} news items from Forex Factory',
            'count': count
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ======================================================================
# TRADE HISTORY API ENDPOINTS
# ======================================================================

@app.route('/api/history/trades')
def api_history_trades():
    """Get trade history"""
    try:
        days = request.args.get('days', None, type=int)
        symbol = request.args.get('symbol', None)
        limit = request.args.get('limit', 100, type=int)
        
        trades = trade_history_manager.get_trade_history(days=days, symbol=symbol, limit=limit)
        
        return jsonify({
            'success': True,
            'count': len(trades),
            'trades': trades
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/history/statistics')
def api_history_statistics():
    """Get trade statistics"""
    try:
        days = request.args.get('days', 30, type=int)
        stats = trade_history_manager.get_trade_statistics(days=days)
        
        return jsonify({
            'success': True,
            'statistics': stats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/history/sync', methods=['POST'])
def api_history_sync():
    """Sync trades from MT5 to database"""
    try:
        days = request.args.get('days', 30, type=int)
        count = trade_history_manager.sync_closed_trades_from_mt5(days=days)
        
        return jsonify({
            'success': True,
            'message': f'Synced {count} trades from MT5',
            'count': count
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/history/export')
def api_history_export():
    """Export trade history to CSV"""
    try:
        days = request.args.get('days', None, type=int)
        filename = f"trade_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join('data', filename)
        
        success = trade_history_manager.export_to_csv(filepath, days=days)
        
        if success:
            return send_from_directory('data', filename, as_attachment=True)
        else:
            return jsonify({'error': 'Export failed'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ======================================================================
# POSITION MANAGEMENT API ENDPOINTS
# ======================================================================

@app.route('/api/positions/close/<int:ticket>', methods=['POST'])
def api_close_position(ticket):
    """Close a specific position by ticket"""
    if not init_mt5():
        return jsonify({'success': False, 'error': 'MT5 not connected'}), 500
    
    try:
        position = None
        positions = mt5.positions_get(ticket=ticket)
        
        if positions and len(positions) > 0:
            position = positions[0]
        else:
            return jsonify({'success': False, 'error': 'Position not found'}), 404
        
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY,
            "price": mt5.symbol_info_tick(position.symbol).bid if position.type == 0 else mt5.symbol_info_tick(position.symbol).ask,
            "deviation": 20,
            "magic": position.magic,
            "comment": "Closed via Dashboard",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(close_request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return jsonify({
                'success': False,
                'error': f'Failed to close position: {result.comment}',
                'retcode': result.retcode
            }), 500
        
        return jsonify({
            'success': True,
            'message': f'Position {ticket} closed successfully',
            'ticket': ticket,
            'order': result.order
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/positions/close-all', methods=['POST'])
def api_close_all_positions():
    """Close all open positions"""
    if not init_mt5():
        return jsonify({'success': False, 'error': 'MT5 not connected'}), 500
    
    try:
        positions = mt5.positions_get()
        
        if not positions or len(positions) == 0:
            return jsonify({
                'success': True,
                'message': 'No positions to close',
                'closed': 0,
                'failed': 0
            })
        
        closed_count = 0
        failed_count = 0
        results = []
        
        for position in positions:
            try:
                close_request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": position.ticket,
                    "symbol": position.symbol,
                    "volume": position.volume,
                    "type": mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY,
                    "price": mt5.symbol_info_tick(position.symbol).bid if position.type == 0 else mt5.symbol_info_tick(position.symbol).ask,
                    "deviation": 20,
                    "magic": position.magic,
                    "comment": "Close All via Dashboard",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                
                result = mt5.order_send(close_request)
                
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    closed_count += 1
                    results.append({
                        'ticket': position.ticket,
                        'symbol': position.symbol,
                        'status': 'closed',
                        'profit': position.profit
                    })
                else:
                    failed_count += 1
                    results.append({
                        'ticket': position.ticket,
                        'symbol': position.symbol,
                        'status': 'failed',
                        'error': result.comment
                    })
                    
            except Exception as e:
                failed_count += 1
                results.append({
                    'ticket': position.ticket,
                    'symbol': position.symbol,
                    'status': 'error',
                    'error': str(e)
                })
        
        return jsonify({
            'success': True,
            'message': f'Closed {closed_count} positions, {failed_count} failed',
            'closed': closed_count,
            'failed': failed_count,
            'details': results
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/positions/close-symbol/<symbol>', methods=['POST'])
def api_close_symbol_positions(symbol):
    """Close all positions for a specific symbol"""
    if not init_mt5():
        return jsonify({'success': False, 'error': 'MT5 not connected'}), 500
    
    try:
        positions = mt5.positions_get(symbol=symbol)
        
        if not positions or len(positions) == 0:
            return jsonify({
                'success': True,
                'message': f'No positions for {symbol}',
                'closed': 0
            })
        
        closed_count = 0
        failed_count = 0
        
        for position in positions:
            try:
                close_request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": position.ticket,
                    "symbol": position.symbol,
                    "volume": position.volume,
                    "type": mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY,
                    "price": mt5.symbol_info_tick(position.symbol).bid if position.type == 0 else mt5.symbol_info_tick(position.symbol).ask,
                    "deviation": 20,
                    "magic": position.magic,
                    "comment": f"Close {symbol} via Dashboard",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                
                result = mt5.order_send(close_request)
                
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    closed_count += 1
                else:
                    failed_count += 1
                    
            except Exception as e:
                failed_count += 1
                print(f"Error closing position {position.ticket}: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': f'Closed {closed_count} {symbol} positions, {failed_count} failed',
            'closed': closed_count,
            'failed': failed_count
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ======================================================================
# STRATEGY MANAGEMENT API ENDPOINTS - UPDATED
# ======================================================================

@app.route('/api/strategy/upload', methods=['POST'])
def api_upload_strategy():
    """Upload a new strategy JSON file AND update current strategy state"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': 'Invalid file type. Only JSON files allowed'}), 400
        
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        try:
            file_content = file.read()
            strategy_data = json.loads(file_content)
            
            if not isinstance(strategy_data, dict):
                return jsonify({'success': False, 'error': 'Invalid strategy format'}), 400
            
            with open(filepath, 'wb') as f:
                f.write(file_content)
            
            print(f"✓ Strategy file saved: {filepath}")
            
            # Extract strategy info
            strategy_info = extract_strategy_info(strategy_data)
            
            # Create updated state
            state = {
                'strategy_name': strategy_info['name'],
                'strategy_path': filepath,
                'strategy_info': strategy_info,
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'timestamp': datetime.now().isoformat()
            }
            
            # Save to state file
            state_file = 'config/.current_strategy_state.json'
            with open(state_file, 'w') as f:
                json.dump(state, f, indent=2)
            
            print(f"✓ Strategy state updated: {strategy_info['name']}")
            print(f"  Last updated: {state['last_updated']}")
            print(f"  Symbols in strategy: {strategy_info.get('pairs', [])}")
            
            # ======================================================================
            # CRITICAL: REFRESH GLOBAL SYMBOLS IMMEDIATELY
            # ======================================================================
            refreshed_symbols = refresh_global_symbols()
            
            # ======================================================================
            # CRITICAL: CREATE RELOAD SIGNAL FOR MAIN.PY
            # ======================================================================
            signal_file = 'config/.reload_strategy'
            with open(signal_file, 'w') as f:
                f.write(datetime.now().isoformat())
            
            print(f"✓ Reload signal created: {signal_file}")
            print(f"✓ Main.py will reload with new symbols: {refreshed_symbols}")
            
            return jsonify({
                'success': True,
                'message': f'Strategy "{filename}" uploaded and activated successfully',
                'filename': filename,
                'filepath': filepath,
                'strategy_info': strategy_info,
                'symbols': refreshed_symbols,  # ← SEND NEW SYMBOLS TO FRONTEND
                'symbols_count': len(refreshed_symbols),
                'state_updated': True,
                'reload_signal_created': True
            })
            
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': f'Invalid JSON format: {str(e)}'}), 400
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/list')
def api_list_strategies():
    """List all available strategy files"""
    try:
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            return jsonify({'success': True, 'strategies': []})
        
        strategies = []
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            if filename.endswith('.json'):
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                
                try:
                    with open(filepath, 'r') as f:
                        strategy_data = json.load(f)
                    
                    strategy_info = extract_strategy_info(strategy_data)
                    strategy_info['filename'] = filename
                    strategy_info['filepath'] = filepath
                    strategy_info['last_modified'] = datetime.fromtimestamp(
                        os.path.getmtime(filepath)
                    ).strftime('%Y-%m-%d %H:%M:%S')
                    
                    strategies.append(strategy_info)
                    
                except Exception as e:
                    print(f"Error reading strategy {filename}: {e}")
                    continue
        
        return jsonify({
            'success': True,
            'count': len(strategies),
            'strategies': strategies
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/current')
def api_current_strategy():
    """Get the currently active strategy from state file"""
    try:
        state_file = 'config/.current_strategy_state.json'
        
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
                
                strategy_file = state.get('strategy_path')
                if strategy_file and os.path.exists(strategy_file):
                    return jsonify({
                        'success': True,
                        'strategy': state.get('strategy_info', {}),
                        'filename': os.path.basename(strategy_file),
                        'filepath': strategy_file,
                        'last_updated': state.get('last_updated', 'Unknown'),
                        'from_state_file': True
                    })
            except Exception as e:
                print(f"Error reading state file: {e}")
        
        strategies_folder = app.config['UPLOAD_FOLDER']
        
        if not os.path.exists(strategies_folder):
            return jsonify({'success': False, 'error': 'No strategies folder found'}), 404
        
        json_files = [f for f in os.listdir(strategies_folder) if f.endswith('.json')]
        
        if not json_files:
            return jsonify({'success': False, 'error': 'No strategy files found'}), 404
        
        latest_file = max(
            [os.path.join(strategies_folder, f) for f in json_files],
            key=os.path.getmtime
        )
        
        with open(latest_file, 'r') as f:
            strategy_data = json.load(f)
        
        strategy_info = extract_strategy_info(strategy_data)
        
        state = {
            'strategy_name': strategy_info['name'],
            'strategy_path': latest_file,
            'strategy_info': strategy_info,
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp': datetime.now().isoformat()
        }
        
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        print(f"✓ Auto-created state file for: {strategy_info['name']}")
        
        return jsonify({
            'success': True,
            'strategy': strategy_info,
            'from_latest_file': True,
            'state_auto_created': True
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/update-state', methods=['POST'])
def api_update_strategy_state():
    """Manually update the current strategy state file"""
    try:
        strategies_folder = app.config['UPLOAD_FOLDER']
        
        if not os.path.exists(strategies_folder):
            return jsonify({'success': False, 'error': 'No strategies folder found'}), 404
        
        json_files = [f for f in os.listdir(strategies_folder) if f.endswith('.json')]
        
        if not json_files:
            return jsonify({'success': False, 'error': 'No strategy files found'}), 404
        
        latest_file = max(
            [os.path.join(strategies_folder, f) for f in json_files],
            key=os.path.getmtime
        )
        
        with open(latest_file, 'r') as f:
            strategy_data = json.load(f)
        
        strategy_info = extract_strategy_info(strategy_data)
        
        state = {
            'strategy_name': strategy_info['name'],
            'strategy_path': latest_file,
            'strategy_info': strategy_info,
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'timestamp': datetime.now().isoformat()
        }
        
        state_file = 'config/.current_strategy_state.json'
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        signal_file = 'config/.reload_strategy'
        with open(signal_file, 'w') as f:
            f.write(datetime.now().isoformat())
        
        return jsonify({
            'success': True,
            'message': f'Strategy state updated to: {strategy_info["name"]}',
            'strategy': strategy_info,
            'state_file': state_file,
            'reload_signal_created': True,
            'strategy_file': latest_file
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/state', methods=['GET'])
def api_get_strategy_state():
    """Get the current strategy state file content"""
    try:
        state_file = 'config/.current_strategy_state.json'
        
        if not os.path.exists(state_file):
            return jsonify({
                'success': False,
                'error': 'No strategy state file found',
                'exists': False
            }), 404
        
        with open(state_file, 'r') as f:
            state = json.load(f)
        
        strategy_file = state.get('strategy_path')
        file_exists = os.path.exists(strategy_file) if strategy_file else False
        
        return jsonify({
            'success': True,
            'state': state,
            'file_exists': file_exists,
            'last_modified': datetime.fromtimestamp(os.path.getmtime(state_file)).isoformat() if os.path.exists(state_file) else None
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/strategy/reload', methods=['POST'])
def api_reload_strategy():
    """Reload the strategy in the trading system"""
    try:
        signal_file = 'config/.reload_strategy'
        
        with open(signal_file, 'w') as f:
            f.write(datetime.now().isoformat())
        
        return jsonify({
            'success': True,
            'message': 'Strategy reload signal sent. Trading system will reload on next cycle.'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ======================================================================
# CONFIGURATION API ENDPOINTS
# ======================================================================

@app.route('/api/config/symbols')
def api_get_symbols():
    """Get configured trading symbols"""
    global symbols_to_track
    return jsonify({
        'success': True,
        'symbols': symbols_to_track,
        'count': len(symbols_to_track)
    })

@app.route('/api/config/reload', methods=['POST'])
def api_reload_config():
    """Reload configuration including symbols"""
    global symbols_to_track
    
    try:
        symbols_to_track = refresh_global_symbols()
        
        return jsonify({
            'success': True,
            'message': 'Configuration reloaded',
            'symbols': symbols_to_track,
            'count': len(symbols_to_track)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ======================================================================
# DEBUG & REFRESH ENDPOINTS
# ======================================================================

@app.route('/api/refresh/symbols', methods=['POST'])
def api_refresh_symbols():
    """Manually refresh symbols from current strategy"""
    try:
        refreshed_symbols = refresh_global_symbols()
        
        return jsonify({
            'success': True,
            'message': f'Symbols refreshed: {len(refreshed_symbols)} pairs',
            'symbols': refreshed_symbols,
            'count': len(refreshed_symbols)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/refresh/all', methods=['POST'])
def api_refresh_all():
    """Force refresh everything: symbols + create reload signal for main.py"""
    try:
        refreshed_symbols = refresh_global_symbols()
        
        signal_file = 'config/.reload_strategy'
        with open(signal_file, 'w') as f:
            f.write(datetime.now().isoformat())
        
        return jsonify({
            'success': True,
            'message': 'Full refresh completed',
            'symbols': refreshed_symbols,
            'reload_signal_created': True
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/debug/status')
def api_debug_status():
    """Debug endpoint to check current state"""
    return jsonify({
        'symbols_to_track': symbols_to_track,
        'symbols_count': len(symbols_to_track),
        'state_file_exists': os.path.exists('config/.current_strategy_state.json'),
        'reload_signal_exists': os.path.exists('config/.reload_strategy'),
        'current_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

# ======================================================================
# INITIALIZATION
# ======================================================================

print("="*80)
print("🚀 ENHANCED FLASK DASHBOARD")
print("="*80)
print(f"📊 Initial trading symbols: {symbols_to_track}")
print(f"📂 Strategy upload folder: {UPLOAD_FOLDER}")
print(f"📡 Real-time market updates: ENABLED")
print(f"🔄 Hot-reload: ENABLED (symbols auto-refresh after strategy upload)")
print(f"⚡ Main.py auto-reload: ENABLED via signal file")
print("="*80)

state_file = 'config/.current_strategy_state.json'
if os.path.exists(state_file):
    try:
        with open(state_file, 'r') as f:
            state = json.load(f)
        print(f"✓ Current strategy: {state.get('strategy_name', 'Unknown')}")
        strategy_info = state.get('strategy_info', {})
        if 'pairs' in strategy_info:
            print(f"✓ Strategy symbols: {strategy_info['pairs']}")
    except Exception as e:
        print(f"⚠ Error reading state file: {e}")

print("="*80)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)