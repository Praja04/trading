import yaml
import logging
import os
from datetime import datetime

def load_config(config_path):
    """Load configuration from YAML file"""
    try:
        with open(config_path, 'r') as file:
            config = yaml.safe_load(file)
        return config
    except Exception as e:
        logging.error(f"Error loading config from {config_path}: {str(e)}")
        return {}

def setup_logging(log_dir='logs', log_level=logging.INFO):
    """Setup logging configuration"""
    # Create logs directory if not exists
    os.makedirs(log_dir, exist_ok=True)
    
    # Generate log filename with timestamp
    log_filename = f"{log_dir}/trading_{datetime.now().strftime('%Y%m%d')}.log"
    
    # Configure logging
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler()
        ]
    )
    
    # Suppress some noisy loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('matplotlib').setLevel(logging.WARNING)

def format_currency(amount, currency='USD'):
    """Format currency for display"""
    return f"{currency} {amount:,.2f}"

def calculate_pips(price1, price2, symbol):
    """Calculate pips difference between two prices"""
    # Simplified: assumes 4-decimal pairs (EURUSD, etc.)
    # For JPY pairs, use 2 decimals
    if 'JPY' in symbol:
        return abs(price1 - price2) * 100
    else:
        return abs(price1 - price2) * 10000

def format_percentage(value):
    """Format percentage for display"""
    return f"{value:.2f}%"

def validate_symbol(symbol):
    """Validate if symbol format is correct"""
    if len(symbol) < 6:
        return False
    if not symbol.replace('/', '').replace('_', '').isalpha():
        return False
    return True

def get_timeframe_minutes(timeframe_str):
    """Convert timeframe string to minutes"""
    timeframe_map = {
        'M1': 1,
        'M5': 5,
        'M15': 15,
        'M30': 30,
        'H1': 60,
        'H4': 240,
        'D1': 1440,
        'W1': 10080,
        'MN1': 43200
    }
    return timeframe_map.get(timeframe_str, 1)

def risk_reward_ratio(entry, stop_loss, take_profit):
    """Calculate risk-reward ratio"""
    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    
    if risk == 0:
        return 0
    
    return reward / risk

class TradingMetrics:
    """Calculate and track trading performance metrics"""
    
    def __init__(self):
        self.trades = []
        self.total_profit = 0
        self.total_loss = 0
        self.winning_trades = 0
        self.losing_trades = 0
    
    def add_trade(self, profit, trade_info=None):
        """Add a completed trade"""
        self.trades.append({
            'profit': profit,
            'timestamp': datetime.now(),
            'info': trade_info
        })
        
        if profit > 0:
            self.winning_trades += 1
            self.total_profit += profit
        else:
            self.losing_trades += 1
            self.total_loss += abs(profit)
    
    def get_win_rate(self):
        """Calculate win rate percentage"""
        total = self.winning_trades + self.losing_trades
        if total == 0:
            return 0
        return (self.winning_trades / total) * 100
    
    def get_profit_factor(self):
        """Calculate profit factor"""
        if self.total_loss == 0:
            return float('inf') if self.total_profit > 0 else 0
        return self.total_profit / self.total_loss
    
    def get_average_win(self):
        """Calculate average winning trade"""
        if self.winning_trades == 0:
            return 0
        return self.total_profit / self.winning_trades
    
    def get_average_loss(self):
        """Calculate average losing trade"""
        if self.losing_trades == 0:
            return 0
        return self.total_loss / self.losing_trades
    
    def get_summary(self):
        """Get trading performance summary"""
        return {
            'total_trades': len(self.trades),
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': self.get_win_rate(),
            'profit_factor': self.get_profit_factor(),
            'total_profit': self.total_profit,
            'total_loss': self.total_loss,
            'net_profit': self.total_profit - self.total_loss,
            'average_win': self.get_average_win(),
            'average_loss': self.get_average_loss()
        }
    
    def print_summary(self):
        """Print trading performance summary"""
        summary = self.get_summary()
        
        print("\n" + "="*60)
        print("TRADING PERFORMANCE SUMMARY")
        print("="*60)
        print(f"Total Trades: {summary['total_trades']}")
        print(f"Winning Trades: {summary['winning_trades']}")
        print(f"Losing Trades: {summary['losing_trades']}")
        print(f"Win Rate: {summary['win_rate']:.2f}%")
        print(f"Profit Factor: {summary['profit_factor']:.2f}")
        print(f"Total Profit: ${summary['total_profit']:.2f}")
        print(f"Total Loss: ${summary['total_loss']:.2f}")
        print(f"Net Profit: ${summary['net_profit']:.2f}")
        print(f"Average Win: ${summary['average_win']:.2f}")
        print(f"Average Loss: ${summary['average_loss']:.2f}")
        print("="*60 + "\n")