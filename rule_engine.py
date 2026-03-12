import MetaTrader5 as mt5
import pandas as pd
import json
import time
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
STOP_SIGNAL_FILE = "config/.stop_rule_engine"
BOT_STATE_FILE   = "config/.rule_engine_state.json"
# ══════════════════════════════════════════════════════════════
# LOAD CONFIG
# ══════════════════════════════════════════════════════════════
CONFIG = "liquidity_matrix_v21_5.json"
with open(CONFIG) as f:
    cfg = json.load(f)

console = Console()

# ══════════════════════════════════════════════════════════════
# CONNECT MT5
# ══════════════════════════════════════════════════════════════
if not mt5.initialize():
    console.print("[red]GAGAL konek ke MT5[/red]")
    quit()

# ══════════════════════════════════════════════════════════════
# SYMBOL DETECT  (baca dari engine.symbol + auto_suffix)
# ══════════════════════════════════════════════════════════════
def detect_symbol():
    base = cfg["engine"]["symbol"]          # "XAUUSD"
    auto_suffix = cfg["broker"]["auto_suffix"]
    symbols = mt5.symbols_get()
    for s in symbols:
        if base in s.name:
            return s.name
    # fallback kalau tidak ketemu
    return base

symbol = detect_symbol()
mt5.symbol_select(symbol, True)

# ══════════════════════════════════════════════════════════════
# BACA SEMUA CONFIG DARI JSON
# ══════════════════════════════════════════════════════════════
lot           = cfg["lot"]["size"]                      # 0.01
lot_mode      = cfg["lot"]["mode"]                      # "fixed"
atr_period    = cfg["volatility"]["atr_period"]         # 14
liq_lookback  = cfg["liquidity"]["lookback"]            # 150
spread_limit  = cfg["filters"]["spread_limit"]          # 22
vol_min       = cfg["filters"]["volatility_min"]        # 120
vol_limit     = cfg["filters"]["volatility_limit"]      # 380
grid_enabled  = cfg["grid"]["enabled"]                  # true
max_levels    = cfg["grid"]["max_levels"]               # 3
atr_mult      = cfg["grid"]["atr_multiplier"]           # 1.1
tp_min        = cfg["dynamic_target"]["min"]            # 200
tp_max        = cfg["dynamic_target"]["max"]            # 1000
max_dd        = cfg["risk"]["max_dd"]                   # 0.018
profit_lock   = cfg["risk"]["profit_lock"]              # 600

# ══════════════════════════════════════════════════════════════
# BROKER TIME
# ══════════════════════════════════════════════════════════════
def broker_time():
    tick = mt5.symbol_info_tick(symbol)
    return datetime.fromtimestamp(tick.time)

# ══════════════════════════════════════════════════════════════
# SESSION FILTER  (baca session.windows dari JSON)
# ══════════════════════════════════════════════════════════════
def session_allowed():
    now = broker_time().strftime("%H:%M")
    for w in cfg["session"]["windows"]:
        if w[0] <= now <= w[1]:
            return True
    return False

# ══════════════════════════════════════════════════════════════
# MARKET DATA
# ══════════════════════════════════════════════════════════════
def get_rates():
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 600)
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index("time", inplace=True)
    return df

# ══════════════════════════════════════════════════════════════
# ATR  (pakai atr_period dari JSON)
# ══════════════════════════════════════════════════════════════
def ATR(df):
    tr = df['high'] - df['low']
    return tr.rolling(atr_period).mean().iloc[-1]

# ══════════════════════════════════════════════════════════════
# VOLATILITY REGIME  (pakai vol_min & vol_limit dari JSON)
# ══════════════════════════════════════════════════════════════
def volatility_regime(df):
    atr = ATR(df)
    if atr < vol_min:
        return "LOW", atr
    if atr > vol_limit:
        return "EXTREME", atr
    return "NORMAL", atr

# ══════════════════════════════════════════════════════════════
# SPREAD FILTER  (pakai spread_limit dari JSON)
# ══════════════════════════════════════════════════════════════
def spread_ok():
    tick   = mt5.symbol_info_tick(symbol)
    point  = 0.01  # untuk XAUUSD, 1 point = 0.1 pip = 0.0001 price
    spread = (tick.ask - tick.bid) / point
    return spread < spread_limit, spread
