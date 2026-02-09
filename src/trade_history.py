import MetaTrader5 as mt5
import sqlite3
import logging
from datetime import datetime, timedelta

class TradeHistoryManager:
    """Manage trade history in database"""
    
    def __init__(self, db_path="data/database/trading_data.db"):
        self.db_path = db_path
        self.init_trade_history_table()
    
    def init_trade_history_table(self):
        """Initialize trade_history table"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket INTEGER UNIQUE,
                    order_ticket INTEGER,
                    symbol TEXT,
                    type TEXT,
                    volume REAL,
                    open_price REAL,
                    close_price REAL,
                    open_time DATETIME,
                    close_time DATETIME,
                    profit REAL,
                    commission REAL,
                    swap REAL,
                    magic INTEGER,
                    comment TEXT,
                    duration_seconds INTEGER
                )
            ''')
            
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_close_time ON trade_history(close_time)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_symbol ON trade_history(symbol)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_trade_magic ON trade_history(magic)')
            
            conn.commit()
            conn.close()
            logging.info("Trade history table initialized successfully")
            
        except Exception as e:
            logging.error(f"Error initializing trade history table: {str(e)}")
    
    def sync_closed_trades_from_mt5(self, days=30):
        """Sync closed trades from MT5 to database"""
        if not mt5.initialize():
            logging.error("MT5 not initialized")
            return 0
        
        try:
            from_date = datetime.now() - timedelta(days=days)
            to_date = datetime.now()
            
            # Get all deals from MT5
            deals = mt5.history_deals_get(from_date, to_date)
            
            if deals is None or len(deals) == 0:
                logging.info("No deals found in MT5 history")
                return 0
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Group deals by position ticket to get complete trades
            position_deals = {}
            
            for deal in deals:
                position_id = deal.position_id
                
                if position_id not in position_deals:
                    position_deals[position_id] = []
                
                position_deals[position_id].append(deal)
            
            saved_count = 0
            
            for position_id, deal_list in position_deals.items():
                # Sort deals by time
                deal_list.sort(key=lambda x: x.time)
                
                # Find entry and exit deals
                entry_deal = None
                exit_deal = None
                
                for deal in deal_list:
                    if deal.entry == 0:  # DEAL_ENTRY_IN
                        entry_deal = deal
                    elif deal.entry == 1:  # DEAL_ENTRY_OUT
                        exit_deal = deal
                
                # Only save complete trades (with both entry and exit)
                if entry_deal and exit_deal:
                    try:
                        open_time = datetime.fromtimestamp(entry_deal.time)
                        close_time = datetime.fromtimestamp(exit_deal.time)
                        duration = int((close_time - open_time).total_seconds())
                        
                        cursor.execute('''
                            INSERT OR REPLACE INTO trade_history
                            (ticket, order_ticket, symbol, type, volume, open_price, close_price,
                             open_time, close_time, profit, commission, swap, magic, comment, duration_seconds)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            exit_deal.ticket,
                            exit_deal.order,
                            exit_deal.symbol,
                            'BUY' if exit_deal.type == 0 else 'SELL',
                            exit_deal.volume,
                            entry_deal.price,
                            exit_deal.price,
                            open_time.strftime('%Y-%m-%d %H:%M:%S'),
                            close_time.strftime('%Y-%m-%d %H:%M:%S'),
                            exit_deal.profit,
                            exit_deal.commission,
                            exit_deal.swap,
                            exit_deal.magic,
                            exit_deal.comment,
                            duration
                        ))
                        
                        saved_count += 1
                        
                    except Exception as e:
                        logging.error(f"Error saving trade {exit_deal.ticket}: {str(e)}")
                        continue
            
            conn.commit()
            conn.close()
            
            logging.info(f"Synced {saved_count} trades from MT5 to database")
            return saved_count
            
        except Exception as e:
            logging.error(f"Error syncing trades from MT5: {str(e)}")
            return 0
    
    def get_trade_history(self, days=None, symbol=None, limit=None):
        """Get trade history from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            
            query = '''
                SELECT ticket, order_ticket, symbol, type, volume, open_price, close_price,
                       open_time, close_time, profit, commission, swap, magic, comment, duration_seconds
                FROM trade_history
                WHERE 1=1
            '''
            
            params = []
            
            if days:
                query += " AND close_time >= datetime('now', '-{} days')".format(days)
            
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            
            query += " ORDER BY close_time DESC"
            
            if limit:
                query += " LIMIT ?"
                params.append(limit)
            
            cursor = conn.cursor()
            cursor.execute(query, params)
            
            trades = []
            for row in cursor.fetchall():
                trades.append({
                    'ticket': row[0],
                    'order_ticket': row[1],
                    'symbol': row[2],
                    'type': row[3],
                    'volume': row[4],
                    'open_price': row[5],
                    'close_price': row[6],
                    'open_time': row[7],
                    'close_time': row[8],
                    'profit': row[9],
                    'commission': row[10],
                    'swap': row[11],
                    'magic': row[12],
                    'comment': row[13],
                    'duration_seconds': row[14]
                })
            
            conn.close()
            return trades
            
        except Exception as e:
            logging.error(f"Error getting trade history: {str(e)}")
            return []
    
    def get_trade_statistics(self, days=None):
        """Calculate trade statistics from history"""
        trades = self.get_trade_history(days=days)
        
        if not trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'total_profit': 0,
                'total_loss': 0,
                'net_profit': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'avg_duration': 0
            }
        
        winning_trades = [t for t in trades if t['profit'] > 0]
        losing_trades = [t for t in trades if t['profit'] < 0]
        
        total_profit = sum(t['profit'] for t in winning_trades)
        total_loss = abs(sum(t['profit'] for t in losing_trades))
        
        avg_duration = sum(t['duration_seconds'] for t in trades) / len(trades)
        
        return {
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': len(winning_trades) / len(trades) * 100,
            'total_profit': total_profit,
            'total_loss': total_loss,
            'net_profit': total_profit - total_loss,
            'avg_win': total_profit / len(winning_trades) if winning_trades else 0,
            'avg_loss': total_loss / len(losing_trades) if losing_trades else 0,
            'profit_factor': total_profit / total_loss if total_loss > 0 else 0,
            'avg_duration': avg_duration
        }
    
    def export_to_csv(self, filename="trade_history.csv", days=None):
        """Export trade history to CSV"""
        import pandas as pd
        
        trades = self.get_trade_history(days=days)
        
        if not trades:
            logging.warning("No trades to export")
            return False
        
        try:
            df = pd.DataFrame(trades)
            df.to_csv(filename, index=False)
            logging.info(f"Exported {len(trades)} trades to {filename}")
            return True
            
        except Exception as e:
            logging.error(f"Error exporting to CSV: {str(e)}")
            return False