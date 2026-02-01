import MetaTrader5 as mt5
from datetime import datetime
import time
import os

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def format_currency(value):
    return f"${value:,.2f}"

def get_all_positions():
    """Get all open positions with details"""
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
            'open_time': datetime.fromtimestamp(pos.time),
            'comment': pos.comment,
            'swap': pos.swap,
            'magic': pos.magic
        })
    
    return position_list

def get_closed_trades_today():
    """Get all closed trades today"""
    from_date = datetime.now().replace(hour=0, minute=0, second=0)
    to_date = datetime.now()
    
    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None:
        return []
    
    # Filter only closing deals (DEAL_ENTRY_OUT)
    trades = []
    for deal in deals:
        if deal.entry == 1:  # DEAL_ENTRY_OUT = closing deal
            trades.append({
                'ticket': deal.ticket,
                'order': deal.order,
                'symbol': deal.symbol,
                'type': 'BUY' if deal.type == 0 else 'SELL',
                'volume': deal.volume,
                'price': deal.price,
                'profit': deal.profit,
                'commission': deal.commission,
                'swap': deal.swap,
                'time': datetime.fromtimestamp(deal.time),
                'comment': deal.comment
            })
    
    return trades

def print_dashboard():
    """Print trading dashboard"""
    clear_screen()
    
    print("="*80)
    print("📊 TRADING BOT DASHBOARD")
    print("="*80)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Account info
    account = mt5.account_info()
    if account:
        profit_color = "🟢" if account.profit >= 0 else "🔴"
        print("💰 ACCOUNT INFO:")
        print("-"*80)
        print(f"Balance:       {format_currency(account.balance)}")
        print(f"Equity:        {format_currency(account.equity)}")
        print(f"Margin:        {format_currency(account.margin)}")
        print(f"Free Margin:   {format_currency(account.margin_free)}")
        print(f"Margin Level:  {account.margin_level:.2f}%" if account.margin > 0 else "Margin Level:  N/A")
        print(f"Profit:        {profit_color} {format_currency(account.profit)}")
        print()
    
    # Open positions
    positions = get_all_positions()
    print(f"📈 OPEN POSITIONS ({len(positions)}):")
    print("-"*80)
    
    if positions:
        total_profit = 0
        total_swap = 0
        
        for pos in positions:
            status = "🟢" if pos['profit'] >= 0 else "🔴"
            duration = datetime.now() - pos['open_time']
            hours = int(duration.total_seconds() / 3600)
            minutes = int((duration.total_seconds() % 3600) / 60)
            
            # Calculate pips
            if 'JPY' in pos['symbol']:
                pips = (pos['current_price'] - pos['open_price']) * 100 if pos['type'] == 'BUY' else (pos['open_price'] - pos['current_price']) * 100
            else:
                pips = (pos['current_price'] - pos['open_price']) * 10000 if pos['type'] == 'BUY' else (pos['open_price'] - pos['current_price']) * 10000
            
            print(f"{status} Ticket: {pos['ticket']} | Magic: {pos['magic']}")
            print(f"   Symbol: {pos['symbol']:12} | Type: {pos['type']:4} | Volume: {pos['volume']:.2f}")
            print(f"   Open:   {pos['open_price']:.5f} | Current: {pos['current_price']:.5f} | Pips: {pips:+.1f}")
            print(f"   SL:     {pos['sl']:.5f} | TP: {pos['tp']:.5f}")
            print(f"   Profit: {format_currency(pos['profit']):>12} | Swap: {format_currency(pos['swap']):>10}")
            print(f"   Duration: {hours}h {minutes}m | Opened: {pos['open_time'].strftime('%H:%M:%S')}")
            if pos['comment']:
                print(f"   Comment: {pos['comment']}")
            print()
            
            total_profit += pos['profit']
            total_swap += pos['swap']
        
        print(f"{'Total Profit:':>50} {format_currency(total_profit)}")
        print(f"{'Total Swap:':>50} {format_currency(total_swap)}")
        print(f"{'Net Unrealized P&L:':>50} {format_currency(total_profit + total_swap)}")
    else:
        print("   No open positions")
    
    print()
    
    # Closed trades today
    trades = get_closed_trades_today()
    print(f"📋 CLOSED TRADES TODAY ({len(trades)}):")
    print("-"*80)
    
    if trades:
        winning_trades = [t for t in trades if t['profit'] > 0]
        losing_trades = [t for t in trades if t['profit'] < 0]
        
        total_profit = sum(t['profit'] for t in trades)
        total_commission = sum(t['commission'] for t in trades)
        total_swap = sum(t['swap'] for t in trades)
        net_profit = total_profit + total_commission + total_swap
        
        # Show last 15 trades
        for trade in sorted(trades, key=lambda x: x['time'], reverse=True)[:15]:
            status = "✅" if trade['profit'] >= 0 else "❌"
            net = trade['profit'] + trade['commission'] + trade['swap']
            print(f"{status} {trade['time'].strftime('%H:%M:%S')} | {trade['symbol']:12} | "
                  f"{trade['type']:4} | Vol: {trade['volume']:.2f} | "
                  f"Price: {trade['price']:.5f} | "
                  f"Gross: {format_currency(trade['profit']):>10} | "
                  f"Net: {format_currency(net):>10}")
        
        if len(trades) > 15:
            print(f"   ... and {len(trades) - 15} more trades")
        
        print()
        win_rate = len(winning_trades)/len(trades)*100 if len(trades) > 0 else 0
        print(f"{'Winning Trades:':>40} {len(winning_trades)}")
        print(f"{'Losing Trades:':>40} {len(losing_trades)}")
        print(f"{'Win Rate:':>40} {win_rate:.1f}%")
        print(f"{'Gross Profit:':>40} {format_currency(total_profit)}")
        print(f"{'Total Commission:':>40} {format_currency(total_commission)}")
        print(f"{'Total Swap:':>40} {format_currency(total_swap)}")
        print(f"{'Net Profit Today:':>40} {format_currency(net_profit)}")
        
        if len(winning_trades) > 0:
            avg_win = sum(t['profit'] for t in winning_trades) / len(winning_trades)
            print(f"{'Average Win:':>40} {format_currency(avg_win)}")
        
        if len(losing_trades) > 0:
            avg_loss = sum(t['profit'] for t in losing_trades) / len(losing_trades)
            print(f"{'Average Loss:':>40} {format_currency(avg_loss)}")
    else:
        print("   No closed trades today")
    
    print("="*80)
    print("Press Ctrl+C to exit | Auto-refresh every 5 seconds")
    print("="*80)

def main():
    # Initialize MT5
    if not mt5.initialize():
        print("MT5 initialization failed")
        return
    
    # Login
    account = 356000
    password = "nag#IS5R1"
    server = "FinexBisnisSolusi-Demo"
    
    if not mt5.login(account, password=password, server=server):
        print(f"Login failed: {mt5.last_error()}")
        mt5.shutdown()
        return
    
    print("✓ Connected to MT5")
    print("Loading dashboard...")
    time.sleep(2)
    
    try:
        while True:
            print_dashboard()
            time.sleep(5)  # Update every 5 seconds
            
    except KeyboardInterrupt:
        print("\n\nDashboard closed")
    
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()