# ══════════════════════════════════════════════════════════════
# GRID CHECK  (pakai max_levels dari JSON)
# ══════════════════════════════════════════════════════════════
def jumlah_order_terbuka():
    positions = mt5.positions_get(symbol=symbol)
    return len(positions) if positions else 0

def boleh_buka_order():
    if not grid_enabled:
        # kalau grid disabled, max 1 order saja
        return jumlah_order_terbuka() == 0
    return jumlah_order_terbuka() < max_levels

# ══════════════════════════════════════════════════════════════
# DRAWDOWN CHECK  (pakai max_dd dari JSON)
# ══════════════════════════════════════════════════════════════
def drawdown_aman():
    acc = mt5.account_info()
    if not acc or acc.balance <= 0:
        return True
    dd = (acc.balance - acc.equity) / acc.balance
    return dd < max_dd

# ══════════════════════════════════════════════════════════════
# PROFIT LOCK CHECK  (pakai profit_lock dari JSON)
# ══════════════════════════════════════════════════════════════
def profit_terkunci():
    acc = mt5.account_info()
    if not acc:
        return False
    # kalau floating profit sudah >= profit_lock → stop buka order baru
    return acc.profit >= profit_lock

# ══════════════════════════════════════════════════════════════
# SL / TP CALCULATOR  (pakai atr_mult & dynamic_target dari JSON)
# Skala TP antara tp_min dan tp_max berdasarkan posisi ATR
# ══════════════════════════════════════════════════════════════
def hitung_sl_tp(direction, price, atr):
    sym_info = mt5.symbol_info(symbol)
    point    = sym_info.point
    digits   = sym_info.digits

    sl_dist = atr * atr_mult

    # Scale TP: makin tinggi ATR (volatile) → TP makin besar
    t        = max(0.0, min((atr - vol_min) / max(vol_limit - vol_min, 1), 1.0))
    tp_pts   = tp_min + int(t * (tp_max - tp_min))
    tp_dist  = tp_pts * point

    if direction == "BUY":
        sl = round(price - sl_dist, digits)
        tp = round(price + tp_dist, digits)
    else:
        sl = round(price + sl_dist, digits)
        tp = round(price - tp_dist, digits)

    return sl, tp, tp_pts

# ══════════════════════════════════════════════════════════════
# BREAKOUT  (Asian session 00:00–06:00)
# ══════════════════════════════════════════════════════════════
def breakout(df):
    asian = df.between_time("00:00", "06:00")
    if asian.empty:
        return None, None
    high  = asian['high'].max()
    low   = asian['low'].min()
    price = df['close'].iloc[-1]
    if price > high:
        return "BUY", f"Breakout Asian High ({high:.2f})"
    if price < low:
        return "SELL", f"Breakout Asian Low ({low:.2f})"
    return None, None

# ══════════════════════════════════════════════════════════════
# LIQUIDITY SWEEP  (pakai liq_lookback dari JSON)
# ══════════════════════════════════════════════════════════════
def liquidity(df):
    h = df['high'].tail(liq_lookback).max()
    l = df['low'].tail(liq_lookback).min()
    p = df['close'].iloc[-1]
    if p > h:
        return "SELL", f"Liquidity Sweep High ({h:.2f})"
    if p < l:
        return "BUY", f"Liquidity Sweep Low ({l:.2f})"
    return None, None

# Tambahkan fungsi ini
def save_bot_status():
    try:
        bot_state = {
            "running": True,
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "session_active": status["sesi"],
            "spread": status["spread_val"],
            "volatility": status["vol"],
            "atr": status["atr"],
            "signal": status["signal"],
            "reason": status["reason"],
            "last_order": status["order"],
            "profit_locked": status["profit_locked"],
            "dd_aman": status["dd_aman"],
            "open_orders": jumlah_order_terbuka(),
            "max_levels": max_levels,
            "logs": status["logs"][-10:]
        }
        with open("config/.rule_engine_state.json", "w") as f:
            json.dump(bot_state, f, indent=2)
    except:
        pass
# ══════════════════════════════════════════════════════════════
# SEND ORDER  (dengan SL/TP otomatis)
# ══════════════════════════════════════════════════════════════
def send_order(direction, atr):
    tick  = mt5.symbol_info_tick(symbol)
    price = tick.ask if direction == "BUY" else tick.bid
    sl, tp, tp_pts = hitung_sl_tp(direction, price, atr)

    request = {
        "action":    mt5.TRADE_ACTION_DEAL,
        "symbol":    symbol,
        "volume":    lot,
        "type":      mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
        "price":     price,
        "sl":        sl,
        "tp":        tp,
        "deviation": 10,
        "magic":     21500,
        "comment":   f"LME_v21 TP={tp_pts}pts"
    }

    result = mt5.order_send(request)
    return result, sl, tp, tp_pts

