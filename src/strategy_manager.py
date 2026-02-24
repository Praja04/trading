"""
strategy_manager.py
====================
Membaca DUA format JSON sekaligus:
  - Format lama  : { "parameters": {...}, "entry_conditions": {...}, ... }
  - Format baru  : { "quant_xxx": { "risk_management": {...}, "symbol_management": {...}, ... } }

Fitur canggih yang diimplementasikan dari JSON baru:
  - Synthetic DXY proxy (multi-pair correlation)
  - Liquidity Imbalance Model (tick body/range/spread)
  - Drawdown Recovery Engine (3 level risk reduction)
  - Dynamic Leverage Scaling (vol-adjusted risk)
  - Spread Filter (execution_slippage_optimizer)
  - Weekend Shield (posisi dikurangi menjelang weekend)
  - News Block (via DB news table)
  - ATR-based dynamic SL/TP
"""

import json
import logging
import os
import re
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Tuple


class StrategyManager:
    """
    Strategy manager yang mendukung dua format JSON.
    Semua parameter perilaku (risk, pairs, SL/TP, scoring) dibaca dari file JSON.
    """

    DB_PATH = "data/database/trading_data.db"

    def __init__(self, strategy_path: str = None):
        self.strategy_config = {}
        self.strategy_name   = "Default"
        self._norm           = {}
        self._format         = "legacy"

        if strategy_path and os.path.exists(strategy_path):
            self.load_strategy(strategy_path)
        else:
            logging.warning("No strategy file — using minimal default")
            self._load_default_strategy()

    # ══════════════════════════════════════════════════════════════════
    # LOADING & NORMALIZATION
    # ══════════════════════════════════════════════════════════════════

    def load_strategy(self, strategy_path: str) -> bool:
        try:
            with open(strategy_path, 'r') as f:
                raw = json.load(f)

            # Unwrap outer key jika ada satu key yang nilainya dict
            if len(raw) == 1 and isinstance(list(raw.values())[0], dict):
                self.strategy_config = list(raw.values())[0]
            else:
                self.strategy_config = raw

            self.strategy_name = self.strategy_config.get('strategy_name', 'Custom')
            self._format       = self._detect_format()
            self._norm         = self._normalize()

            logging.info(f"[OK] Strategy loaded: {self.strategy_name} (format={self._format})")
            self._log_strategy_info()
            return True

        except Exception as e:
            logging.error(f"Error loading strategy: {e}")
            self._load_default_strategy()
            return False

    def _detect_format(self) -> str:
        """Deteksi apakah JSON format advanced atau legacy."""
        advanced_keys = {
            'risk_management', 'symbol_management', 'general_parameters',
            'liquidity_imbalance_model', 'drawdown_recovery_engine',
            'broker_auto_detection', 'synthetic_macro_proxies'
        }
        if advanced_keys & set(self.strategy_config.keys()):
            return "advanced"
        return "legacy"

    def _normalize(self) -> Dict:
        """
        Buat view seragam dari kedua format JSON.
        Semua kode lain pakai self._norm, bukan self.strategy_config langsung.
        """
        cfg = self.strategy_config
        n   = {}

        if self._format == "advanced":
            # ── PAIRS ──────────────────────────────────────────────
            sym  = cfg.get('symbol_management', {})
            base = sym.get('base_symbols', {})
            pri  = base.get('primary', 'XAUUSD')
            sec  = base.get('secondary', [])
            n['trading_pairs'] = [pri] + (sec if isinstance(sec, list) else [])

            # ── RISK ───────────────────────────────────────────────
            rm = cfg.get('risk_management', {})
            n['risk_per_trade_min'] = rm.get('risk_per_trade_min',      0.003)
            n['risk_per_trade_max'] = rm.get('risk_per_trade_max',      0.010)
            n['max_drawdown_limit'] = rm.get('max_total_drawdown',      0.04)
            n['daily_loss_limit']   = rm.get('daily_loss_limit',        0.02)
            n['portfolio_heat_cap'] = rm.get('portfolio_heat_cap_weekday', 0.032)

            # ── GENERAL ────────────────────────────────────────────
            gp = cfg.get('general_parameters', {})
            n['max_positions']  = gp.get('max_trades_per_day', 6)
            n['max_trades_day'] = gp.get('max_trades_per_day', 6)
            n['execution_tf']   = gp.get('execution_timeframe', 'M5')
            n['timeframes']     = [
                gp.get('micro_timeframe',     'M1'),
                gp.get('execution_timeframe', 'M5'),
                gp.get('trend_timeframe',     'M15'),
            ]

            # ── MIN CONFIDENCE ─────────────────────────────────────
            n['min_confidence'] = 55

            # ── INDICATORS (defaults untuk advanced) ───────────────
            n['atr_period']         = 14
            n['rsi_period']         = 14
            n['rsi_oversold']       = 30
            n['rsi_overbought']     = 70
            n['macd_fast']          = 12
            n['macd_slow']          = 26
            n['macd_signal_period'] = 9
            n['bb_period']          = 20
            n['bb_std']             = 2.0

            # ── EXIT ───────────────────────────────────────────────
            n['use_atr_exit']      = True
            n['atr_multiplier_sl'] = 1.5
            n['atr_multiplier_tp'] = 2.5
            n['stop_loss_pips']    = 20   # fallback
            n['take_profit_pips']  = 40

            # ── SCORING ────────────────────────────────────────────
            n['scoring'] = {
                'ma_cross':        20,
                'ma_cross_bonus':   8,
                'rsi':             20,
                'macd':            20,
                'macd_cross_bonus': 8,
                'bollinger_bands': 15,
                'momentum':        10,
                'lim_bonus':       15,
            }

            # ── LEVERAGE SCALING ───────────────────────────────────
            dls = cfg.get('dynamic_leverage_scaling', {})
            n['leverage_enabled']        = dls.get('enabled', True)
            n['leverage_expansion_mult'] = dls.get('expansion_multiplier', 1.2)
            n['leverage_low_vol_mult']   = dls.get('low_vol_multiplier',   0.7)
            n['leverage_extreme_mult']   = dls.get('extreme_multiplier',   0.5)

            # ── LIQUIDITY IMBALANCE MODEL ──────────────────────────
            lim = cfg.get('liquidity_imbalance_model', {})
            n['lim_enabled']           = lim.get('enabled', True)
            n['lim_tick_weight']       = lim.get('tick_imbalance_weight', 0.30)
            n['lim_volume_weight']     = lim.get('volume_spike_weight',   0.25)
            n['lim_body_weight']       = lim.get('body_range_weight',     0.20)
            n['lim_spread_weight']     = lim.get('spread_weight',         0.15)
            n['lim_micro_atr_weight']  = lim.get('micro_atr_weight',      0.10)
            n['lim_strong_threshold']  = lim.get('strong_threshold',      0.6)
            n['lim_extreme_threshold'] = lim.get('extreme_threshold',     0.8)
            n['lim_risk_reduction']    = lim.get('risk_reduction_extreme', 0.7)

            # ── DRAWDOWN RECOVERY ──────────────────────────────────
            dre = cfg.get('drawdown_recovery_engine', {})
            n['dre_l1_thresh'] = dre.get('level_1_threshold',       0.01)
            n['dre_l2_thresh'] = dre.get('level_2_threshold',       0.02)
            n['dre_l3_thresh'] = dre.get('level_3_threshold',       0.03)
            n['dre_l1_mult']   = dre.get('risk_multiplier_level_1', 0.8)
            n['dre_l2_mult']   = dre.get('risk_multiplier_level_2', 0.6)
            n['dre_l3_mult']   = dre.get('risk_multiplier_level_3', 0.4)

            # ── SPREAD / SLIPPAGE FILTER ───────────────────────────
            slo = cfg.get('execution_slippage_optimizer', {})
            n['spread_filter_enabled'] = slo.get('enabled', True)
            n['max_spread_multiplier'] = slo.get('max_spread_multiplier', 1.8)
            n['max_micro_atr_spike']   = slo.get('max_micro_atr_spike',   2.0)

            # ── WEEKEND SHIELD ─────────────────────────────────────
            ws = cfg.get('weekend_shield', {})
            n['weekend_shield_enabled'] = ws.get('enabled', True)
            n['weekend_hours_before']   = ws.get('activation_hours_before_close', 4)

            # ── NEWS BLOCK ─────────────────────────────────────────
            sse = cfg.get('surprise_score_engine', {})
            n['news_block_enabled']   = sse.get('enabled', True)
            n['news_block_before_min'] = sse.get('block_minutes_before_event', 30)
            n['news_block_after_min']  = sse.get('block_minutes_after_event',  30)

            # ── SYNTHETIC DXY ──────────────────────────────────────
            smp = cfg.get('synthetic_macro_proxies', {})
            n['dxy_weights']           = smp.get('synthetic_dxy_weights', {})
            n['vol_percentile_window'] = smp.get('volatility_percentile_window', 60)

        else:
            # ── LEGACY FORMAT ──────────────────────────────────────
            params    = cfg.get('parameters', {})
            entry_cfg = cfg.get('entry_conditions', {})
            exit_cfg  = cfg.get('exit_strategy', {})
            indic     = {
                **entry_cfg.get('indicators', {}),
                **entry_cfg.get('momentum_confirmation', {})
            }

            n['trading_pairs'] = (
                params.get('trading_pairs') or params.get('pairs') or
                cfg.get('trading_pairs') or cfg.get('pairs') or []
            )

            rr = params.get('risk_per_trade_range', [0.01, 0.02])
            n['risk_per_trade_min'] = rr[0]
            n['risk_per_trade_max'] = rr[1]
            n['max_drawdown_limit'] = params.get('max_drawdown_limit', 0.15)
            n['daily_loss_limit']   = 0
            n['portfolio_heat_cap'] = rr[1] * params.get('max_positions', 3)

            n['max_positions']  = params.get('max_positions', 3)
            n['max_trades_day'] = params.get('max_positions', 3)
            n['execution_tf']   = (params.get('timeframes', ['M1']) or ['M1'])[0]
            n['timeframes']     = params.get('timeframes', ['M1'])
            n['min_confidence'] = (
                entry_cfg.get('min_confidence') or
                params.get('min_confidence', 60)
            )

            n['atr_period']         = indic.get('atr_period', 14)
            n['rsi_period']         = indic.get('rsi_period', 14)
            n['rsi_oversold']       = indic.get('rsi_oversold', 30)
            n['rsi_overbought']     = indic.get('rsi_overbought', 70)
            n['macd_fast']          = indic.get('macd_fast', 12)
            n['macd_slow']          = indic.get('macd_slow', 26)
            n['macd_signal_period'] = indic.get('macd_signal', 9)
            n['bb_period']          = indic.get('bb_period', 20)
            n['bb_std']             = indic.get('bb_std_dev', 2.0)

            n['use_atr_exit']      = (
                exit_cfg.get('use_atr', False) or 'atr_multiplier_sl' in exit_cfg
            )
            n['atr_multiplier_sl'] = exit_cfg.get('atr_multiplier_sl', 1.5)
            n['atr_multiplier_tp'] = exit_cfg.get('atr_multiplier_tp', 2.5)
            n['stop_loss_pips']    = self._parse_pips(
                exit_cfg.get('stop_loss', exit_cfg.get('stop_loss_pips', 20)))
            n['take_profit_pips']  = self._parse_pips(
                exit_cfg.get('take_profit', exit_cfg.get('take_profit_pips', 40)))

            n['scoring'] = entry_cfg.get('scoring', {
                'ma_cross': 30, 'ma_cross_bonus': 10,
                'rsi': 25, 'macd': 25, 'macd_cross_bonus': 10,
                'bollinger_bands': 15, 'stochastic': 15, 'momentum': 10,
            })

            # Advanced features OFF untuk legacy
            n['lim_enabled']            = False
            n['leverage_enabled']       = False
            n['weekend_shield_enabled'] = False
            n['news_block_enabled']     = False
            n['spread_filter_enabled']  = False
            n['dxy_weights']            = {}

        return n

    def _load_default_strategy(self):
        self.strategy_config = {}
        self._format = "legacy"
        self._norm = {
            'trading_pairs': [], 'risk_per_trade_min': 0.01, 'risk_per_trade_max': 0.02,
            'max_drawdown_limit': 0.15, 'daily_loss_limit': 0, 'portfolio_heat_cap': 0.06,
            'max_positions': 3, 'max_trades_day': 3, 'timeframes': ['M1'], 'execution_tf': 'M1',
            'min_confidence': 60, 'atr_period': 14, 'rsi_period': 14,
            'rsi_oversold': 30, 'rsi_overbought': 70, 'macd_fast': 12,
            'macd_slow': 26, 'macd_signal_period': 9, 'bb_period': 20, 'bb_std': 2.0,
            'use_atr_exit': False, 'atr_multiplier_sl': 1.5, 'atr_multiplier_tp': 2.5,
            'stop_loss_pips': 20, 'take_profit_pips': 40,
            'scoring': {'ma_cross': 35, 'rsi': 35, 'macd': 30},
            'lim_enabled': False, 'leverage_enabled': False,
            'weekend_shield_enabled': False, 'news_block_enabled': False,
            'spread_filter_enabled': False, 'dxy_weights': {},
        }
        self.strategy_name = "Default"

    def _log_strategy_info(self):
        n = self._norm
        logging.info("=" * 65)
        logging.info(f"STRATEGY    : {self.strategy_name}")
        logging.info(f"FORMAT      : {self._format}")
        logging.info(f"PAIRS       : {n.get('trading_pairs', [])}")
        logging.info(f"TIMEFRAME   : {n.get('execution_tf')}")
        logging.info(f"RISK        : {n['risk_per_trade_min']*100:.2f}% – {n['risk_per_trade_max']*100:.2f}%")
        logging.info(f"MAX POS     : {n.get('max_positions')}")
        logging.info(f"MIN CONF    : {n.get('min_confidence')}%")
        logging.info(f"ATR EXIT    : {n.get('use_atr_exit')}")
        logging.info(f"LIM         : {n.get('lim_enabled')}")
        logging.info(f"DD RECOVERY : L1={n.get('dre_l1_thresh','off')} L2={n.get('dre_l2_thresh','off')} L3={n.get('dre_l3_thresh','off')}")
        logging.info(f"NEWS BLOCK  : {n.get('news_block_enabled')}")
        logging.info(f"WEEKEND     : {n.get('weekend_shield_enabled')}")
        logging.info(f"SPREAD FILT : {n.get('spread_filter_enabled')}")
        logging.info("=" * 65)

    # ══════════════════════════════════════════════════════════════════
    # INDICATOR CALCULATION
    # ══════════════════════════════════════════════════════════════════

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            df    = df.copy()
            n     = self._norm
            indic = {}

            # Legacy indicators key
            if self._format == "legacy":
                ec    = self.strategy_config.get('entry_conditions', {})
                indic = {**ec.get('indicators', {}), **ec.get('momentum_confirmation', {})}
                if 'ma_fast' in indic:
                    df['ma_fast'] = df['close'].rolling(int(indic['ma_fast'])).mean()
                if 'ma_slow' in indic:
                    df['ma_slow'] = df['close'].rolling(int(indic['ma_slow'])).mean()
                if 'ema_fast' in indic:
                    df['ema_fast'] = df['close'].ewm(span=int(indic['ema_fast']), adjust=False).mean()
                if 'ema_slow' in indic:
                    df['ema_slow'] = df['close'].ewm(span=int(indic['ema_slow']), adjust=False).mean()
                if 'stochastic_period' in indic:
                    st = self._calc_stoch(df, int(indic['stochastic_period']))
                    df['stoch_k'] = st['k']
                    df['stoch_d'] = st['d']
            else:
                # Advanced — pakai EMA 9/21 sebagai default trend filter
                df['ema_fast'] = df['close'].ewm(span=9,  adjust=False).mean()
                df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()

            # Semua format: RSI, MACD, BB, ATR, Momentum
            df['rsi'] = self._calc_rsi(df['close'], n['rsi_period'])

            m = self._calc_macd(df['close'], n['macd_fast'], n['macd_slow'], n['macd_signal_period'])
            df['macd']           = m['macd']
            df['macd_signal']    = m['signal']
            df['macd_histogram'] = m['histogram']

            bb = self._calc_bb(df['close'], n['bb_period'], n['bb_std'])
            df['bb_upper']  = bb['upper']
            df['bb_middle'] = bb['middle']
            df['bb_lower']  = bb['lower']

            df['atr']      = self._calc_atr(df, n['atr_period'])
            df['momentum'] = df['close'].pct_change(periods=5) * 100

            # Extra columns untuk LIM
            if n.get('lim_enabled', False):
                if 'volume' in df.columns and len(df) >= 20:
                    df['volume_pct'] = df['volume'].rolling(20).apply(
                        lambda x: float((x.iloc[-1] > x.iloc[:-1]).mean()), raw=False
                    )
                df['body']       = np.abs(df['close'] - df['open'])
                df['body_ratio'] = df['body'] / (df['high'] - df['low']).replace(0, np.nan)

            return df

        except Exception as e:
            logging.error(f"Error calculating indicators: {e}")
            return df

    # ══════════════════════════════════════════════════════════════════
    # MATH HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _calc_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        delta = prices.diff()
        gain  = delta.where(delta > 0, 0).rolling(period).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs    = gain / loss
        return 100 - (100 / (1 + rs))

    def _calc_macd(self, prices, fast=12, slow=26, signal=9):
        ef  = prices.ewm(span=fast,   adjust=False).mean()
        es  = prices.ewm(span=slow,   adjust=False).mean()
        mac = ef - es
        sig = mac.ewm(span=signal, adjust=False).mean()
        return {'macd': mac, 'signal': sig, 'histogram': mac - sig}

    def _calc_bb(self, prices, period=20, std_dev=2.0):
        mid = prices.rolling(period).mean()
        std = prices.rolling(period).std()
        return {'upper': mid + std * std_dev, 'middle': mid, 'lower': mid - std * std_dev}

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        hl = df['high'] - df['low']
        hc = np.abs(df['high'] - df['close'].shift())
        lc = np.abs(df['low']  - df['close'].shift())
        return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(period).mean()

    def _calc_stoch(self, df: pd.DataFrame, period=14, sk=3, sd=3) -> Dict:
        lo = df['low'].rolling(period).min()
        hi = df['high'].rolling(period).max()
        k  = 100 * (df['close'] - lo) / (hi - lo)
        k  = k.rolling(sk).mean()
        return {'k': k, 'd': k.rolling(sd).mean()}

    # ══════════════════════════════════════════════════════════════════
    # ADVANCED FEATURE ENGINES
    # ══════════════════════════════════════════════════════════════════

    def compute_liquidity_imbalance_score(self, df: pd.DataFrame, current_spread: float = 0) -> float:
        """
        Liquidity Imbalance Model — skor 0..1.
        Semakin tinggi = pasar lebih liquid & momentum kuat = aman untuk trade.
        """
        n = self._norm
        if not n.get('lim_enabled', False) or len(df) < 5:
            return 0.5

        try:
            latest = df.iloc[-1]
            score  = 0.0

            # 1. Volume spike
            if 'volume' in df.columns:
                avg_vol = df['volume'].iloc[-20:].mean()
                spike   = min(float(latest['volume']) / max(avg_vol, 1), 3) / 3
                score  += spike * n['lim_volume_weight']

            # 2. Volume percentile
            vol_pct = float(latest['volume_pct']) if 'volume_pct' in df.columns else 0.5
            score  += vol_pct * n['lim_tick_weight']

            # 3. Body/range ratio
            body_ratio = float(latest.get('body_ratio', 0.5)) if 'body_ratio' in df.columns else 0.5
            score     += min(body_ratio, 1.0) * n['lim_body_weight']

            # 4. Spread score (spread kecil = lebih liquid)
            if 'atr' in df.columns and not pd.isna(latest['atr']) and float(latest['atr']) > 0:
                spread_score = max(0.0, 1.0 - current_spread / float(latest['atr']))
            else:
                spread_score = 0.5
            score += spread_score * n['lim_spread_weight']

            # 5. Micro ATR (ATR rendah relatif terhadap max = lebih tenang)
            if 'atr' in df.columns and not pd.isna(latest['atr']):
                atr_max    = float(df['atr'].rolling(20).max().iloc[-1])
                micro_score = 1.0 - min(float(latest['atr']) / max(atr_max, 1e-9), 1.0)
            else:
                micro_score = 0.5
            score += micro_score * n['lim_micro_atr_weight']

            return round(min(max(score, 0.0), 1.0), 4)

        except Exception as e:
            logging.debug(f"LIM error: {e}")
            return 0.5

    def compute_drawdown_risk_multiplier(self, current_equity: float, peak_equity: float) -> float:
        """
        Drawdown Recovery Engine.
        Return multiplier: 1.0 (normal) / 0.8 / 0.6 / 0.4 tergantung level DD.
        """
        n = self._norm
        if 'dre_l1_thresh' not in n or peak_equity <= 0:
            return 1.0

        dd = (peak_equity - current_equity) / peak_equity

        if dd >= n['dre_l3_thresh']:
            mult = n['dre_l3_mult']
            logging.warning(f"[DD-Recovery] Level 3 — DD={dd:.2%}, risk x{mult}")
        elif dd >= n['dre_l2_thresh']:
            mult = n['dre_l2_mult']
            logging.warning(f"[DD-Recovery] Level 2 — DD={dd:.2%}, risk x{mult}")
        elif dd >= n['dre_l1_thresh']:
            mult = n['dre_l1_mult']
            logging.info(f"[DD-Recovery] Level 1 — DD={dd:.2%}, risk x{mult}")
        else:
            mult = 1.0

        return mult

    def compute_volatility_risk_multiplier(self, df: pd.DataFrame) -> float:
        """
        Dynamic Leverage Scaling — sesuaikan risk berdasarkan ATR percentile.
        High vol → kurangi risk. Low vol → slight reduction (tetap konservatif).
        """
        n = self._norm
        if not n.get('leverage_enabled', False) or 'atr' not in df.columns:
            return 1.0

        try:
            window     = n.get('vol_percentile_window', 60)
            atr_series = df['atr'].dropna()
            if len(atr_series) < 10:
                return 1.0

            current_atr = float(atr_series.iloc[-1])
            rolling_atr = atr_series.iloc[-min(window, len(atr_series)):]
            percentile  = float((rolling_atr < current_atr).mean())

            if percentile > 0.8:
                mult = n.get('leverage_extreme_mult', 0.5)
                logging.info(f"[DLS] Extreme vol (p={percentile:.2f}) → x{mult}")
            elif percentile < 0.3:
                mult = n.get('leverage_low_vol_mult', 0.7)
                logging.info(f"[DLS] Low vol (p={percentile:.2f}) → x{mult}")
            else:
                mult = 1.0

            return mult

        except Exception as e:
            logging.debug(f"DLS error: {e}")
            return 1.0

    def check_spread_filter(self, current_spread: float, df: pd.DataFrame) -> bool:
        """
        Spread Filter — return False = BLOCK trade (spread terlalu lebar).
        """
        n = self._norm
        if not n.get('spread_filter_enabled', False) or 'atr' not in df.columns:
            return True

        try:
            atr = float(df['atr'].iloc[-1])
            if pd.isna(atr) or atr <= 0:
                return True
            max_spread = atr * n.get('max_spread_multiplier', 1.8)
            if current_spread > max_spread:
                logging.info(f"[SpreadFilter] BLOCKED spread={current_spread:.5f} > max={max_spread:.5f}")
                return False
            return True
        except Exception as e:
            logging.debug(f"SpreadFilter error: {e}")
            return True

    def check_weekend_shield(self) -> bool:
        """
        Weekend Shield — return False = jangan buka posisi baru.
        Aktif pada Jumat sore (N jam sebelum tutup) dan Sabtu–Minggu.
        """
        n = self._norm
        if not n.get('weekend_shield_enabled', False):
            return True

        now = datetime.now()
        if now.weekday() in (5, 6):   # Sabtu, Minggu
            logging.info("[WeekendShield] Weekend — no new trades")
            return False
        if now.weekday() == 4:        # Jumat
            hours_left = 22 - now.hour   # asumsi market close 22:00 UTC
            if hours_left <= n.get('weekend_hours_before', 4):
                logging.info(f"[WeekendShield] {hours_left}h to close — blocking new trades")
                return False
        return True

    def check_news_block(self, symbol: str) -> bool:
        """
        News Block — cek DB untuk high-impact news dalam window.
        Return False = BLOCK trade.
        """
        n = self._norm
        if not n.get('news_block_enabled', False):
            return True

        try:
            if not os.path.exists(self.DB_PATH):
                return True

            before_min = n.get('news_block_before_min', 30)
            after_min  = n.get('news_block_after_min',  30)
            currencies = self._currencies_from_symbol(symbol)

            conn   = sqlite3.connect(self.DB_PATH)
            cursor = conn.cursor()
            ph     = ','.join(['?' for _ in currencies])
            cursor.execute(f'''
                SELECT title, currency, event_time FROM news
                WHERE currency IN ({ph})
                  AND impact = 'High'
                  AND event_time BETWEEN datetime('now', '-{after_min} minutes')
                                     AND datetime('now', '+{before_min} minutes')
                ORDER BY event_time ASC LIMIT 5
            ''', currencies)
            rows = cursor.fetchall()
            conn.close()

            if rows:
                for r in rows:
                    logging.info(f"[NewsBlock] {symbol} BLOCKED — '{r[0]}' ({r[1]}) @ {r[2]}")
                return False
            return True

        except Exception as e:
            logging.debug(f"NewsBlock error: {e}")
            return True

    def _currencies_from_symbol(self, symbol: str):
        s = symbol.upper().replace('.S', '').replace('_', '').strip()
        if len(s) >= 6:
            return [s[:3], s[3:6]]
        return [s]

    # ══════════════════════════════════════════════════════════════════
    # MAIN ANALYSIS
    # ══════════════════════════════════════════════════════════════════

    def analyze(self, symbol: str, df: pd.DataFrame,
                current_spread: float = 0,
                current_equity: float = 0,
                peak_equity: float = 0) -> Dict:
        """
        Analisis utama — return sinyal {action, confidence, stop_loss, take_profit, ...}.

        Args:
            symbol         : nama pair (e.g. "XAUUSD.s")
            df             : OHLC DataFrame
            current_spread : spread saat ini dalam price units
            current_equity : equity akun saat ini (untuk DD engine)
            peak_equity    : equity tertinggi historis (untuk DD engine)
        """
        try:
            df = self.calculate_indicators(df)
            if len(df) < 2:
                return self._hold(df.iloc[-1]['close'] if len(df) > 0 else 0)

            latest = df.iloc[-1]
            prev   = df.iloc[-2]
            n      = self._norm

            # ── Gate 1: Weekend Shield ──────────────────────────────
            if not self.check_weekend_shield():
                return self._hold(float(latest['close']), "weekend_shield")

            # ── Gate 2: News Block ──────────────────────────────────
            if not self.check_news_block(symbol):
                return self._hold(float(latest['close']), "news_block")

            # ── Gate 3: Spread Filter ───────────────────────────────
            if not self.check_spread_filter(current_spread, df):
                return self._hold(float(latest['close']), "spread_too_wide")

            # ── Base Scoring ────────────────────────────────────────
            buy_score, sell_score = self._evaluate_conditions(latest, prev)

            # ── LIM Adjustment ──────────────────────────────────────
            lim_score = self.compute_liquidity_imbalance_score(df, current_spread)
            if n.get('lim_enabled', False):
                extreme_th = n.get('lim_extreme_threshold', 0.8)
                strong_th  = n.get('lim_strong_threshold',  0.6)
                lim_bonus  = n['scoring'].get('lim_bonus', 0)

                if lim_score >= extreme_th:
                    # Terlalu volatile/illiquid — pangkas confidence
                    factor = n.get('lim_risk_reduction', 0.7)
                    buy_score  = int(buy_score  * factor)
                    sell_score = int(sell_score * factor)
                    logging.info(f"[LIM] Extreme ({lim_score:.2f}) — score *{factor}")
                elif lim_score >= strong_th:
                    bonus = int(lim_bonus * lim_score)
                    buy_score  += bonus
                    sell_score += bonus

            # ── Risk Multipliers ────────────────────────────────────
            dd_mult  = self.compute_drawdown_risk_multiplier(current_equity, peak_equity) \
                       if current_equity > 0 and peak_equity > 0 else 1.0
            vol_mult = self.compute_volatility_risk_multiplier(df)

            # ── Decision ────────────────────────────────────────────
            min_conf = n.get('min_confidence', 60)
            signal   = {
                'action':          'HOLD',
                'confidence':      0,
                'price':           float(latest['close']),
                'timestamp':       datetime.now(),
                'strategy':        self.strategy_name,
                'format':          self._format,
                'indicators':      self._extract_indicators(latest),
                'lim_score':       lim_score,
                'dd_multiplier':   dd_mult,
                'vol_multiplier':  vol_mult,
                'risk_multiplier': dd_mult * vol_mult,
                'hold_reason':     '',
            }

            if buy_score > sell_score and buy_score >= min_conf:
                sl, tp = self._exit_levels(float(latest['close']), 'BUY', symbol, latest)
                signal.update({'action': 'BUY', 'confidence': buy_score,
                                'stop_loss': sl, 'take_profit': tp})

            elif sell_score > buy_score and sell_score >= min_conf:
                sl, tp = self._exit_levels(float(latest['close']), 'SELL', symbol, latest)
                signal.update({'action': 'SELL', 'confidence': sell_score,
                                'stop_loss': sl, 'take_profit': tp})

            return signal

        except Exception as e:
            logging.error(f"Error analysing {symbol}: {e}")
            import traceback; logging.error(traceback.format_exc())
            return self._hold(0)

    # ══════════════════════════════════════════════════════════════════
    # SCORING
    # ══════════════════════════════════════════════════════════════════

    def _evaluate_conditions(self, latest: pd.Series, prev: pd.Series) -> Tuple[int, int]:
        buy_score  = 0
        sell_score = 0
        scoring    = self._norm.get('scoring', {})
        n          = self._norm

        def w(key, default=20):
            return scoring.get(key, default)

        # 1. EMA / MA cross
        for fast_col, slow_col in [('ema_fast', 'ema_slow'), ('ma_fast', 'ma_slow')]:
            if fast_col in latest.index and slow_col in latest.index:
                fv, sv = latest.get(fast_col), latest.get(slow_col)
                if fv is not None and sv is not None and not pd.isna(fv) and not pd.isna(sv):
                    s = w('ma_cross', 25)
                    b = w('ma_cross_bonus', 8)
                    if float(fv) > float(sv):
                        buy_score += s
                        if float(prev.get(fast_col, 0)) <= float(prev.get(slow_col, 0)):
                            buy_score += b
                    else:
                        sell_score += s
                        if float(prev.get(fast_col, 0)) >= float(prev.get(slow_col, 0)):
                            sell_score += b
                    break

        # 2. RSI
        rsi_val = latest.get('rsi')
        if rsi_val is not None and not pd.isna(rsi_val):
            s   = w('rsi', 20)
            rsi = float(rsi_val)
            if rsi < n.get('rsi_oversold', 30):
                buy_score  += s
            elif rsi > n.get('rsi_overbought', 70):
                sell_score += s
            elif rsi < 50:
                buy_score  += int(s * 0.4)
            else:
                sell_score += int(s * 0.4)

        # 3. MACD
        macd_val = latest.get('macd')
        msig_val = latest.get('macd_signal')
        if macd_val is not None and msig_val is not None:
            if not pd.isna(macd_val) and not pd.isna(msig_val):
                s = w('macd', 20)
                b = w('macd_cross_bonus', 8)
                if float(macd_val) > float(msig_val):
                    buy_score += s
                    if float(prev.get('macd', 0)) <= float(prev.get('macd_signal', 0)):
                        buy_score += b
                else:
                    sell_score += s
                    if float(prev.get('macd', 0)) >= float(prev.get('macd_signal', 0)):
                        sell_score += b

        # 4. Bollinger Bands
        bbu = latest.get('bb_upper')
        bbl = latest.get('bb_lower')
        cls = latest.get('close')
        if bbu is not None and bbl is not None and not pd.isna(bbu):
            s = w('bollinger_bands', 15)
            if float(cls) < float(bbl):
                buy_score  += s
            elif float(cls) > float(bbu):
                sell_score += s

        # 5. Stochastic (legacy only)
        stk = latest.get('stoch_k')
        if stk is not None and not pd.isna(stk):
            s = w('stochastic', 12)
            if float(stk) < 20:
                buy_score  += s
            elif float(stk) > 80:
                sell_score += s

        # 6. Momentum
        mom = latest.get('momentum')
        if mom is not None and not pd.isna(mom):
            s = w('momentum', 10)
            if float(mom) > 0:
                buy_score  += s
            else:
                sell_score += s

        return buy_score, sell_score

    # ══════════════════════════════════════════════════════════════════
    # EXIT LEVELS
    # ══════════════════════════════════════════════════════════════════

    def _exit_levels(self, entry: float, action: str,
                     symbol: str, latest: pd.Series) -> Tuple[float, float]:
        n = self._norm

        # Pip value per instrument
        sym_upper = symbol.upper()
        if 'XAU' in sym_upper or 'GOLD' in sym_upper:
            pip_value = 0.01
        elif 'JPY' in sym_upper:
            pip_value = 0.01
        else:
            pip_value = 0.0001

        # ATR-based (preferred untuk advanced)
        atr_val = latest.get('atr')
        if n.get('use_atr_exit', False) and atr_val is not None and not pd.isna(atr_val):
            atr     = float(atr_val)
            sl_dist = atr * float(n.get('atr_multiplier_sl', 1.5))
            tp_dist = atr * float(n.get('atr_multiplier_tp', 2.5))
            if action == 'BUY':
                return round(entry - sl_dist, 5), round(entry + tp_dist, 5)
            else:
                return round(entry + sl_dist, 5), round(entry - tp_dist, 5)

        # Fixed pips fallback
        sl_dist = n.get('stop_loss_pips', 20) * pip_value
        tp_dist = n.get('take_profit_pips', 40) * pip_value
        if action == 'BUY':
            return round(entry - sl_dist, 5), round(entry + tp_dist, 5)
        else:
            return round(entry + sl_dist, 5), round(entry - tp_dist, 5)

    # ══════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _parse_pips(self, value, default=20) -> int:
        try:
            if isinstance(value, (int, float)):
                return int(value)
            nums = re.findall(r'\d+', str(value))
            return int(nums[0]) if nums else default
        except:
            return default

    def _extract_indicators(self, latest: pd.Series) -> Dict:
        keys = ['ema_fast', 'ema_slow', 'ma_fast', 'ma_slow', 'rsi',
                'macd', 'macd_signal', 'macd_histogram',
                'bb_upper', 'bb_middle', 'bb_lower',
                'atr', 'momentum', 'stoch_k', 'stoch_d',
                'body_ratio', 'volume_pct']
        result = {}
        for k in keys:
            v = latest.get(k)
            if v is not None and not pd.isna(v):
                result[k] = round(float(v), 6)
        return result

    def _hold(self, price: float, reason: str = "") -> Dict:
        return {
            'action': 'HOLD', 'confidence': 0, 'price': price,
            'timestamp': datetime.now(), 'strategy': self.strategy_name,
            'indicators': {}, 'hold_reason': reason,
            'lim_score': 0, 'dd_multiplier': 1.0,
            'vol_multiplier': 1.0, 'risk_multiplier': 1.0,
        }

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC API — dipanggil TradeExecutor & main.py
    # ══════════════════════════════════════════════════════════════════

    def get_risk_parameters(self) -> Dict:
        n = self._norm
        kelly_cfg = self.strategy_config.get('unique_features', {}).get('kelly_optimization', {})
        return {
            'risk_per_trade_min':  n['risk_per_trade_min'],
            'risk_per_trade_max':  n['risk_per_trade_max'],
            'max_leverage':        n.get('max_leverage', 100),
            'max_drawdown_limit':  n['max_drawdown_limit'],
            'daily_loss_limit':    n.get('daily_loss_limit', 0),
            'max_positions':       n['max_positions'],
            'portfolio_heat_cap':  n.get('portfolio_heat_cap', n['risk_per_trade_max'] * 3),
            'kelly_base':          kelly_cfg.get('base_kelly', 0.25) if kelly_cfg else 0.25,
        }

    def get_strategy_info(self) -> Dict:
        n = self._norm
        return {
            'name':      self.strategy_name,
            'format':    self._format,
            'philosophy': self.strategy_config.get('core_philosophy', 'N/A'),
            'timeframes': n.get('timeframes', ['M1']),
            'pairs':      n.get('trading_pairs', []),
            'performance_targets': self.strategy_config.get(
                'performance_targets',
                self.strategy_config.get('performance_targets_reference', {})
            ),
            'features': {
                'lim':              n.get('lim_enabled',            False),
                'dd_recovery':      'dre_l1_thresh' in n,
                'weekend_shield':   n.get('weekend_shield_enabled', False),
                'news_block':       n.get('news_block_enabled',     False),
                'spread_filter':    n.get('spread_filter_enabled',  False),
                'dynamic_leverage': n.get('leverage_enabled',       False),
                'atr_exit':         n.get('use_atr_exit',           False),
            }
        }