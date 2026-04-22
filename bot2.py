"""
V104 Trading Bot — bot2.py
===========================
- Data source  : MetaTrader 5 (mt5 library)
- Indicators   : BOS, EMA trend, ATR, spread check
- Risk          : Dynamic lot sizing with SL/TP (ATR-based)
- AI            : Adaptive threshold dengan proper win-rate calibration
- Output        : Execute orders ke MT5 demo account
- Config        : Baca dari config/broker.yaml (tidak hardcode credentials)
- Dashboard     : Tulis state ke config/.v104_state.json setiap cycle
"""

import json
import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import MetaTrader5 as mt5
import numpy as np
import yaml

# ─────────────────────────────────────────────
# PATH CONSTANTS  (relatif dari root project)
# ─────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR     = os.path.join(BASE_DIR, "config")
DATA_DIR       = os.path.join(BASE_DIR, "data")
LOGS_DIR       = os.path.join(BASE_DIR, "logs")

BROKER_YAML    = os.path.join(CONFIG_DIR, "broker.yaml")
V104_CONFIG    = os.path.join(BASE_DIR,   "V104_AI_CONFIG.json")
MEMORY_PATH    = os.path.join(BASE_DIR,   "AI_MEMORY.json")
LOG_FILE       = os.path.join(BASE_DIR,   "V104_bot.log")

# State files untuk dashboard
V104_STATE_FILE = os.path.join(CONFIG_DIR, ".v104_state.json")
V104_STOP_FILE  = os.path.join(CONFIG_DIR, ".stop_v104")

# Pastikan direktori ada
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,   exist_ok=True)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("V104")

# In-memory log buffer untuk dashboard
_v104_logs: list[str] = []


def v104_log(msg: str, level: str = "INFO") -> None:
    """Log ke file + terminal + buffer dashboard."""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    _v104_logs.append(line)
    if len(_v104_logs) > 200:
        _v104_logs.pop(0)
    getattr(log, level.lower(), log.info)(msg)


# ─────────────────────────────────────────────
# LOAD BROKER CREDENTIALS
# ─────────────────────────────────────────────
def load_broker_config() -> tuple[int, str, str]:
    """Baca login, password, server dari config/broker.yaml."""
    try:
        with open(BROKER_YAML, "r") as f:
            cfg = yaml.safe_load(f)
        broker = cfg.get("broker", {})
        login    = int(broker.get("account",  0))
        password = str(broker.get("password", ""))
        server   = str(broker.get("server",   ""))
        if not login or not password or not server:
            raise ValueError("broker.yaml tidak lengkap (account/password/server)")
        return login, password, server
    except FileNotFoundError:
        log.error("broker.yaml tidak ditemukan di %s", BROKER_YAML)
        raise
    except Exception as e:
        log.error("Gagal baca broker.yaml: %s", e)
        raise


# ─────────────────────────────────────────────
# LOAD CONFIG & MEMORY
# ─────────────────────────────────────────────
DEFAULT_CONFIG = {
    "version": "V104",
    "risk": {
        "base_lot": 0.03, "min_lot": 0.01, "max_lot": 0.10,
        "target_dd": 5.0, "cooldown_sec": 60
    },
    "pairs": {
        "core": ["XAUUSD"], "hedge": ["USDJPY"],
        "synthetic": ["EURUSD", "GBPUSD"], "booster": ["GBPJPY"]
    },
    "scoring": {
        "ema_align": 15, "bos": 20, "candle": 15, "atr": 15, "spread": 10
    },
    "filters": {
        "session_hours": [13, 22], "max_spread": 30,
        "atr_min_pips": 5, "atr_max_pips": 200
    },
    "sl_tp": {"atr_multiplier_sl": 1.5, "atr_multiplier_tp": 2.5},
    "logic": {
        "synthetic_enable_score": 60, "synthetic_dd_limit": 3.0,
        "hedge_dd_limit": 4.0, "booster_dd_limit": 4.0
    },
    "ai": {
        "threshold_default": 90, "threshold_min": 85, "threshold_max": 97,
        "learning_rate": 0.05, "min_trades_learn": 10,
        "wr_high": 0.60, "wr_low": 0.40, "adjust_step": 5
    }
}