# ══════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════
def build_dashboard(status: dict) -> Panel:
    acc       = mt5.account_info()
    balance   = f"${acc.balance:,.2f}"  if acc else "N/A"
    equity    = f"${acc.equity:,.2f}"   if acc else "N/A"
    floating  = f"${acc.profit:,.2f}"   if acc else "N/A"
    drawdown  = f"{((acc.balance - acc.equity) / acc.balance * 100):.2f}%" if acc and acc.balance > 0 else "N/A"

    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    broker_t = broker_time().strftime("%H:%M:%S")

    open_pos = jumlah_order_terbuka()

    sesi_color   = "green"  if status.get("sesi")       else "red"
    spread_color = "green"  if status.get("spread_ok")  else "red"
    vol          = status.get("vol", "-")
    vol_color    = "green"  if vol == "NORMAL" else ("yellow" if vol == "EXTREME" else "red")
    sig_color    = "cyan"   if status.get("signal")     else "white"
    grid_color   = "green"  if open_pos < max_levels    else "red"
    dd_color     = "green"  if status.get("dd_aman", True) else "red"

    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold white", width=22)
    t.add_column(width=28)
    t.add_column(style="bold white", width=22)
    t.add_column(width=22)

    t.add_row("🕐 Waktu Lokal",   now,          "💰 Balance",   balance)
    t.add_row("🕐 Waktu Broker",  broker_t,      "📊 Equity",    equity)
    t.add_row("📈 Symbol",        symbol,         "📉 Floating",  floating)
    t.add_row("📦 Lot",           f"{lot} ({lot_mode})", "⚠️ Drawdown", f"[{dd_color}]{drawdown}[/{dd_color}]")
    t.add_row("", "", "", "")

    t.add_row(
        "🟢 Sesi Trading",
        f"[{sesi_color}]{'AKTIF' if status.get('sesi') else 'DI LUAR SESI'}[/{sesi_color}]",
        "📡 Spread",
        f"[{spread_color}]{status.get('spread_val', 0):.1f} / {spread_limit} pts[/{spread_color}]"
    )
    t.add_row(
        "⚡ Volatility",
        f"[{vol_color}]{vol}[/{vol_color}] (ATR={status.get('atr', 0):.2f})",
        "🔢 Grid",
        f"[{grid_color}]{open_pos} / {max_levels} order[/{grid_color}]"
    )
    t.add_row(
        "🎯 Sinyal",
        f"[{sig_color}]{status.get('signal', 'Tidak ada')}[/{sig_color}]",
        "📋 Alasan",
        f"[yellow]{status.get('reason', '-')}[/yellow]"
    )
    t.add_row(
        "📤 Order Terakhir",
        f"[cyan]{status.get('order', '-')}[/cyan]",
        "🔒 Profit Lock",
        f"${profit_lock} (aktif={status.get('profit_locked', False)})"
    )
    t.add_row("", "", "", "")

    # Config summary
    t.add_row(
        "[bold]⚙️  Config JSON[/bold]",
        f"ATR={atr_period} | Lookback={liq_lookback}",
        "",
        f"TP {tp_min}–{tp_max} pts | MaxDD {max_dd*100:.1f}%"
    )
    t.add_row("", "", "", "")

    # Log
    t.add_row("[bold]📜 Log[/bold]", "", "", "")
    for log in status.get("logs", [])[-6:]:
        t.add_row("", log, "", "")

    return Panel(t, title="[bold cyan]🤖 LIQUIDITY MATRIX ENGINE v21.5 — XAUUSD[/bold cyan]", border_style="cyan")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
status = {
    "sesi": False, "spread_ok": False, "spread_val": 0,
    "vol": "-", "atr": 0, "signal": None,
    "reason": "-", "order": "-", "logs": [],
    "dd_aman": True, "profit_locked": False
}

def add_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    status["logs"].append(f"[{ts}] {msg}")
    if len(status["logs"]) > 30:
        status["logs"].pop(0)

console.print(f"[cyan]Bot dimulai — {cfg['engine']['name']} {cfg['engine']['version']}[/cyan]")

