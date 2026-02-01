import MetaTrader5 as mt5
import time

# Initialize
if not mt5.initialize():
    print("MT5 init failed")
    quit()

# Login
account = 356000
password = "nag#IS5R1"
server = "FinexBisnisSolusi-Demo"

if not mt5.login(account, password=password, server=server):
    print(f"Login failed: {mt5.last_error()}")
    mt5.shutdown()
    quit()

print("✓ Connected successfully\n")

# SYMBOL YANG BENAR
symbol = "EURUSD.s"

print("="*60)
print(f"Testing: {symbol}")
print("="*60)

symbol_info = mt5.symbol_info(symbol)

if symbol_info is None:
    print(f"❌ Symbol {symbol} not found")
    mt5.shutdown()
    quit()

# Check if visible
if not symbol_info.visible:
    print(f"Symbol not visible, enabling...")
    if not mt5.symbol_select(symbol, True):
        print(f"❌ Failed to enable symbol")
        mt5.shutdown()
        quit()

# Get tick
tick = mt5.symbol_info_tick(symbol)
if tick is None:
    print(f"❌ Failed to get tick")
    mt5.shutdown()
    quit()

# Print info
print(f"Bid: {tick.bid:.5f}")
print(f"Ask: {tick.ask:.5f}")
print(f"Spread: {tick.ask - tick.bid:.5f}")
print(f"Trade Mode: {symbol_info.trade_mode} ✅ ENABLED")
print(f"Filling Mode: {symbol_info.filling_mode}")
print(f"Min Volume: {symbol_info.volume_min}")
print(f"Max Volume: {symbol_info.volume_max}")
print(f"Volume Step: {symbol_info.volume_step}")

# PERBAIKAN 1: Filling mode yang benar
# Filling Mode 3 = FOK + IOC (bitmask)
# Bit 0 (1) = FOK
# Bit 1 (2) = IOC
# Bit 0+1 (3) = FOK or IOC

filling_mode = symbol_info.filling_mode

if filling_mode & 1:  # Support FOK
    filling_type = mt5.ORDER_FILLING_FOK
    print(f"\nUsing: ORDER_FILLING_FOK")
elif filling_mode & 2:  # Support IOC
    filling_type = mt5.ORDER_FILLING_IOC
    print(f"\nUsing: ORDER_FILLING_IOC")
else:
    filling_type = mt5.ORDER_FILLING_RETURN
    print(f"\nUsing: ORDER_FILLING_RETURN")

# PERBAIKAN 2: Lot size yang benar
lot = symbol_info.volume_min  # 0.1
print(f"Using lot size: {lot}")

# Prepare order
point = symbol_info.point
price = tick.ask

request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": symbol,
    "volume": lot,
    "type": mt5.ORDER_TYPE_BUY,
    "price": price,
    "sl": price - 200 * point,
    "tp": price + 300 * point,
    "deviation": 20,
    "magic": 234000,
    "comment": "Python test",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": filling_type,
}

print("\nSending order...")

# Send order
result = mt5.order_send(request)

if result is None:
    print(f"❌ Order send failed: {mt5.last_error()}")
    mt5.shutdown()
    quit()

print(f"\nResult code: {result.retcode}")

if result.retcode == mt5.TRADE_RETCODE_DONE:
    print("\n" + "="*60)
    print("🎉🎉🎉 ORDER SUCCESSFUL! 🎉🎉🎉")
    print("="*60)
    print(f"Order ID: {result.order}")
    print(f"Deal ID: {result.deal}")
    print(f"Volume: {result.volume}")
    print(f"Price: {result.price:.5f}")
    print("="*60)
    
    # Wait 3 seconds
    print("\nWaiting 3 seconds before closing...")
    time.sleep(3)
    
    # Close position
    positions = mt5.positions_get(symbol=symbol)
    if positions and len(positions) > 0:
        pos = positions[0]
        
        print(f"\nClosing position {pos.ticket}...")
        
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if pos.type == 0 else tick.ask,
            "deviation": 20,
            "magic": 234000,
            "comment": "Close test",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_type,
        }
        
        close_result = mt5.order_send(close_request)
        if close_result and close_result.retcode == mt5.TRADE_RETCODE_DONE:
            print("✅ Position closed!")
            print(f"Profit/Loss: ${pos.profit:.2f}")
        else:
            print("⚠️ Failed to close - close manually in MT5")
else:
    print(f"\n❌ Order failed!")
    print(f"Error code: {result.retcode}")
    print(f"Comment: {result.comment}")

mt5.shutdown()
print("\nTest completed!")