def load_config() -> dict:
    """
    Baca V104_AI_CONFIG.json.
    - Kalau file tidak ada  → buat otomatis dari DEFAULT_CONFIG.
    - Kalau file kosong/corrupt → overwrite dengan DEFAULT_CONFIG + warning.
    """
    if not os.path.exists(V104_CONFIG):
        log.warning("V104_AI_CONFIG.json tidak ditemukan — membuat file default di %s", V104_CONFIG)
        with open(V104_CONFIG, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG

    try:
        with open(V104_CONFIG, encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            log.warning("V104_AI_CONFIG.json kosong — menulis ulang dengan default config.")
            with open(V104_CONFIG, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
            return DEFAULT_CONFIG

        return json.loads(content)

    except json.JSONDecodeError as e:
        log.error(
            "V104_AI_CONFIG.json corrupt (JSONDecodeError: %s) — "
            "backup ke V104_AI_CONFIG.bak.json dan tulis ulang.", e
        )
        # Backup file corrupt
        bak = V104_CONFIG.replace(".json", ".bak.json")
        try:
            import shutil
            shutil.copy2(V104_CONFIG, bak)
            log.info("Backup disimpan ke %s", bak)
        except Exception:
            pass
        with open(V104_CONFIG, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG


def load_memory() -> dict:
    try:
        with open(MEMORY_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"models": {}}


def save_memory(memory: dict) -> None:
    with open(MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)
    v104_log("Memory saved.")


# ─────────────────────────────────────────────
# DASHBOARD STATE WRITER
# ─────────────────────────────────────────────
def write_state(
    balance: float,
    dd: float,
    memory: dict,
    last_trades: list,
    signal: str = "",
    reason: str = "",
    running: bool = True,
) -> None:
    """
    Tulis status bot ke config/.v104_state.json setiap cycle.
    Dibaca oleh app.py → /api/v104/status → bot2_dashboard.html
    """
    try:
        positions = mt5.positions_get()
        open_orders = len(positions) if positions else 0

        state = {
            "running":      running,
            "balance":      round(balance, 2),
            "dd":           round(dd, 2),
            "open_orders":  open_orders,
            "signal":       signal,
            "reason":       reason,
            "pairs":        memory.get("models", {}),
            "last_trades":  last_trades[-10:] if last_trades else [],
            "logs":         _v104_logs[-40:],
            "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        with open(V104_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    except Exception as e:
        log.warning("write_state error: %s", e)


def check_stop_signal() -> bool:
    """
    Return True jika stop signal ada (dikirim dari dashboard /api/v104/stop).
    Hapus file setelah dibaca.
    """
    if os.path.exists(V104_STOP_FILE):
        try:
            os.remove(V104_STOP_FILE)
        except Exception:
            pass
        return True
    return False


# ─────────────────────────────────────────────
# MT5 CONNECTION
# ─────────────────────────────────────────────
def connect_mt5(login: int, password: str, server: str) -> bool:
    if not mt5.initialize():
        v104_log(f"MT5 initialize() failed: {mt5.last_error()}", "error")
        return False

    if not mt5.login(login, password=password, server=server):
        v104_log(f"MT5 login failed: {mt5.last_error()}", "error")
        mt5.shutdown()
        return False

    info = mt5.account_info()
    v104_log(
        f"Connected ✓ — Account: {info.login} | "
        f"Balance: {info.balance:.2f} | Leverage: 1:{info.leverage}"
    )
    return True


# ─────────────────────────────────────────────
# MARKET DATA HELPERS
# ─────────────────────────────────────────────
def resolve_symbol(symbol: str) -> Optional[str]:
    """
    Cari nama symbol yang valid di broker MT5.
    Exness biasanya pakai suffix 'm', '.', '#', dsb.
    Return nama symbol yang valid atau None.
    """
    # Coba as-is dulu
    info = mt5.symbol_info(symbol)
    if info is not None:
        if not info.visible:
            mt5.symbol_select(symbol, True)
        return symbol

    # Strip suffix yang umum lalu coba variasi
    base = symbol.upper()
    for suffix in ('.S', '.s', 'M', 'm', '.PRO', '.pro', '.ECN', '.ecn',
                   '.R', '.r', '#', '.', '.STD', '.STP', '.MINI'):
        if base.endswith(suffix.upper()):
            base = base[:-len(suffix)]
            break

    candidates = [
        base,
        base + 'm',
        base + 'M',
        base + '.s',
        base + '.S',
        base + '#',
        base + '.pro',
        base + '.PRO',
        base + '.ecn',
    ]

    for c in candidates:
        info = mt5.symbol_info(c)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(c, True)
            if c != symbol:
                log.info("Symbol resolved: %s → %s", symbol, c)
            return c

    # Fallback: scan semua symbol broker
    all_syms = mt5.symbols_get()
    if all_syms:
        for s in all_syms:
            clean = s.name.upper()
            for suffix in ('.S', 'M', '.PRO', '.ECN', '.R', '#', '.', '.STD', '.STP'):
                if clean.endswith(suffix):
                    clean = clean[:-len(suffix)]
                    break
            if clean == base:
                log.info("Symbol resolved (scan): %s → %s", symbol, s.name)
                mt5.symbol_select(s.name, True)
                return s.name

    log.error("Symbol not found in broker: %s (base=%s)", symbol, base)
    return None


# Cache resolved symbols supaya tidak resolve ulang setiap cycle
_symbol_cache: dict[str, str] = {}


def get_rates(
    symbol: str,
    timeframe=mt5.TIMEFRAME_H1,
    count: int = 100,
) -> Optional[np.ndarray]:
    """Fetch OHLCV bars dari MT5. Auto-resolve symbol name. Return numpy array atau None."""
    # Resolve symbol name (pakai cache)
    if symbol not in _symbol_cache:
        resolved = resolve_symbol(symbol)
        if resolved is None:
            return None
        _symbol_cache[symbol] = resolved

    sym = _symbol_cache[symbol]
    rates = mt5.copy_rates_from_pos(sym, timeframe, 0, count)
    if rates is None or len(rates) < 20:
        log.warning("Insufficient data for %s (resolved: %s) — bars=%s",
                    symbol, sym, len(rates) if rates is not None else 0)
        # Reset cache supaya dicoba resolve ulang cycle berikutnya
        _symbol_cache.pop(symbol, None)
        return None
    return rates


def get_spread_points(symbol: str) -> float:
    resolved = _symbol_cache.get(symbol, symbol)
    tick = mt5.symbol_info_tick(resolved)
    info = mt5.symbol_info(resolved)
    if tick is None or info is None:
        return 9999.0
    return (tick.ask - tick.bid) / info.point


# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────
def calc_atr(rates: np.ndarray, period: int = 14) -> float:
    """Average True Range. Ambil slice yang konsisten untuk menghindari shape mismatch."""
    n          = min(period + 1, len(rates))
    high       = rates["high"][-n:].astype(float)
    low        = rates["low"][-n:].astype(float)
    close      = rates["close"][-n:].astype(float)
    close_prev = close[:-1]   # n-1 elemen
    high_cur   = high[1:]     # n-1 elemen
    low_cur    = low[1:]      # n-1 elemen
    tr = np.maximum(
        high_cur - low_cur,
        np.maximum(
            np.abs(high_cur - close_prev),
            np.abs(low_cur  - close_prev),
        ),
    )
    return float(np.mean(tr))


def calc_ema(values: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1)
    ema = np.zeros(len(values))
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = values[i] * k + ema[i - 1] * (1 - k)
    return ema


def detect_bos(rates: np.ndarray, lookback: int = 10) -> int:
    """
    Break of Structure.
    +1 = bullish BOS, -1 = bearish BOS, 0 = none.
    """
    highs      = rates["high"][-lookback - 1:-1]
    lows       = rates["low"][-lookback - 1:-1]
    close      = rates["close"][-1]
    swing_high = float(np.max(highs))
    swing_low  = float(np.min(lows))

    if close > swing_high:
        return 1
    if close < swing_low:
        return -1
    return 0


def detect_candle_direction(rates: np.ndarray) -> int:
    """
    Arah candle terakhir yang sudah tutup.
    Filter: body harus > 40% dari total range.
    +1 = bullish, -1 = bearish, 0 = doji/ignored.
    """
    o     = rates["open"][-2]
    c     = rates["close"][-2]
    h     = rates["high"][-2]
    l     = rates["low"][-2]
    body  = abs(c - o)
    total = h - l
    if total == 0 or body / total < 0.4:
        return 0
    return 1 if c > o else -1


# ─────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────
def score_market(symbol: str, config: dict) -> tuple[int, int]:
    """
    Score pasar untuk `symbol`. Return (score, direction).
    direction: +1 = BUY, -1 = SELL, 0 = no bias.

    Breakdown (max 75):
      BOS detected    : +20
      Candle confirm  : +15
      ATR in range    : +15
      Spread OK       : +10
      EMA align       : +15

    CATATAN: threshold default di config harus ≤ 75.
    Kalau pakai threshold=90, bot tidak akan pernah trade.
    Rekomendasi: set threshold 50–65 untuk live trading.
    """
    sc   = config["scoring"]
    filt = config["filters"]

    # Gunakan resolved symbol dari cache kalau ada
    resolved = _symbol_cache.get(symbol, symbol)

    rates = get_rates(symbol)
    if rates is None:
        return 0, 0

    score     = 0
    direction = 0

    # BOS
    bos = detect_bos(rates)
    if bos != 0:
        score    += sc["bos"]
        direction = bos

    # Candle confirmation
    candle = detect_candle_direction(rates)
    if candle != 0 and candle == direction:
        score += sc["candle"]
    elif candle != 0 and direction == 0:
        direction = candle
        score += sc["candle"]

    # ATR filter
    atr  = calc_atr(rates)
    info = mt5.symbol_info(resolved)
    if info:
        atr_pips = atr / info.point / 10
        atr_min  = filt.get("atr_min_pips", 5)
        atr_max  = filt.get("atr_max_pips", 200)
        if atr_min < atr_pips < atr_max:
            score += sc["atr"]

    # Spread filter — support per-pair limit atau single value
    spread     = get_spread_points(resolved)
    max_spread = filt.get("max_spread", 50)
    if isinstance(max_spread, dict):
        base_sym = symbol.upper().rstrip("M").rstrip(".S")
        limit    = max_spread.get(symbol) or max_spread.get(base_sym) or max_spread.get("default", 50)
    else:
        limit = max_spread
    if spread < limit:
        score += sc["spread"]

    # EMA trend alignment
    closes   = rates["close"].astype(float)
    ema20    = calc_ema(closes, 20)
    ema50    = calc_ema(closes, 50)
    ema_bias = 1 if ema20[-1] > ema50[-1] else -1
    if ema_bias == direction:
        score += sc.get("ema_align", sc.get("session", 15))

    log.info(
        "%s | score=%d/75 dir=%+d | bos=%+d candle=%+d "
        "atr_pips=%.1f spread=%.0f ema_bias=%+d",
        symbol, score, direction, bos, candle,
        atr / (info.point * 10) if info else 0,
        spread, ema_bias,
    )
    return score, direction


# ─────────────────────────────────────────────
# LOT SIZING
# ─────────────────────────────────────────────
def auto_lot(balance: float, dd: float, config: dict) -> float:
    r   = config["risk"]
    lot = (balance / 1000.0) * r["base_lot"]

    if dd >= 4.0:
        lot *= 0.4
    elif dd >= 3.0:
        lot *= 0.6
    elif dd >= 2.0:
        lot *= 0.8

    return round(max(r["min_lot"], min(r["max_lot"], lot)), 2)


# ─────────────────────────────────────────────
# SL / TP
# ─────────────────────────────────────────────

# Dollar cap per-symbol: max loss & target profit dalam USD
# Bisa di-override lewat V104_AI_CONFIG.json di key "sl_tp.dollar_caps"
_DEFAULT_DOLLAR_CAPS: dict[str, dict] = {
    "XAUUSD": {"sl_usd": 25.0,  "tp_usd": 125.0},
    "GBPJPY": {"sl_usd": 20.0,  "tp_usd":  80.0},
    "USDJPY": {"sl_usd": 15.0,  "tp_usd":  60.0},
    "EURUSD": {"sl_usd": 15.0,  "tp_usd":  60.0},
    "GBPUSD": {"sl_usd": 15.0,  "tp_usd":  60.0},
}


def calc_sl_tp(
    symbol: str,
    direction: int,
    config: dict,
    lot: float = 0.03,
) -> tuple[float, float]:
    """
    SL/TP dengan dollar cap per-symbol supaya risiko terkontrol.

    Logika:
    1. Hitung SL/TP berbasis ATR (pendekatan teknikal).
    2. Konversi ke dollar risk/reward berdasarkan lot & tick value.
    3. Cap: kalau dollar risk > sl_usd → perkecil jarak SL.
            kalau dollar reward < tp_usd → besarkan jarak TP.
    4. Pastikan R:R >= 3:1 (TP minimal 3× SL distance).

    Return (sl_price, tp_price) atau (0.0, 0.0) kalau gagal.
    """
    sl_tp_cfg = config.get("sl_tp", {})
    sl_mult   = sl_tp_cfg.get("atr_multiplier_sl", 1.5)
    tp_mult   = sl_tp_cfg.get("atr_multiplier_tp", 2.5)

    # Dollar caps: bisa di-override dari config
    caps_cfg  = sl_tp_cfg.get("dollar_caps", {})
    base_key  = symbol.upper().replace("M", "").replace(".S", "")
    caps      = caps_cfg.get(symbol) or caps_cfg.get(base_key) or _DEFAULT_DOLLAR_CAPS.get(base_key, {})
    max_sl_usd = caps.get("sl_usd", 30.0)
    min_tp_usd = caps.get("tp_usd", 90.0)

    resolved = _symbol_cache.get(symbol, symbol)
    rates    = get_rates(symbol)
    if rates is None:
        return 0.0, 0.0

    atr  = calc_atr(rates)
    tick = mt5.symbol_info_tick(resolved)
    info = mt5.symbol_info(resolved)
    if tick is None or info is None:
        log.warning("calc_sl_tp: no tick/info for %s (resolved=%s)", symbol, resolved)
        return 0.0, 0.0

    price       = tick.ask if direction == 1 else tick.bid
    tick_value  = info.trade_tick_value   # USD per 1 tick per 1 lot
    tick_size   = info.trade_tick_size    # ukuran 1 tick dalam harga
    digits      = info.digits

    if tick_value <= 0 or tick_size <= 0:
        log.warning("calc_sl_tp: tick_value/size invalid for %s", symbol)
        return 0.0, 0.0

    # Konversi 1 unit price distance → dollar per lot
    usd_per_price_unit = tick_value / tick_size  # USD per 1.0 harga per lot

    # --- ATR-based distance awal ---
    sl_dist_atr = atr * sl_mult
    tp_dist_atr = atr * tp_mult

    # --- Dollar risk/reward dari ATR ---
    sl_usd_atr = sl_dist_atr * usd_per_price_unit * lot
    tp_usd_atr = tp_dist_atr * usd_per_price_unit * lot

    # --- Terapkan dollar cap ---
    # SL: kalau terlalu besar → perkecil jaraknya
    if sl_usd_atr > max_sl_usd:
        sl_dist = max_sl_usd / (usd_per_price_unit * lot)
    else:
        sl_dist = sl_dist_atr

    # TP: utamakan TP, pastikan minimal min_tp_usd
    tp_dist_from_budget = min_tp_usd / (usd_per_price_unit * lot)
    if tp_usd_atr < min_tp_usd:
        tp_dist = tp_dist_from_budget
    else:
        tp_dist = tp_dist_atr

    # Pastikan R:R minimal 3:1
    if tp_dist < sl_dist * 3.0:
        tp_dist = sl_dist * 3.0

    if direction == 1:
        sl = price - sl_dist
        tp = price + tp_dist
    else:
        sl = price + sl_dist
        tp = price - tp_dist

    sl_usd_final = sl_dist * usd_per_price_unit * lot
    tp_usd_final = tp_dist * usd_per_price_unit * lot

    log.info(
        "SL/TP %s | price=%.5f SL=%.5f TP=%.5f | "
        "risk=$%.2f reward=$%.2f RR=1:%.1f",
        symbol, price, sl, tp,
        sl_usd_final, tp_usd_final,
        tp_usd_final / sl_usd_final if sl_usd_final > 0 else 0,
    )

    return round(sl, digits), round(tp, digits)


# ─────────────────────────────────────────────
# AI MODEL
# ─────────────────────────────────────────────
def get_model(symbol: str, memory: dict, config: dict) -> dict:
    if symbol not in memory["models"]:
        memory["models"][symbol] = {
            "threshold": config["ai"]["threshold_default"],
            "win":       0,
            "loss":      0,
            "trades":    0,
        }
    return memory["models"][symbol]


def update_feedback(symbol: str, profit: float, memory: dict, config: dict) -> None:
    m = get_model(symbol, memory, config)
    m["trades"] += 1
    if profit > 0:
        m["win"] += 1
    else:
        m["loss"] += 1
    calibrate(symbol, memory, config)


def calibrate(symbol: str, memory: dict, config: dict) -> None:
    """
    Sesuaikan threshold berdasarkan win rate.
    WR > wr_high → turunkan threshold (pasar lebih mudah)
    WR < wr_low  → naikkan threshold (pasar lebih susah)
    """
    ai = config["ai"]
    m  = get_model(symbol, memory, config)

    if m["trades"] < ai["min_trades_learn"]:
        return

    wr       = m["win"] / m["trades"]
    wr_high  = ai.get("wr_high",    0.60)
    wr_low   = ai.get("wr_low",     0.40)
    adj      = ai.get("adjust_step", 5) * ai["learning_rate"]

    if wr > wr_high:
        m["threshold"] -= adj
    elif wr < wr_low:
        m["threshold"] += adj

    m["threshold"] = max(
        ai["threshold_min"],
        min(ai["threshold_max"], m["threshold"]),
    )

    v104_log(
        f"Calibrate {symbol} | WR={wr*100:.0f}% "
        f"trades={m['trades']} threshold→{m['threshold']:.1f}"
    )


# ─────────────────────────────────────────────
# COOLDOWN
# ─────────────────────────────────────────────
_last_trade_time: dict[str, float] = {}


def is_on_cooldown(symbol: str, cooldown_sec: int) -> bool:
    return (time.time() - _last_trade_time.get(symbol, 0)) < cooldown_sec


def mark_traded(symbol: str) -> None:
    _last_trade_time[symbol] = time.time()


# ─────────────────────────────────────────────
# ORDER EXECUTION
# ─────────────────────────────────────────────
def send_order(
    symbol: str,
    direction: int,
    lot: float,
    sl: float,
    tp: float,
    comment: str = "V104",
) -> bool:
    # Selalu pakai resolved symbol (XAUUSDm, bukan XAUUSD)
    resolved = _symbol_cache.get(symbol, symbol)

    tick = mt5.symbol_info_tick(resolved)
    info = mt5.symbol_info(resolved)
    if tick is None or info is None:
        v104_log(f"Cannot fetch tick/info for {symbol} (resolved={resolved})", "error")
        return False

    if not info.visible:
        mt5.symbol_select(resolved, True)

    # Cek filling mode yang didukung broker
    filling = mt5.ORDER_FILLING_IOC
    if info.filling_mode & 1:      # FOK supported
        filling = mt5.ORDER_FILLING_FOK
    elif info.filling_mode & 2:    # IOC supported
        filling = mt5.ORDER_FILLING_IOC
    else:                          # Return / Market
        filling = mt5.ORDER_FILLING_RETURN

    order_type = mt5.ORDER_TYPE_BUY if direction == 1 else mt5.ORDER_TYPE_SELL
    price      = tick.ask if direction == 1 else tick.bid

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       resolved,   # pakai resolved name
        "volume":       lot,
        "type":         order_type,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    20,
        "magic":        104,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": filling,
    }

    result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        v104_log(
            f"ORDER FILLED ✓ | {'BUY' if direction==1 else 'SELL'} {resolved} | "
            f"lot={lot:.2f} price={price:.5f} SL={sl:.5f} TP={tp:.5f}"
        )
        mark_traded(symbol)
        return True
    else:
        v104_log(
            f"Order FAILED | {resolved} | retcode={result.retcode} "
            f"comment={result.comment}",
            "error",
        )
        return False


def close_all_positions(reason: str = "DD limit") -> int:
    """
    Close semua posisi yang terbuka. Return jumlah posisi yang berhasil ditutup.
    Dipanggil ketika DD mencapai target untuk membatasi kerugian lebih lanjut.
    """
    positions = mt5.positions_get()
    if not positions:
        return 0

    closed = 0
    for pos in positions:
        sym      = pos.symbol
        info     = mt5.symbol_info(sym)
        tick     = mt5.symbol_info_tick(sym)
        if info is None or tick is None:
            continue

        # Arah close: kebalikan dari posisi terbuka
        if pos.type == mt5.ORDER_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price      = tick.bid
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price      = tick.ask

        filling = mt5.ORDER_FILLING_IOC
        if info.filling_mode & 1:
            filling = mt5.ORDER_FILLING_FOK
        elif info.filling_mode & 2:
            filling = mt5.ORDER_FILLING_IOC
        else:
            filling = mt5.ORDER_FILLING_RETURN

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       sym,
            "volume":       pos.volume,
            "type":         order_type,
            "position":     pos.ticket,
            "price":        price,
            "deviation":    20,
            "magic":        104,
            "comment":      f"V104-CLOSE:{reason}",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            v104_log(f"CLOSED ✓ | ticket={pos.ticket} {sym} | reason={reason}")
            closed += 1
        else:
            v104_log(
                f"Close FAILED | ticket={pos.ticket} {sym} | "
                f"retcode={result.retcode} comment={result.comment}",
                "error",
            )

    return closed


# ─────────────────────────────────────────────
# TRAILING STOP
# ─────────────────────────────────────────────
def manage_trailing_stops(config: dict) -> None:
    """
    Trailing stop untuk semua posisi terbuka milik bot (magic=104).

    Cara kerja:
    - Setiap posisi: hitung "trail distance" dari ATR × trail atr_mult.
    - Kalau harga sudah bergerak menguntungkan >= trigger_rr × sl_dist dari entry,
      geser SL ikuti harga dengan jarak trail_dist.
    - SL baru tidak boleh lebih buruk dari SL lama (only trail in profit direction).

    Contoh BUY entry=2000, SL=1975, harga naik ke 2040:
      sl_dist=25, trigger saat profit >= 12.5 (0.5×25)
      trail_dist = ATR (misalnya 15) → SL baru = 2040-15 = 2025 ✓ (naik dari 1975)

    Konfigurasi di V104_AI_CONFIG.json key "trailing_stop":
      enabled     : true/false
      trigger_rr  : mulai trail setelah profit >= X × sl_dist (default 0.5)
      atr_mult    : jarak trailing = ATR × ini (default 1.0)
    """
    trail_cfg      = config.get("trailing_stop", {})
    enabled        = trail_cfg.get("enabled", True)
    trigger_rr     = trail_cfg.get("trigger_rr", 0.5)
    trail_atr_mult = trail_cfg.get("atr_mult", 1.0)

    if not enabled:
        return

    positions = mt5.positions_get()
    if not positions:
        return

    for pos in positions:
        if pos.magic != 104:
            continue

        sym      = pos.symbol
        tick     = mt5.symbol_info_tick(sym)
        info     = mt5.symbol_info(sym)
        if tick is None or info is None:
            continue

        current_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask
        entry_price   = pos.price_open
        current_sl    = pos.sl
        digits        = info.digits

        sl_dist = abs(entry_price - current_sl) if current_sl > 0 else 0
        if sl_dist <= 0:
            continue

        rates = get_rates(sym)
        if rates is None:
            continue
        atr        = calc_atr(rates)
        trail_dist = min(atr * trail_atr_mult, sl_dist)

        if pos.type == mt5.ORDER_TYPE_BUY:
            profit_dist = current_price - entry_price
            if profit_dist < sl_dist * trigger_rr:
                continue
            new_sl = round(current_price - trail_dist, digits)
            if new_sl <= current_sl:
                continue
        else:
            profit_dist = entry_price - current_price
            if profit_dist < sl_dist * trigger_rr:
                continue
            new_sl = round(current_price + trail_dist, digits)
            if new_sl >= current_sl:
                continue

        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   sym,
            "position": pos.ticket,
            "sl":       new_sl,
            "tp":       pos.tp,
        }
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            v104_log(
                f"TRAIL ✓ | {'BUY' if pos.type==0 else 'SELL'} {sym} "
                f"| SL {current_sl:.{digits}f} → {new_sl:.{digits}f} "
                f"| price={current_price:.{digits}f}"
            )
        else:
            v104_log(
                f"Trail modify FAILED | {sym} ticket={pos.ticket} "
                f"retcode={result.retcode}",
                "warning",
            )


# ─────────────────────────────────────────────
# ACCOUNT STATE
# ─────────────────────────────────────────────
def get_account_state() -> tuple[float, float]:
    info    = mt5.account_info()
    balance = info.balance
    equity  = info.equity
    dd      = max(0.0, (balance - equity) / balance * 100) if balance > 0 else 0.0
    return balance, dd


def has_open_position(symbol: str) -> bool:
    resolved  = _symbol_cache.get(symbol, symbol)
    positions = mt5.positions_get(symbol=resolved)
    return positions is not None and len(positions) > 0


# ─────────────────────────────────────────────
# TRADE ENGINE
# ─────────────────────────────────────────────
def trade_engine(config: dict, memory: dict) -> tuple[list, str, str]:
    """
    Jalankan satu siklus evaluasi.
    Return (trades_executed, signal_str, reason_str) untuk write_state.
    """
    balance, dd = get_account_state()
    v104_log(f"Account | Balance={balance:.2f} | DD={dd:.2f}%")

    trades_executed = []
    signal = ""
    reason = "Score < threshold"

    # Hard DD circuit breaker — close semua posisi & suspend trading
    if dd >= config["risk"]["target_dd"]:
        v104_log(
            f"DD {dd:.2f}% ≥ target {config['risk']['target_dd']}% "
            "— menutup semua posisi & suspend trading.", "warning"
        )
        closed = close_all_positions(reason=f"DD {dd:.2f}%")
        if closed:
            v104_log(f"DD protection: {closed} posisi ditutup.", "warning")
        return trades_executed, signal, f"DD limit reached ({dd:.2f}%) — {closed} pos closed"

    cooldown = config["risk"]["cooldown_sec"]
    logic    = config["logic"]
    lot_base = auto_lot(balance, dd, config)

    # ── CORE: XAUUSD ────────────────────────────────────────
    xau       = "XAUUSD"
    xau_model = get_model(xau, memory, config)

    if is_on_cooldown(xau, cooldown):
        v104_log(f"{xau} on cooldown — skip.")
    elif has_open_position(xau):
        v104_log(f"{xau} already has open position — skip.")
    else:
        score, direction = score_market(xau, config)
        v104_log(
            f"{xau} | score={score} threshold={xau_model['threshold']:.1f} "
            f"dir={direction:+d}"
        )

        if score >= xau_model["threshold"] and direction != 0:
            sl, tp = calc_sl_tp(xau, direction, config, lot=lot_base)
            if sl > 0:
                ok = send_order(xau, direction, lot_base, sl, tp, "V104-CORE")
                if ok:
                    trades_executed.append((xau, "BUY" if direction==1 else "SELL", lot_base))
                    signal = "BUY" if direction == 1 else "SELL"
                    reason = f"BOS + EMA align | score={score}"

            # HEDGE: USDJPY
            if dd < logic["hedge_dd_limit"] and not has_open_position("USDJPY"):
                hedge_dir    = -direction
                lot_h        = round(lot_base * 0.5, 2)
                sl_h, tp_h   = calc_sl_tp("USDJPY", hedge_dir, config, lot=lot_h)
                if sl_h > 0:
                    ok_h  = send_order("USDJPY", hedge_dir, lot_h, sl_h, tp_h, "V104-HEDGE")
                    if ok_h:
                        trades_executed.append(("USDJPY", "BUY" if hedge_dir==1 else "SELL", lot_h))

            # SYNTHETIC: EURUSD + GBPUSD
            if (score >= logic["synthetic_enable_score"]
                    and dd < logic["synthetic_dd_limit"]):
                for sym, ratio in [("EURUSD", 0.5), ("GBPUSD", 0.4)]:
                    if not has_open_position(sym):
                        lot_s      = round(lot_base * ratio, 2)
                        sl_s, tp_s = calc_sl_tp(sym, direction, config, lot=lot_s)
                        if sl_s > 0:
                            ok_s  = send_order(sym, direction, lot_s, sl_s, tp_s, "V104-SYNTH")
                            if ok_s:
                                trades_executed.append((sym, "BUY" if direction==1 else "SELL", lot_s))

    # ── BOOSTER: GBPJPY ─────────────────────────────────────
    gbj       = "GBPJPY"
    gbj_model = get_model(gbj, memory, config)

    if is_on_cooldown(gbj, cooldown):
        v104_log(f"{gbj} on cooldown — skip.")
    elif has_open_position(gbj):
        v104_log(f"{gbj} already has open position — skip.")
    elif dd < logic["booster_dd_limit"]:
        score_gj, dir_gj = score_market(gbj, config)
        v104_log(
            f"{gbj} | score={score_gj} threshold={gbj_model['threshold']:.1f} "
            f"dir={dir_gj:+d}"
        )
        if score_gj >= gbj_model["threshold"] and dir_gj != 0:
            lot_gj = round(lot_base * 0.7, 2)
            sl_gj, tp_gj = calc_sl_tp(gbj, dir_gj, config, lot=lot_gj)
            if sl_gj > 0:
                ok_gj = send_order(gbj, dir_gj, lot_gj, sl_gj, tp_gj, "V104-BOOST")
                if ok_gj:
                    trades_executed.append((gbj, "BUY" if dir_gj==1 else "SELL", lot_gj))
                    if not signal:
                        signal = "BUY" if dir_gj == 1 else "SELL"
                        reason = f"GBPJPY booster | score={score_gj}"

    return trades_executed, signal, reason


# ─────────────────────────────────────────────
# CLOSED TRADE SCANNER (AI feedback)
# ─────────────────────────────────────────────
_processed_tickets: set[int] = set()


def scan_closed_trades(memory: dict, config: dict) -> None:
    """Baca histori MT5, feed hasil trade V104 ke AI."""
    now   = datetime.now()
    since = now - timedelta(hours=24)
    deals = mt5.history_deals_get(since, now)

    if deals is None:
        return

    for deal in deals:
        if deal.magic != 104:
            continue
        if deal.ticket in _processed_tickets:
            continue
        if deal.profit == 0:
            continue

        _processed_tickets.add(deal.ticket)
        sym = deal.symbol
        update_feedback(sym, deal.profit, memory, config)
        v104_log(f"Feedback | {sym} | profit={deal.profit:.2f} | ticket={deal.ticket}")


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def main():
    # Baca credentials dari config/broker.yaml
    try:
        MT5_LOGIN, MT5_PASSWORD, MT5_SERVER = load_broker_config()
    except Exception:
        log.error("Tidak bisa baca broker.yaml. Bot berhenti.")
        return

    config = load_config()
    memory = load_memory()

    if not connect_mt5(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER):
        write_state(0, 0, memory, [], running=False,
                    reason="MT5 connection failed")
        return

    LOOP_INTERVAL = 60  # detik antar setiap cycle

    v104_log("V104 Bot started. Ctrl+C untuk stop.")
    balance, dd = get_account_state()
    write_state(balance, dd, memory, [], running=True, reason="Bot started")

    try:
        while True:
            # Cek stop signal dari dashboard
            if check_stop_signal():
                v104_log("Stop signal diterima dari dashboard. Bot berhenti.")
                balance, dd = get_account_state()
                write_state(balance, dd, memory, [], running=False,
                            reason="Stopped via dashboard")
                break

            try:
                config = load_config()  # reload setiap cycle, perubahan JSON langsung efektif
                scan_closed_trades(memory, config)
                manage_trailing_stops(config)          # ← trailing stop setiap cycle
                trades, signal, reason = trade_engine(config, memory)
                save_memory(memory)
                balance, dd = get_account_state()
                write_state(balance, dd, memory, trades,
                            signal=signal, reason=reason, running=True)
                v104_log(
                    f"Cycle done | Balance={balance:.2f} | "
                    f"DD={dd:.2f}% | Trades={len(trades)}"
                )

            except Exception as e:
                v104_log(f"Cycle error: {e}", "error")
                try:
                    balance, dd = get_account_state()
                except Exception:
                    balance, dd = 0.0, 0.0
                write_state(balance, dd, memory, [], running=True,
                            reason=f"Cycle error: {e}")

            v104_log(f"Sleeping {LOOP_INTERVAL}s...\n")
            time.sleep(LOOP_INTERVAL)

    except KeyboardInterrupt:
        v104_log("Bot dihentikan manual (Ctrl+C).")
    finally:
        balance, dd = get_account_state()
        write_state(balance, dd, memory, [], running=False,
                    reason="Bot stopped")
        save_memory(memory)
        mt5.shutdown()
        v104_log("MT5 disconnected. Selesai.")


if __name__ == "__main__":
    main()