with Live(build_dashboard(status), refresh_per_second=1, screen=True) as live:
    while True:
        try:
            # Reset per cycle
            status["signal"] = None
            status["order"]  = "-"
            status["reason"] = "-"

            # ── 1. Cek sesi ──────────────────────────────────────
            in_session = session_allowed()
            status["sesi"] = in_session
            if not in_session:
                status["reason"] = "Di luar jam trading"
                add_log("⏸ Di luar sesi — menunggu...")
                live.update(build_dashboard(status))
                time.sleep(5)
                continue

            # ── 2. Ambil data ────────────────────────────────────
            df = get_rates()
            add_log(f"📥 Data M1 diambil (600 candle)")

            # ── 3. Cek spread ────────────────────────────────────
            ok, spread_val = spread_ok()
            status["spread_ok"]  = ok
            status["spread_val"] = spread_val
            if not ok:
                status["reason"] = f"Spread {spread_val:.1f} > limit {spread_limit}"
                add_log(f"❌ Spread terlalu lebar ({spread_val:.1f} pts) — skip")
                live.update(build_dashboard(status))
                time.sleep(5)
                continue

            # ── 4. Cek volatility regime ─────────────────────────
            regime, atr_val = volatility_regime(df)
            status["vol"] = regime
            status["atr"] = atr_val
            if regime == "LOW":
                status["reason"] = f"Volatility LOW (ATR={atr_val:.2f} < {vol_min})"
                add_log(f"😴 Volatility LOW — skip")
                live.update(build_dashboard(status))
                time.sleep(5)
                continue
            if regime == "EXTREME":
                status["reason"] = f"Volatility EXTREME (ATR={atr_val:.2f} > {vol_limit})"
                add_log(f"🔥 Volatility EXTREME — skip")
                live.update(build_dashboard(status))
                time.sleep(5)
                continue

            # ── 5. Cek drawdown ──────────────────────────────────
            dd_ok = drawdown_aman()
            status["dd_aman"] = dd_ok
            if not dd_ok:
                status["reason"] = f"Drawdown melebihi {max_dd*100:.1f}% — trading dihentikan"
                add_log(f"🛑 Max drawdown tercapai — stop trading")
                live.update(build_dashboard(status))
                time.sleep(5)
                continue

            # ── 6. Cek profit lock ───────────────────────────────
            locked = profit_terkunci()
            status["profit_locked"] = locked
            if locked:
                status["reason"] = f"Floating profit >= ${profit_lock} — profit dikunci"
                add_log(f"🔒 Profit lock ${profit_lock} tercapai — tidak buka order baru")
                live.update(build_dashboard(status))
                time.sleep(5)
                continue

            # ── 7. Cek grid (max order terbuka) ─────────────────
            if not boleh_buka_order():
                status["reason"] = f"Grid penuh ({jumlah_order_terbuka()}/{max_levels} order)"
                add_log(f"🔢 Grid penuh {jumlah_order_terbuka()}/{max_levels} — skip")
                live.update(build_dashboard(status))
                time.sleep(5)
                continue

            # ── 8. Cek sinyal ────────────────────────────────────
            sig, reason = breakout(df)
            if sig:
                status["signal"] = sig
                status["reason"] = reason
                add_log(f"🚀 Breakout {sig} — {reason}")
            else:
                sig, reason = liquidity(df)
                if sig:
                    status["signal"] = sig
                    status["reason"] = reason
                    add_log(f"💧 Liquidity {sig} — {reason}")
                else:
                    status["reason"] = "Menunggu sinyal..."
                    add_log(f"🔍 Tidak ada sinyal")

            # ── 9. Kirim order ───────────────────────────────────
            if sig:
                result, sl, tp, tp_pts = send_order(sig, atr_val)
                if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                    status["order"] = f"✅ {sig} #{result.order} | SL={sl} TP={tp} ({tp_pts}pts)"
                    add_log(f"✅ {sig} berhasil #{result.order} | SL={sl} | TP={tp} ({tp_pts}pts)")
                else:
                    code = result.retcode if result else "N/A"
                    status["order"] = f"❌ Gagal (retcode={code})"
                    add_log(f"❌ Order gagal — retcode={code}")

            live.update(build_dashboard(status))
            time.sleep(5)

        except Exception as e:
            add_log(f"⚠️ ERROR: {e}")
            live.update(build_dashboard(status))
            time.sleep(5)