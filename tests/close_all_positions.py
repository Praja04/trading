import MetaTrader5 as mt5
from datetime import datetime

def close_all_positions():
    """Close all open positions"""
    
    # Initialize MT5
    if not mt5.initialize():
        print("❌ MT5 initialization failed")
        return
    
    # Login
    account = 356000
    password = "nag#IS5R1"
    server = "FinexBisnisSolusi-Demo"
    
    if not mt5.login(account, password=password, server=server):
        print(f"❌ Login failed: {mt5.last_error()}")
        mt5.shutdown()
        return
    
    print("✓ Connected to MT5")
    print()
    
    # Get all open positions
    positions = mt5.positions_get()
    
    if positions is None or len(positions) == 0:
        print("ℹ️  No open positions to close")
        mt5.shutdown()
        return
    
    print(f"Found {len(positions)} open position(s)")
    print("="*70)
    
    total_profit = 0
    closed_count = 0
    failed_count = 0
    
    for pos in positions:
        print(f"\nClosing position:")
        print(f"  Ticket: {pos.ticket}")
        print(f"  Symbol: {pos.symbol}")
        print(f"  Type: {'BUY' if pos.type == 0 else 'SELL'}")
        print(f"  Volume: {pos.volume}")
        print(f"  Open Price: {pos.price_open:.5f}")
        print(f"  Current Profit: ${pos.profit:.2f}")
        
        # Get symbol info
        symbol_info = mt5.symbol_info(pos.symbol)
        if symbol_info is None:
            print(f"  ❌ Failed to get symbol info")
            failed_count += 1
            continue
        
        # Get current tick
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            print(f"  ❌ Failed to get tick")
            failed_count += 1
            continue
        
        # Determine filling mode
        filling_mode = symbol_info.filling_mode
        if filling_mode & 1:
            type_filling = mt5.ORDER_FILLING_FOK
        elif filling_mode & 2:
            type_filling = mt5.ORDER_FILLING_IOC
        else:
            type_filling = mt5.ORDER_FILLING_RETURN
        
        # Prepare close request
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if pos.type == 0 else tick.ask
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "price": close_price,
            "deviation": 20,
            "magic": 234000,
            "comment": "Close all positions",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": type_filling,
        }
        
        # Send close order
        result = mt5.order_send(request)
        
        if result is None:
            print(f"  ❌ Failed to send close order: {mt5.last_error()}")
            failed_count += 1
            continue
        
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"  ✅ Position closed successfully!")
            print(f"  Close Price: {result.price:.5f}")
            print(f"  Final Profit: ${pos.profit:.2f}")
            closed_count += 1
            total_profit += pos.profit
        else:
            print(f"  ❌ Failed to close: {result.retcode} - {result.comment}")
            failed_count += 1
    
    print()
    print("="*70)
    print("📊 SUMMARY:")
    print("="*70)
    print(f"Total Positions: {len(positions)}")
    print(f"Successfully Closed: {closed_count}")
    print(f"Failed to Close: {failed_count}")
    print(f"Total Profit/Loss: ${total_profit:.2f}")
    print("="*70)
    
    mt5.shutdown()

if __name__ == "__main__":
    print("="*70)
    print("🔴 CLOSE ALL POSITIONS")
    print("="*70)
    
    # Confirmation
    confirm = input("\n⚠️  Are you sure you want to close ALL positions? (yes/no): ")
    
    if confirm.lower() in ['yes', 'y']:
        print("\nClosing all positions...\n")
        close_all_positions()
    else:
        print("\n❌ Operation cancelled")