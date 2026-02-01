import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta

def export_trades_to_excel(days=7):
    """Export trading history to Excel"""
    
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
    
    print(f"✓ Connected to MT5")
    print(f"Exporting trades from last {days} days...")
    
    # Get deals
    from_date = datetime.now() - timedelta(days=days)
    to_date = datetime.now()
    
    deals = mt5.history_deals_get(from_date, to_date)
    
    if deals is None or len(deals) == 0:
        print("No deals found")
        mt5.shutdown()
        return
    
    # Convert to DataFrame
    deals_list = []
    for deal in deals:
        deals_list.append({
            'Time': datetime.fromtimestamp(deal.time),
            'Ticket': deal.ticket,
            'Order': deal.order,
            'Symbol': deal.symbol,
            'Type': 'BUY' if deal.type == 0 else 'SELL',
            'Entry': 'IN' if deal.entry == 0 else 'OUT',
            'Volume': deal.volume,
            'Price': deal.price,
            'Commission': deal.commission,
            'Swap': deal.swap,
            'Profit': deal.profit,
            'Comment': deal.comment
        })
    
    df = pd.DataFrame(deals_list)
    
    # Get orders
    orders = mt5.history_orders_get(from_date, to_date)
    orders_list = []
    
    if orders:
        for order in orders:
            orders_list.append({
                'Time Setup': datetime.fromtimestamp(order.time_setup),
                'Time Done': datetime.fromtimestamp(order.time_done),
                'Ticket': order.ticket,
                'Symbol': order.symbol,
                'Type': 'BUY' if order.type == 0 else 'SELL',
                'Volume Requested': order.volume_initial,
                'Volume Executed': order.volume_current,
                'Price': order.price_open,
                'SL': order.sl,
                'TP': order.tp,
                'State': order.state,
                'Comment': order.comment
            })
    
    df_orders = pd.DataFrame(orders_list)
    
    # Calculate statistics
    closed_trades = df[df['Entry'] == 'OUT'].copy()
    
    if len(closed_trades) > 0:
        stats = {
            'Total Trades': len(closed_trades),
            'Winning Trades': len(closed_trades[closed_trades['Profit'] > 0]),
            'Losing Trades': len(closed_trades[closed_trades['Profit'] < 0]),
            'Win Rate (%)': len(closed_trades[closed_trades['Profit'] > 0]) / len(closed_trades) * 100,
            'Total Profit': closed_trades['Profit'].sum(),
            'Total Commission': closed_trades['Commission'].sum(),
            'Total Swap': closed_trades['Swap'].sum(),
            'Net Profit': closed_trades['Profit'].sum() + closed_trades['Commission'].sum() + closed_trades['Swap'].sum(),
            'Average Win': closed_trades[closed_trades['Profit'] > 0]['Profit'].mean() if len(closed_trades[closed_trades['Profit'] > 0]) > 0 else 0,
            'Average Loss': closed_trades[closed_trades['Profit'] < 0]['Profit'].mean() if len(closed_trades[closed_trades['Profit'] < 0]) > 0 else 0,
            'Largest Win': closed_trades['Profit'].max(),
            'Largest Loss': closed_trades['Profit'].min(),
        }
        
        df_stats = pd.DataFrame([stats])
    else:
        df_stats = pd.DataFrame()
    
    # Export to Excel
    filename = f"trading_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Deals', index=False)
        df_orders.to_excel(writer, sheet_name='Orders', index=False)
        if not df_stats.empty:
            df_stats.to_excel(writer, sheet_name='Statistics', index=False)
    
    print(f"✅ Report exported: {filename}")
    
    # Print summary
    if not df_stats.empty:
        print("\n" + "="*60)
        print("TRADING SUMMARY")
        print("="*60)
        for key, value in stats.items():
            if isinstance(value, float):
                print(f"{key:.<40} {value:.2f}")
            else:
                print(f"{key:.<40} {value}")
        print("="*60)
    
    mt5.shutdown()

if __name__ == "__main__":
    export_trades_to_excel(days=7)  # Export last 7 days