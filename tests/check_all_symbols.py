import MetaTrader5 as mt5

mt5.initialize()
mt5.login(356000, password="nag#IS5R1", server="FinexBisnisSolusi-Demo")

print("\n" + "="*70)
print("CHECKING ALL SYMBOLS FOR API TRADING")
print("="*70)

all_symbols = mt5.symbols_get()
enabled_symbols = []
disabled_symbols = []

for s in all_symbols:
    if s.trade_mode == 0:
        disabled_symbols.append(s.name)
    else:
        enabled_symbols.append(s.name)

print(f"\n✓ ENABLED for API Trading ({len(enabled_symbols)}):")
if enabled_symbols:
    for sym in enabled_symbols[:20]:  # Show first 20
        print(f"  - {sym}")
    if len(enabled_symbols) > 20:
        print(f"  ... and {len(enabled_symbols) - 20} more")
else:
    print("  ❌ NONE - ALL SYMBOLS DISABLED!")

print(f"\n✗ DISABLED for API Trading ({len(disabled_symbols)}):")
print(f"  (Total: {len(disabled_symbols)} symbols)")

print("\n" + "="*70)
print("ACCOUNT & TERMINAL INFO:")
print("="*70)

terminal = mt5.terminal_info()
account = mt5.account_info()

print(f"Terminal Trade Allowed: {terminal.trade_allowed}")
print(f"Terminal Trade Expert: {not terminal.tradeapi_disabled}")
print(f"Account Trade Allowed: {account.trade_allowed}")
print(f"Account Trade Expert: {account.trade_expert}")
print("="*70)

mt5.shutdown()