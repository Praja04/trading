"""
strategy_manager.py — v14 (Liquidity_Matrix_Engine Compatible)
===============================================================
Membaca TIGA format JSON:
  - Format lama  : { "parameters": {...}, "entry_conditions": {...}, ... }
  - Format baru  : { "quant_xxx": { "risk_management": {...}, ... } }
  - Format LME   : { "engine_core": {...}, "trading_session": {...}, ... }
                   (Liquidity_Matrix_Engine v4.1)

Fitur Liquidity_Matrix_Engine yang diimplementasikan:
  - Symbol detection dari base_symbol + auto_suffix
  - Dual session filter (summer/winter DST)
  - News filter (high/medium impact, pause before/after)
  - Entry engine: liquidity_sweep mode
  - Lot management: fixed lot
  - Grid engine: ATR-adaptive
  - Dynamic target engine (range/normal/strong/liquidity_expansion)
  - Partial take-profit (stage 1 + dynamic target)
  - Spread filter (max_spread_points)
  - Safety limits (max_orders_total, max_orders_per_side, min_free_margin)
  - Magic numbers (buy/sell/hedge)
  - Execution control (max_slippage_points, max_trades_per_day)
"""

import json
import logging
import os
import re
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, List


class StrategyManager:
    DB_PATH = "data/database/trading_data.db"

    def __init__(self, strategy_path: str = None):
        self.strategy_config = {}
        self.strategy_name   = "Default"
        self._norm           = {}
        self._format         = "legacy"

        # AI Capital Allocator — pair scores runtime state
        self._pair_scores: Dict[str, float] = {}
        self._pair_trade_results: Dict[str, List[float]] = {}

        # AI Supervisor / Performance Engine — runtime state
        self._trade_results: List[float] = []
        self._peak_equity: float = 0.0
        self._supervisor_frozen: bool = False
        self._safe_mode: bool = False

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

            # Unwrap single-key wrapper if present
            if len(raw) == 1 and isinstance(list(raw.values())[0], dict):
                self.strategy_config = list(raw.values())[0]
            else:
                self.strategy_config = raw

            # Try strategy_name from multiple locations
            self.strategy_name = (
                self.strategy_config.get('strategy_name') or
                self.strategy_config.get('engine_core', {}).get('name') or
                'Custom'
            )

            self._format = self._detect_format()
            self._norm   = self._normalize()

            logging.info(f"[OK] Strategy loaded: {self.strategy_name} (format={self._format})")
            self._log_strategy_info()
            return True

        except Exception as e:
            logging.error(f"Error loading strategy: {e}")
            self._load_default_strategy()
            return False

    def _detect_format(self) -> str:
        # LME format detection — has engine_core key
        if 'engine_core' in self.strategy_config:
            return "lme"

        advanced_keys = {
            'risk_management', 'symbol_management', 'general_parameters',
            'liquidity_imbalance_model', 'drawdown_recovery_engine',
            'broker_auto_detection', 'synthetic_macro_proxies'
        }
        if advanced_keys & set(self.strategy_config.keys()):
            return "advanced"

        return "legacy"

    def _normalize(self) -> Dict:
        cfg = self.strategy_config
        n   = {}

        if self._format == "lme":
            n = self._normalize_lme(cfg)
        elif self._format == "advanced":
            n = self._normalize_advanced(cfg)
        else:
            n = self._normalize_legacy(cfg)

        return n

    # ──────────────────────────────────────────────────────────────────
    # LME NORMALIZER  (Liquidity_Matrix_Engine v4.1)
    # ──────────────────────────────────────────────────────────────────
    def _normalize_lme(self, cfg: dict) -> dict:
        n = {}

        engine  = cfg.get('engine_core', {})
        sym_det = cfg.get('symbol_detection', {})
        lot_mgr = cfg.get('lot_management', {})
        grid    = cfg.get('grid_engine', {})
        dyn_tp  = cfg.get('dynamic_target_engine', {})
        partial = cfg.get('partial_takeprofit', {})
        exec_c  = cfg.get('execution_control', {})
        spread  = cfg.get('spread_filter', {})
        magic   = cfg.get('magic_numbers', {})
        safety  = cfg.get('safety_limits', {})
        news    = cfg.get('news_filter', {})
        session = cfg.get('trading_session', {})
        entry   = cfg.get('entry_engine', {})
        broker_t = cfg.get('broker_time_engine', {})

        # ── PAIRS ──────────────────────────────────────────────────────
        base = sym_det.get('base_symbol', 'XAUUSD')
        n['trading_pairs']       = [base]
        n['base_symbol']         = base
        n['auto_suffix']         = sym_det.get('auto_suffix_detection', True)

        # ── LOT MANAGEMENT ─────────────────────────────────────────────
        n['lot_mode']            = lot_mgr.get('mode', 'fixed')
        n['fixed_lot']           = float(lot_mgr.get('fixed_lot', 0.01))

        # For sizing compatibility — use fixed_lot as default
        n['risk_per_trade_min']  = 0.005
        n['risk_per_trade_max']  = 0.01
        n['compounding']         = False

        # ── MAGIC NUMBERS ──────────────────────────────────────────────
        n['buy_magic']           = int(magic.get('buy_magic',   88001))
        n['sell_magic']          = int(magic.get('sell_magic',  88002))
        n['hedge_magic']         = int(magic.get('hedge_magic', 88003))
        # Default magic (used by executor)
        n['magic_number']        = n['buy_magic']

        # ── EXECUTION CONTROL ──────────────────────────────────────────
        n['max_slippage_points'] = int(exec_c.get('max_slippage_points', 20))
        n['max_trades_per_day']  = int(exec_c.get('max_trades_per_day', 6))
        n['max_positions']       = int(exec_c.get('max_trades_per_day', 6))
        n['max_trades_day']      = n['max_positions']

        # ── SAFETY LIMITS ──────────────────────────────────────────────
        n['max_orders_total']         = int(safety.get('max_orders_total', 12))
        n['max_orders_per_side']      = int(safety.get('max_orders_per_side', 6))
        n['min_free_margin_percent']  = float(safety.get('min_free_margin_percent', 200))
        n['max_drawdown_limit']       = 0.20   # conservative default
        n['daily_loss_limit']         = 0.0
        n['portfolio_heat_cap']       = 0.10
        n['portfolio_heat_cap_weekday'] = 0.10
        n['portfolio_heat_cap_weekend'] = 0.05
        n['daily_profit_target']      = 0.0
        n['max_single_position_loss'] = -10.0
        n['max_leverage']             = 500

        # ── SPREAD FILTER ──────────────────────────────────────────────
        n['spread_filter_enabled']    = spread.get('enabled', True)
        # max_spread_points stored in points — will be converted in check_spread_filter
        n['max_spread_points']        = float(spread.get('max_spread_points', 50))
        n['max_spread_multiplier']    = 2.0     # fallback for ATR-based check
        n['max_micro_atr_spike']      = 2.0

        # ── NEWS FILTER ────────────────────────────────────────────────
        n['news_block_enabled']       = news.get('enabled', True)
        n['news_impact_levels']       = news.get('impact_levels', ['high', 'medium'])
        n['news_currency_filter']     = news.get('currency_filter', ['USD'])
        n['news_block_before_min']    = int(news.get('pause_before_minutes', 20))
        n['news_block_after_min']     = int(news.get('pause_after_minutes', 20))
        n['news_risk_minor']          = 0.7

        # ── SESSION FILTER ─────────────────────────────────────────────
        n['session_filter_enabled']   = True
        n['session_mode']             = session.get('session_mode', 'dual_session')
        n['session_summer']           = session.get('summer_dst', {})
        n['session_winter']           = session.get('winter_dst', {})
        n['broker_tz']                = broker_t.get('reference_timezone', 'GMT')
        n['auto_dst']                 = broker_t.get('auto_dst_adjustment', True)

        # ── ENTRY ENGINE ───────────────────────────────────────────────
        n['entry_mode']               = entry.get('entry_mode', 'liquidity_sweep')
        n['require_session_break']    = entry.get('require_session_break', True)
        n['require_rejection_candle'] = entry.get('require_rejection_candle', True)
        n['confirm_volume_spike']     = entry.get('confirm_volume_spike', True)

        # ── GRID ENGINE ────────────────────────────────────────────────
        n['grid_type']                = grid.get('type', 'ATR_ADAPTIVE')
        n['atr_period']               = int(grid.get('atr_period', 14))
        n['max_grid_levels']          = int(grid.get('max_grid_levels', 6))
        grid_modes                    = grid.get('grid_modes', {})
        n['grid_atr_mult_range']      = grid_modes.get('range_market',    {}).get('atr_multiplier', 1.2)
        n['grid_atr_mult_normal']     = grid_modes.get('normal_market',   {}).get('atr_multiplier', 1.4)
        n['grid_atr_mult_volatile']   = grid_modes.get('volatile_market', {}).get('atr_multiplier', 1.6)

        # ── DYNAMIC TARGET ENGINE ──────────────────────────────────────
        n['dynamic_target_enabled']   = dyn_tp.get('enabled', True)
        targets                       = dyn_tp.get('targets', {})
        n['target_range_market']      = int(targets.get('range_market',         200))
        n['target_normal_trend']      = int(targets.get('normal_trend',         300))
        n['target_strong_trend']      = int(targets.get('strong_trend',         400))
        n['target_liquidity_expansion'] = int(targets.get('liquidity_expansion', 500))
        det_params                    = dyn_tp.get('detection_parameters', {})
        n['atr_threshold_low']        = float(det_params.get('atr_threshold_low', 2.0))
        n['atr_threshold_high']       = float(det_params.get('atr_threshold_high', 3.5))
        n['trend_ema_period']         = int(det_params.get('trend_ema_period', 50))

        # ── PARTIAL TAKE-PROFIT ────────────────────────────────────────
        n['partial_tp_enabled']       = partial.get('enabled', True)
        n['tp_stage_1_points']        = int(partial.get('tp_stage_1', 150))
        n['tp_stage_2']               = partial.get('tp_stage_2', 'dynamic_target')

        # ── INDICATORS (shared with scoring engine) ────────────────────
        n['rsi_period']               = 14
        n['rsi_oversold']             = 30
        n['rsi_overbought']           = 70
        n['macd_fast']                = 12
        n['macd_slow']                = 26
        n['macd_signal_period']       = 9
        n['bb_period']                = 20
        n['bb_std']                   = 2.0

        # ── ATR EXIT ───────────────────────────────────────────────────
        n['use_atr_exit']             = True
        n['atr_multiplier_sl']        = n['grid_atr_mult_normal']
        n['atr_multiplier_tp']        = n['grid_atr_mult_normal'] * 2.0
        n['stop_loss_pips']           = 30
        n['take_profit_pips']         = 60

        # ── TIMEFRAMES ─────────────────────────────────────────────────
        n['timeframes']               = ['M1', 'M5', 'M15']
        n['execution_tf']             = 'M1'
        n['min_confidence']           = 55   # LME uses multi-confirm, lower threshold

        # ── SCORING ────────────────────────────────────────────────────
        n['scoring'] = {
            'ma_cross':         20,
            'ma_cross_bonus':    8,
            'rsi':              20,
            'macd':             20,
            'macd_cross_bonus':  8,
            'bollinger_bands':  15,
            'momentum':         10,
            'volume_spike':     15,   # LME-specific
            'rejection_candle': 15,   # LME-specific
            'session_break':    10,   # LME-specific
        }

        # ── ADVANCED FEATURES (disabled for LME — uses its own logic) ──
        n['lim_enabled']             = False
        n['leverage_enabled']        = False
        n['weekend_shield_enabled']  = False   # LME handles via session
        n['dxy_weights']             = {}
        n['aca_enabled']             = False
        n['cars_enabled']            = False
        n['supervisor_enabled']      = False
        n['dpe_enabled']             = False
        n['blocked_sessions']        = []

        return n

    # ──────────────────────────────────────────────────────────────────
    # ADVANCED NORMALIZER (unchanged from v13)
    # ──────────────────────────────────────────────────────────────────
    def _normalize_advanced(self, cfg: dict) -> dict:
        n = {}

        sym  = cfg.get('symbol_management', {})
        base = sym.get('base_symbols', {})
        pri  = base.get('primary', 'XAUUSD')
        sec  = base.get('secondary', [])
        n['trading_pairs'] = [pri] + (sec if isinstance(sec, list) else [])

        rm = cfg.get('risk_management', {})
        n['risk_per_trade_min']          = rm.get('risk_per_trade_min',         0.003)
        n['risk_per_trade_max']          = rm.get('risk_per_trade_max',         0.010)
        n['max_drawdown_limit']          = rm.get('max_total_drawdown',         0.04)
        n['daily_loss_limit']            = rm.get('daily_loss_limit',           0.02)
        n['portfolio_heat_cap_weekday']  = rm.get('portfolio_heat_cap_weekday', 0.032)
        n['portfolio_heat_cap_weekend']  = rm.get('portfolio_heat_cap_weekend', 0.018)
        n['portfolio_heat_cap']          = rm.get('portfolio_heat_cap_weekday', 0.032)
        n['daily_profit_target']         = rm.get('daily_profit_target',        0.0)
        n['max_single_position_loss']    = rm.get('max_single_position_loss',   -5.0)

        gp = cfg.get('general_parameters', {})
        n['max_positions']     = gp.get('max_trades_per_day', 6)
        n['max_trades_day']    = gp.get('max_trades_per_day', 6)
        n['execution_tf']      = gp.get('execution_timeframe', 'M5')
        n['compounding']       = gp.get('compounding_enabled', True)
        n['timeframes']        = [
            gp.get('micro_timeframe',     'M1'),
            gp.get('execution_timeframe', 'M5'),
            gp.get('trend_timeframe',     'M15'),
        ]
        n['min_confidence']    = gp.get('min_confidence', 65)
        n['magic_number']      = 234000
        n['max_leverage']      = 100

        n['atr_period']         = 14
        n['rsi_period']         = 14
        n['rsi_oversold']       = 30
        n['rsi_overbought']     = 70
        n['macd_fast']          = 12
        n['macd_slow']          = 26
        n['macd_signal_period'] = 9
        n['bb_period']          = 20
        n['bb_std']             = 2.0

        n['use_atr_exit']      = True
        n['atr_multiplier_sl'] = 1.5
        n['atr_multiplier_tp'] = 2.5
        n['stop_loss_pips']    = 20
        n['take_profit_pips']  = 40

        n['scoring'] = {
            'ma_cross': 20, 'ma_cross_bonus': 8,
            'rsi': 20, 'macd': 20, 'macd_cross_bonus': 8,
            'bollinger_bands': 15, 'momentum': 10, 'lim_bonus': 15,
        }

        dls = cfg.get('dynamic_leverage_scaling', {})
        n['leverage_enabled']        = dls.get('enabled', True)
        n['leverage_base_risk']      = dls.get('base_risk',            0.008)
        n['leverage_expansion_mult'] = dls.get('expansion_multiplier', 1.2)
        n['leverage_low_vol_mult']   = dls.get('low_vol_multiplier',   0.7)
        n['leverage_extreme_mult']   = dls.get('extreme_multiplier',   0.5)

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

        dre = cfg.get('drawdown_recovery_engine', {})
        n['dre_l1_thresh'] = dre.get('level_1_threshold',       0.01)
        n['dre_l2_thresh'] = dre.get('level_2_threshold',       0.02)
        n['dre_l3_thresh'] = dre.get('level_3_threshold',       0.03)
        n['dre_l1_mult']   = dre.get('risk_multiplier_level_1', 0.8)
        n['dre_l2_mult']   = dre.get('risk_multiplier_level_2', 0.6)
        n['dre_l3_mult']   = dre.get('risk_multiplier_level_3', 0.4)

        slo = cfg.get('execution_slippage_optimizer', {})
        n['spread_filter_enabled'] = slo.get('enabled', True)
        n['max_spread_multiplier'] = slo.get('max_spread_multiplier', 1.8)
        n['max_micro_atr_spike']   = slo.get('max_micro_atr_spike',   2.0)
        n['max_spread_points']     = 50

        ws = cfg.get('weekend_shield', {})
        n['weekend_shield_enabled'] = ws.get('enabled', True)
        n['weekend_hours_before']   = ws.get('activation_hours_before_close', 4)
        n['weekend_reduce_pct']     = ws.get('reduce_profitable_positions_percentage', 0.7)

        sf = cfg.get('session_filter', {})
        n['session_filter_enabled'] = sf.get('enabled', False)
        n['blocked_sessions']       = sf.get('blocked_sessions', [])

        sse = cfg.get('surprise_score_engine', {})
        n['news_block_enabled']    = sse.get('enabled', True)
        n['news_block_before_min'] = sse.get('block_minutes_before_event', 30)
        n['news_block_after_min']  = sse.get('block_minutes_after_event',  30)
        n['news_risk_minor']       = sse.get('risk_multiplier_minor', 0.7)

        smp = cfg.get('synthetic_macro_proxies', {})
        n['dxy_weights']           = smp.get('synthetic_dxy_weights', {})
        n['vol_percentile_window'] = smp.get('volatility_percentile_window', 60)

        aca = cfg.get('ai_capital_allocator', {})
        n['aca_enabled']             = aca.get('enabled', True)
        n['aca_rebalance_hours']     = aca.get('rebalance_interval_hours', 4)
        n['aca_eval_window']         = aca.get('evaluation_window_trades', 20)
        n['aca_disable_score_below'] = aca.get('disable_pair_score_below', 0.40)

        cars = cfg.get('cross_asset_risk_sentiment', {})
        n['cars_enabled']        = cars.get('enabled', True)
        n['cars_equity_weight']  = cars.get('equity_proxy_weight',    0.35)
        n['cars_vol_weight']     = cars.get('volatility_proxy_weight', 0.25)
        n['cars_dxy_weight']     = cars.get('dxy_proxy_weight',        0.20)
        n['cars_yield_weight']   = cars.get('yield_proxy_weight',      0.20)
        n['cars_risk_on_thresh'] = cars.get('risk_on_threshold',       0.5)
        n['cars_panic_thresh']   = cars.get('panic_threshold',        -0.5)
        n['cars_yield_symbol']   = smp.get('yield_proxy_symbol', 'USDJPY')

        sup = cfg.get('ai_supervisor_meta_layer', {})
        n['supervisor_enabled']        = sup.get('enabled', True)
        n['supervisor_drift_window']   = sup.get('model_drift_window_trades', 50)
        n['supervisor_corr_threshold'] = sup.get('correlation_threshold',     0.75)
        n['supervisor_freeze_dd']      = sup.get('freeze_optimization_above_dd', 0.02)
        n['supervisor_safe_mode_dd']   = sup.get('safe_mode_trigger_dd',      0.04)

        dpe = cfg.get('dynamic_performance_engine', {})
        n['dpe_enabled']     = dpe.get('enabled', True)
        calc_win             = dpe.get('calculation_windows', {})
        n['dpe_risk_window'] = calc_win.get('risk_trade_window',   50)
        n['dpe_winrate_window'] = calc_win.get('winrate_window',  100)
        n['dpe_pf_window']   = calc_win.get('profit_factor_window', 200)
        health               = dpe.get('health_thresholds', {})
        n['dpe_min_pf']      = health.get('min_profit_factor',   3.5)
        n['dpe_min_recovery'] = health.get('min_recovery_factor', 3.0)
        n['dpe_max_expected_dd'] = health.get('max_expected_dd', 0.05)

        ptr = cfg.get('performance_targets_reference', {})
        n['perf_monthly_return']  = ptr.get('expected_avg_monthly_return_range', [0.41, 0.46])
        n['perf_max_dd']          = ptr.get('expected_max_dd_range',             [0.023, 0.028])
        n['perf_profit_factor']   = ptr.get('expected_profit_factor_range',      [4.8, 5.5])
        n['perf_recovery_factor'] = ptr.get('expected_recovery_factor_range',    [6.0, 10.0])

        return n

    # ──────────────────────────────────────────────────────────────────
    # LEGACY NORMALIZER (unchanged from v13)
    # ──────────────────────────────────────────────────────────────────
    def _normalize_legacy(self, cfg: dict) -> dict:
        n = {}
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
        n['risk_per_trade_min']         = rr[0]
        n['risk_per_trade_max']         = rr[1]
        n['max_drawdown_limit']         = params.get('max_drawdown_limit', 0.15)
        n['daily_loss_limit']           = 0
        n['portfolio_heat_cap']         = rr[1] * params.get('max_positions', 3)
        n['portfolio_heat_cap_weekday'] = n['portfolio_heat_cap']
        n['portfolio_heat_cap_weekend'] = n['portfolio_heat_cap'] * 0.5
        n['compounding']                = False
        n['magic_number']               = 234000
        n['max_leverage']               = 100

        n['max_positions']  = params.get('max_positions', 3)
        n['max_trades_day'] = params.get('max_positions', 3)
        n['execution_tf']   = (params.get('timeframes', ['M1']) or ['M1'])[0]
        n['timeframes']     = params.get('timeframes', ['M1'])
        n['min_confidence'] = (
            entry_cfg.get('min_confidence') or params.get('min_confidence', 60)
        )

        n['atr_period']         = self._safe_period(indic.get('atr_period'), 14)
        n['rsi_period']         = self._safe_period(indic.get('rsi_period'), 14)
        n['rsi_oversold']       = self._safe_period(indic.get('rsi_oversold'), 30)
        n['rsi_overbought']     = self._safe_period(indic.get('rsi_overbought'), 70)
        n['macd_fast']          = self._safe_period(indic.get('macd_fast'), 12)
        n['macd_slow']          = self._safe_period(indic.get('macd_slow'), 26)
        n['macd_signal_period'] = self._safe_period(indic.get('macd_signal'), 9)
        n['bb_period']          = self._safe_period(indic.get('bb_period'), 20)
        n['bb_std']             = float(
            indic.get('bb_std_dev', 2.0) if not isinstance(indic.get('bb_std_dev'), dict) else 2.0
        )

        n['use_atr_exit']      = (exit_cfg.get('use_atr', False) or 'atr_multiplier_sl' in exit_cfg)
        n['atr_multiplier_sl'] = exit_cfg.get('atr_multiplier_sl', 1.5)
        n['atr_multiplier_tp'] = exit_cfg.get('atr_multiplier_tp', 2.5)
        n['stop_loss_pips']    = self._parse_pips(exit_cfg.get('stop_loss', exit_cfg.get('stop_loss_pips', 20)))
        n['take_profit_pips']  = self._parse_pips(exit_cfg.get('take_profit', exit_cfg.get('take_profit_pips', 40)))

        n['scoring'] = entry_cfg.get('scoring', {
            'ma_cross': 30, 'ma_cross_bonus': 10,
            'rsi': 25, 'macd': 25, 'macd_cross_bonus': 10,
            'bollinger_bands': 15, 'stochastic': 15, 'momentum': 10,
        })
        n['max_spread_points']       = 50
        n['lim_enabled']             = False
        n['leverage_enabled']        = False
        n['weekend_shield_enabled']  = False
        n['news_block_enabled']      = False
        n['spread_filter_enabled']   = False
        n['session_filter_enabled']  = False
        n['blocked_sessions']        = []
        n['dxy_weights']             = {}
        n['aca_enabled']             = False
        n['cars_enabled']            = False
        n['supervisor_enabled']      = False
        n['dpe_enabled']             = False
        n['daily_profit_target']     = 0.0
        n['max_single_position_loss'] = -5.0

        return n

    def _load_default_strategy(self):
        self.strategy_config = {}
        self._format = "legacy"
        self._norm = {
            'trading_pairs': [], 'risk_per_trade_min': 0.01, 'risk_per_trade_max': 0.02,
            'max_drawdown_limit': 0.15, 'daily_loss_limit': 0,
            'portfolio_heat_cap': 0.06, 'portfolio_heat_cap_weekday': 0.06,
            'portfolio_heat_cap_weekend': 0.03, 'compounding': False,
            'max_positions': 3, 'max_trades_day': 3, 'timeframes': ['M1'], 'execution_tf': 'M1',
            'min_confidence': 60, 'atr_period': 14, 'rsi_period': 14,
            'rsi_oversold': 30, 'rsi_overbought': 70, 'macd_fast': 12,
            'macd_slow': 26, 'macd_signal_period': 9, 'bb_period': 20, 'bb_std': 2.0,
            'use_atr_exit': False, 'atr_multiplier_sl': 1.5, 'atr_multiplier_tp': 2.5,
            'stop_loss_pips': 20, 'take_profit_pips': 40,
            'scoring': {'ma_cross': 35, 'rsi': 35, 'macd': 30},
            'magic_number': 234000, 'max_leverage': 100, 'max_spread_points': 50,
            'lim_enabled': False, 'leverage_enabled': False,
            'weekend_shield_enabled': False, 'news_block_enabled': False,
            'spread_filter_enabled': False, 'session_filter_enabled': False,
            'blocked_sessions': [], 'dxy_weights': {},
            'aca_enabled': False, 'cars_enabled': False,
            'supervisor_enabled': False, 'dpe_enabled': False,
            'daily_profit_target': 0.0, 'max_single_position_loss': -5.0,
        }
        self.strategy_name = "Default"

    def _log_strategy_info(self):
        n = self._norm
        logging.info("=" * 65)
        logging.info(f"STRATEGY    : {self.strategy_name}")
        logging.info(f"FORMAT      : {self._format}")
        logging.info(f"PAIRS       : {n.get('trading_pairs', [])}")
        logging.info(f"TIMEFRAME   : {n.get('execution_tf')}")
        if self._format == "lme":
            logging.info(f"LOT MODE    : {n.get('lot_mode')} ({n.get('fixed_lot')} lots)")
            logging.info(f"MAX TRADES  : {n.get('max_positions')} /day")
            logging.info(f"MAX ORDERS  : {n.get('max_orders_total')} total | {n.get('max_orders_per_side')}/side")
            logging.info(f"MIN MARGIN  : {n.get('min_free_margin_percent')}%")
            logging.info(f"ENTRY MODE  : {n.get('entry_mode')}")
            logging.info(f"GRID        : {n.get('grid_type')} | ATR x{n.get('grid_atr_mult_normal')}")
            logging.info(f"DYN TARGET  : {n.get('dynamic_target_enabled')} | Levels: {n.get('target_range_market')}/{n.get('target_normal_trend')}/{n.get('target_strong_trend')} pts")
            logging.info(f"PARTIAL TP  : {n.get('partial_tp_enabled')} | Stage1={n.get('tp_stage_1_points')} pts")
            logging.info(f"SPREAD FILT : {n.get('spread_filter_enabled')} (max {n.get('max_spread_points')} pts)")
            logging.info(f"NEWS FILTER : {n.get('news_block_enabled')} ({n.get('news_block_before_min')}m before / {n.get('news_block_after_min')}m after)")
            logging.info(f"SESSION     : {n.get('session_mode')}")
            logging.info(f"SLIPPAGE    : {n.get('max_slippage_points')} pts")
            logging.info(f"MAGIC (B/S) : {n.get('buy_magic')}/{n.get('sell_magic')}")
        else:
            logging.info(f"RISK        : {n['risk_per_trade_min']*100:.2f}% – {n['risk_per_trade_max']*100:.2f}%")
            logging.info(f"MAX POS     : {n.get('max_positions')} | COMPOUNDING: {n.get('compounding')}")
            logging.info(f"MIN CONF    : {n.get('min_confidence')}%")
            logging.info(f"ATR EXIT    : {n.get('use_atr_exit')} (SL x{n.get('atr_multiplier_sl')} / TP x{n.get('atr_multiplier_tp')})")
            logging.info(f"LIM         : {n.get('lim_enabled')}")
            logging.info(f"NEWS BLOCK  : {n.get('news_block_enabled')}")
            logging.info(f"SPREAD FILT : {n.get('spread_filter_enabled')}")
        logging.info("=" * 65)

    # ══════════════════════════════════════════════════════════════════
    # INDICATOR CALCULATION
    # ══════════════════════════════════════════════════════════════════

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            df    = df.copy()
            n     = self._norm

            if self._format == "lme":
                # LME uses EMA-9/21 + trend EMA-50
                df['ema_fast']  = df['close'].ewm(span=9,  adjust=False).mean()
                df['ema_slow']  = df['close'].ewm(span=21, adjust=False).mean()
                df['ema_trend'] = df['close'].ewm(span=n.get('trend_ema_period', 50), adjust=False).mean()
            elif self._format == "legacy":
                ec    = self.strategy_config.get('entry_conditions', {})
                indic = {**ec.get('indicators', {}), **ec.get('momentum_confirmation', {})}
                if 'ma_fast' in indic:
                    df['ma_fast'] = df['close'].rolling(self._safe_period(indic['ma_fast'], 9)).mean()
                if 'ma_slow' in indic:
                    df['ma_slow'] = df['close'].rolling(self._safe_period(indic['ma_slow'], 21)).mean()
                if 'ema_fast' in indic:
                    df['ema_fast'] = df['close'].ewm(span=self._safe_period(indic['ema_fast'], 9), adjust=False).mean()
                if 'ema_slow' in indic:
                    df['ema_slow'] = df['close'].ewm(span=self._safe_period(indic['ema_slow'], 21), adjust=False).mean()
                if 'stochastic_period' in indic:
                    st = self._calc_stoch(df, self._safe_period(indic['stochastic_period'], 14))
                    df['stoch_k'] = st['k']
                    df['stoch_d'] = st['d']
            else:
                df['ema_fast'] = df['close'].ewm(span=9,  adjust=False).mean()
                df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()

            df['rsi']  = self._calc_rsi(df['close'], n['rsi_period'])
            m = self._calc_macd(df['close'], n['macd_fast'], n['macd_slow'], n['macd_signal_period'])
            df['macd']           = m['macd']
            df['macd_signal']    = m['signal']
            df['macd_histogram'] = m['histogram']
            bb = self._calc_bb(df['close'], n['bb_period'], n['bb_std'])
            df['bb_upper']  = bb['upper']
            df['bb_middle'] = bb['middle']
            df['bb_lower']  = bb['lower']
            df['atr']       = self._calc_atr(df, n['atr_period'])
            df['momentum']  = df['close'].pct_change(periods=5) * 100

            if n.get('lim_enabled', False):
                if 'volume' in df.columns and len(df) >= 20:
                    df['volume_pct'] = df['volume'].rolling(20).apply(
                        lambda x: float((x.iloc[-1] > x.iloc[:-1]).mean()), raw=False
                    )
                df['body']       = np.abs(df['close'] - df['open'])
                df['body_ratio'] = df['body'] / (df['high'] - df['low']).replace(0, np.nan)

            # LME: volume spike detection
            if self._format == "lme" and 'volume' in df.columns:
                avg_vol = df['volume'].rolling(20).mean()
                df['volume_spike'] = df['volume'] > avg_vol * 1.5
                # Rejection candle: small body, large wick
                df['body']         = np.abs(df['close'] - df['open'])
                df['candle_range'] = df['high'] - df['low']
                df['rejection_candle'] = (df['body'] / df['candle_range'].replace(0, np.nan)) < 0.35

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
    # LME-SPECIFIC: SESSION FILTER
    # ══════════════════════════════════════════════════════════════════

    def _is_dst(self) -> bool:
        """Simple DST check: Northern Hemisphere — DST active March–October."""
        now = datetime.now()
        return 3 <= now.month <= 10

    def check_session_filter(self) -> bool:
        n = self._norm

        if not n.get('session_filter_enabled', False):
            return True

        if self._format == "lme":
            return self._check_lme_session()

        # Legacy/advanced: blocked_sessions list
        try:
            now_utc = datetime.now(timezone.utc)
            now_str = now_utc.strftime('%H:%M')
            for session in n.get('blocked_sessions', []):
                start = session.get('start_utc', '00:00')
                end   = session.get('end_utc',   '00:00')
                name  = session.get('name', 'unknown')
                if start <= now_str <= end:
                    logging.info(f"[SessionFilter] BLOCKED — {name} ({start}-{end} UTC)")
                    return False
            return True
        except Exception as e:
            logging.debug(f"SessionFilter error: {e}")
            return True

    def _check_lme_session(self) -> bool:
        """
        LME dual-session check. Returns True only if current time
        falls within one of the two configured trading windows.
        """
        n   = self._norm
        dst = self._is_dst() if n.get('auto_dst', True) else False

        sessions_cfg = n['session_summer'] if dst else n['session_winter']
        if not sessions_cfg:
            return True  # no config = allow all

        now = datetime.now()
        now_str = now.strftime('%H:%M')

        for key in ['session_1', 'session_2']:
            sess = sessions_cfg.get(key, {})
            start = sess.get('start', '00:00')
            end   = sess.get('end',   '23:59')
            if start <= now_str <= end:
                return True

        mode = "summer (DST)" if dst else "winter"
        s1   = sessions_cfg.get('session_1', {})
        s2   = sessions_cfg.get('session_2', {})
        logging.info(
            f"[LME SessionFilter] Outside session windows ({mode}): "
            f"{s1.get('start')}-{s1.get('end')} / {s2.get('start')}-{s2.get('end')}"
        )
        return False

    # ══════════════════════════════════════════════════════════════════
    # LME-SPECIFIC: SPREAD FILTER (uses points, not ATR)
    # ══════════════════════════════════════════════════════════════════

    def check_spread_filter(self, current_spread: float, df: pd.DataFrame) -> bool:
        n = self._norm
        if not n.get('spread_filter_enabled', False):
            return True

        if self._format == "lme":
            # current_spread passed in price units; convert to points
            # For XAUUSD: 1 point = 0.01
            symbol = n.get('base_symbol', 'XAUUSD')
            if 'XAU' in symbol or 'GOLD' in symbol:
                point = 0.01
            elif 'JPY' in symbol:
                point = 0.01
            else:
                point = 0.00001

            spread_in_points = current_spread / point if point > 0 else 0
            max_pts = n.get('max_spread_points', 50)

            if spread_in_points > max_pts:
                logging.info(
                    f"[LME SpreadFilter] BLOCKED spread={spread_in_points:.1f} pts > max={max_pts} pts"
                )
                return False
            return True

        # ATR-based (advanced/legacy)
        try:
            atr = float(df['atr'].iloc[-1])
            if pd.isna(atr) or atr <= 0:
                return True
            max_spread = atr * n.get('max_spread_multiplier', 1.8)
            if current_spread > max_spread:
                logging.info(f"[SpreadFilter] BLOCKED spread={current_spread:.5f} > max={max_spread:.5f}")
                return False
            return True
        except:
            return True

    # ══════════════════════════════════════════════════════════════════
    # LME-SPECIFIC: MARKET REGIME DETECTION
    # ══════════════════════════════════════════════════════════════════

    def detect_market_regime(self, df: pd.DataFrame) -> str:
        """
        Detect market regime for dynamic target selection.
        Returns: 'range_market' | 'normal_trend' | 'strong_trend' | 'liquidity_expansion'
        """
        n = self._norm
        if 'atr' not in df.columns or len(df) < 20:
            return 'normal_trend'

        try:
            atr_current = float(df['atr'].iloc[-1])
            atr_avg     = float(df['atr'].rolling(20).mean().iloc[-1])

            if atr_avg <= 0:
                return 'normal_trend'

            atr_ratio = atr_current / atr_avg
            low_th    = n.get('atr_threshold_low',  2.0)
            high_th   = n.get('atr_threshold_high', 3.5)

            # Check trend strength via EMA
            trend_str = 0.0
            if 'ema_fast' in df.columns and 'ema_trend' in df.columns:
                ema_f = float(df['ema_fast'].iloc[-1])
                ema_t = float(df['ema_trend'].iloc[-1])
                if ema_t > 0:
                    trend_str = abs(ema_f - ema_t) / ema_t

            if atr_ratio > high_th:
                regime = 'liquidity_expansion'
            elif atr_ratio > 2.0 or trend_str > 0.005:
                regime = 'strong_trend'
            elif atr_ratio < low_th * 0.6:
                regime = 'range_market'
            else:
                regime = 'normal_trend'

            logging.info(f"[LME Regime] {regime} | ATR ratio={atr_ratio:.2f} | Trend str={trend_str:.4f}")
            return regime

        except Exception as e:
            logging.debug(f"Regime detection error: {e}")
            return 'normal_trend'

    def get_dynamic_target_points(self, df: pd.DataFrame) -> int:
        """Return dynamic TP target in points based on market regime."""
        n = self._norm
        if not n.get('dynamic_target_enabled', True):
            return n.get('take_profit_pips', 60)

        regime = self.detect_market_regime(df)
        targets = {
            'range_market':         n.get('target_range_market',           200),
            'normal_trend':         n.get('target_normal_trend',           300),
            'strong_trend':         n.get('target_strong_trend',           400),
            'liquidity_expansion':  n.get('target_liquidity_expansion',    500),
        }
        return targets.get(regime, 300)

    def get_grid_atr_multiplier(self, df: pd.DataFrame) -> float:
        """Return ATR multiplier for grid spacing based on market regime."""
        n = self._norm
        regime = self.detect_market_regime(df)
        mult_map = {
            'range_market':        n.get('grid_atr_mult_range',    1.2),
            'normal_trend':        n.get('grid_atr_mult_normal',   1.4),
            'strong_trend':        n.get('grid_atr_mult_volatile', 1.6),
            'liquidity_expansion': n.get('grid_atr_mult_volatile', 1.6),
        }
        return mult_map.get(regime, 1.4)

    # ══════════════════════════════════════════════════════════════════
    # ADVANCED FEATURE ENGINES (from v13, unchanged)
    # ══════════════════════════════════════════════════════════════════

    def compute_liquidity_imbalance_score(self, df: pd.DataFrame, current_spread: float = 0) -> float:
        n = self._norm
        if not n.get('lim_enabled', False) or len(df) < 5:
            return 0.5
        try:
            latest = df.iloc[-1]
            score  = 0.0
            if 'volume' in df.columns:
                avg_vol = df['volume'].iloc[-20:].mean()
                spike   = min(float(latest['volume']) / max(avg_vol, 1), 3) / 3
                score  += spike * n['lim_volume_weight']
            vol_pct = float(latest['volume_pct']) if 'volume_pct' in df.columns else 0.5
            score  += vol_pct * n['lim_tick_weight']
            body_ratio = float(latest.get('body_ratio', 0.5)) if 'body_ratio' in df.columns else 0.5
            score     += min(body_ratio, 1.0) * n['lim_body_weight']
            if 'atr' in df.columns and not pd.isna(latest['atr']) and float(latest['atr']) > 0:
                spread_score = max(0.0, 1.0 - current_spread / float(latest['atr']))
            else:
                spread_score = 0.5
            score += spread_score * n['lim_spread_weight']
            if 'atr' in df.columns and not pd.isna(latest['atr']):
                atr_max     = float(df['atr'].rolling(20).max().iloc[-1])
                micro_score = 1.0 - min(float(latest['atr']) / max(atr_max, 1e-9), 1.0)
            else:
                micro_score = 0.5
            score += micro_score * n['lim_micro_atr_weight']
            return round(min(max(score, 0.0), 1.0), 4)
        except Exception as e:
            logging.debug(f"LIM error: {e}")
            return 0.5

    def compute_drawdown_risk_multiplier(self, current_equity: float, peak_equity: float) -> float:
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

    def check_weekend_shield(self) -> bool:
        n = self._norm
        if not n.get('weekend_shield_enabled', False):
            return True
        now = datetime.now()
        if now.weekday() in (5, 6):
            logging.info("[WeekendShield] Weekend — no new trades")
            return False
        if now.weekday() == 4:
            hours_left = 22 - now.hour
            if hours_left <= n.get('weekend_hours_before', 4):
                logging.info(f"[WeekendShield] {hours_left}h to close — blocking new trades")
                return False
        return True

    def get_portfolio_heat_cap(self) -> float:
        n   = self._norm
        now = datetime.now()
        if now.weekday() in (5, 6):
            return n.get('portfolio_heat_cap_weekend', n.get('portfolio_heat_cap', 0.032))
        return n.get('portfolio_heat_cap_weekday', n.get('portfolio_heat_cap', 0.032))

    def check_daily_profit_target(self, current_equity: float, balance: float) -> bool:
        n = self._norm
        target = n.get('daily_profit_target', 0.0)
        if target <= 0:
            return True
        daily_profit = current_equity - balance
        if daily_profit >= target:
            logging.info(
                f"[DailyTarget] Profit ${daily_profit:.2f} >= target ${target:.2f} — STOP trading hari ini"
            )
            return False
        return True

    def check_news_block(self, symbol: str) -> bool:
        n = self._norm
        if not n.get('news_block_enabled', False):
            return True
        try:
            if not os.path.exists(self.DB_PATH):
                return True
            before_min = n.get('news_block_before_min', 30)
            after_min  = n.get('news_block_after_min',  30)
            currencies = self._currencies_from_symbol(symbol)

            # LME has specific currency filter
            if self._format == "lme":
                currency_filter = n.get('news_currency_filter', ['USD'])
                currencies = [c for c in currencies if c in currency_filter] or currencies

            conn   = sqlite3.connect(self.DB_PATH)
            cursor = conn.cursor()
            ph     = ','.join(['?' for _ in currencies])
            cursor.execute(f'''
                SELECT title, currency, event_time FROM news
                WHERE currency IN ({ph})
                  AND impact IN ('High', 'high', 'Medium', 'medium')
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
        # Handle XAU/GOLD special cases
        if 'XAU' in s:
            return ['XAU', 'USD']
        if 'GOLD' in s:
            return ['XAU', 'USD']
        if len(s) >= 6:
            return [s[:3], s[3:6]]
        return [s]

    def check_min_free_margin(self) -> bool:
        """LME safety: check minimum free margin percentage."""
        n = self._norm
        if self._format != "lme":
            return True
        try:
            import MetaTrader5 as mt5
            account = mt5.account_info()
            if account is None:
                return True
            if account.margin > 0:
                free_margin_pct = (account.margin_free / account.margin) * 100
            else:
                free_margin_pct = 999.0

            min_pct = n.get('min_free_margin_percent', 200)
            if free_margin_pct < min_pct:
                logging.warning(
                    f"[LME Safety] Free margin {free_margin_pct:.1f}% < min {min_pct}% — BLOCKED"
                )
                return False
            return True
        except Exception as e:
            logging.debug(f"Margin check error: {e}")
            return True

    # ══════════════════════════════════════════════════════════════════
    # AI CAPITAL ALLOCATOR (unchanged from v13)
    # ══════════════════════════════════════════════════════════════════

    def update_pair_result(self, symbol: str, profit: float):
        if symbol not in self._pair_trade_results:
            self._pair_trade_results[symbol] = []
        self._pair_trade_results[symbol].append(profit)
        self._trade_results.append(profit)
        self._recalculate_pair_score(symbol)

    def _recalculate_pair_score(self, symbol: str):
        n = self._norm
        window  = n.get('aca_eval_window', 20)
        results = self._pair_trade_results.get(symbol, [])[-window:]
        if len(results) < 3:
            self._pair_scores[symbol] = 0.6
            return
        wins    = [r for r in results if r > 0]
        losses  = [r for r in results if r < 0]
        winrate = len(wins) / len(results)
        gross_profit = sum(wins) if wins else 0
        gross_loss   = abs(sum(losses)) if losses else 1e-9
        pf           = gross_profit / gross_loss
        pf_norm = min(pf / 5.0, 1.0)
        score   = winrate * 0.5 + pf_norm * 0.5
        self._pair_scores[symbol] = round(score, 4)
        logging.info(f"[ACA] {symbol} score={score:.3f} (wr={winrate:.1%} pf={pf:.2f})")

    def check_pair_enabled(self, symbol: str) -> bool:
        n = self._norm
        if not n.get('aca_enabled', False):
            return True
        score     = self._pair_scores.get(symbol, 0.6)
        threshold = n.get('aca_disable_score_below', 0.40)
        if score < threshold:
            logging.info(f"[ACA] {symbol} DISABLED — score {score:.3f} < threshold {threshold:.3f}")
            return False
        return True

    def get_pair_risk_allocation(self, symbol: str, base_risk: float) -> float:
        n = self._norm
        if not n.get('aca_enabled', False):
            return base_risk
        score = self._pair_scores.get(symbol, 0.6)
        multiplier = 0.7 + (score - 0.4) * (1.3 / 0.6)
        multiplier = max(0.5, min(multiplier, 1.3))
        adjusted   = base_risk * multiplier
        logging.info(f"[ACA] {symbol} risk_alloc x{multiplier:.2f} → {adjusted*100:.3f}%")
        return adjusted

    # ══════════════════════════════════════════════════════════════════
    # CROSS-ASSET RISK SENTIMENT (unchanged from v13)
    # ══════════════════════════════════════════════════════════════════

    def compute_risk_sentiment(self, tick_data: Dict[str, Dict]) -> float:
        n = self._norm
        if not n.get('cars_enabled', False) or not tick_data:
            return 0.0
        try:
            dxy_weights  = n.get('dxy_weights', {})
            score        = 0.0
            weight_total = 0.0
            dxy_score = 0.0
            dxy_wt    = 0.0
            for sym, w in dxy_weights.items():
                key = self._find_tick_key(tick_data, sym)
                if key:
                    mid = (tick_data[key]['bid'] + tick_data[key]['ask']) / 2
                    dxy_score += w * mid
                    dxy_wt    += abs(w)
            if dxy_wt > 0:
                dxy_norm   = dxy_score / dxy_wt
                dxy_signal = max(-1.0, min(dxy_norm * 100, 1.0))
                score        += (-dxy_signal) * n.get('cars_dxy_weight', 0.20)
                weight_total += n.get('cars_dxy_weight', 0.20)
            yield_sym = n.get('cars_yield_symbol', 'USDJPY')
            yield_key  = self._find_tick_key(tick_data, yield_sym)
            if yield_key:
                usdjpy = (tick_data[yield_key]['bid'] + tick_data[yield_key]['ask']) / 2
                yield_signal = (usdjpy - 130) / 30
                yield_signal = max(-1.0, min(yield_signal, 1.0))
                score        += yield_signal * n.get('cars_yield_weight', 0.20)
                weight_total += n.get('cars_yield_weight', 0.20)
            if weight_total > 0:
                score /= weight_total
            else:
                score = 0.0
            score = max(-1.0, min(score, 1.0))
            return round(score, 4)
        except Exception as e:
            logging.debug(f"CARS error: {e}")
            return 0.0

    def _find_tick_key(self, tick_data: Dict, symbol: str) -> str:
        sym_clean = symbol.upper().replace('.S', '').replace('_', '')
        for k in tick_data:
            k_clean = k.upper().replace('.S', '').replace('_', '')
            if k_clean == sym_clean or k_clean.startswith(sym_clean):
                return k
        return None

    def sentiment_to_risk_multiplier(self, sentiment_score: float) -> float:
        n = self._norm
        if not n.get('cars_enabled', False):
            return 1.0
        panic_th   = n.get('cars_panic_thresh',   -0.5)
        risk_on_th = n.get('cars_risk_on_thresh',  0.5)
        if sentiment_score <= panic_th:
            return 0.5
        elif sentiment_score >= risk_on_th:
            return 1.1
        else:
            return 0.5 + (sentiment_score - panic_th) / (risk_on_th - panic_th) * 0.6

    # ══════════════════════════════════════════════════════════════════
    # AI SUPERVISOR META LAYER (unchanged from v13)
    # ══════════════════════════════════════════════════════════════════

    def update_supervisor(self, current_equity: float):
        n = self._norm
        if not n.get('supervisor_enabled', False):
            return
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        if self._peak_equity <= 0:
            return
        dd = (self._peak_equity - current_equity) / self._peak_equity
        safe_dd = n.get('supervisor_safe_mode_dd', 0.04)
        if dd >= safe_dd and not self._safe_mode:
            self._safe_mode = True
            logging.warning(f"[Supervisor] SAFE MODE activated — DD={dd:.2%}")
        elif dd < safe_dd and self._safe_mode:
            self._safe_mode = False
            logging.info("[Supervisor] Safe mode deactivated")
        freeze_dd = n.get('supervisor_freeze_dd', 0.02)
        if dd >= freeze_dd and not self._supervisor_frozen:
            self._supervisor_frozen = True
            logging.warning(f"[Supervisor] OPTIMIZATION FROZEN — DD={dd:.2%}")
        elif dd < freeze_dd and self._supervisor_frozen:
            self._supervisor_frozen = False
            logging.info("[Supervisor] Optimization unfrozen")

    def is_safe_mode(self) -> bool:
        return self._safe_mode

    def is_optimization_frozen(self) -> bool:
        return self._supervisor_frozen

    def get_supervisor_risk_multiplier(self) -> float:
        if self._safe_mode:
            return 0.3
        return 1.0

    # ══════════════════════════════════════════════════════════════════
    # DYNAMIC PERFORMANCE ENGINE (unchanged from v13)
    # ══════════════════════════════════════════════════════════════════

    def get_dynamic_performance(self) -> Dict:
        n       = self._norm
        results = self._trade_results
        if len(results) < 5:
            return {'status': 'insufficient_data', 'trades': len(results)}
        w_risk    = results[-n.get('dpe_risk_window',   50):]
        w_winrate = results[-n.get('dpe_winrate_window',100):]
        w_pf      = results[-n.get('dpe_pf_window',    200):]
        wins_pf  = [r for r in w_pf if r > 0]
        loss_pf  = [r for r in w_pf if r < 0]
        pf       = sum(wins_pf) / abs(sum(loss_pf)) if loss_pf else float('inf')
        wins_wr  = [r for r in w_winrate if r > 0]
        winrate  = len(wins_wr) / len(w_winrate)
        net_profit = sum(results)
        max_loss   = abs(min(results)) if results else 0
        recovery   = net_profit / max_loss if max_loss > 0 else 0
        health = self._evaluate_health(pf, recovery)
        return {
            'status': 'ok', 'trades': len(results),
            'profit_factor': round(pf, 3), 'win_rate': round(winrate, 4),
            'recovery_factor': round(recovery, 3), 'net_profit': round(net_profit, 2),
            'health': health, 'frozen': self._supervisor_frozen, 'safe_mode': self._safe_mode,
        }

    def _evaluate_health(self, pf: float, recovery: float) -> str:
        n = self._norm
        min_pf       = n.get('dpe_min_pf',       3.5)
        min_recovery = n.get('dpe_min_recovery',  3.0)
        if pf >= min_pf and recovery >= min_recovery:
            return 'HEALTHY'
        elif pf >= min_pf * 0.7 and recovery >= min_recovery * 0.7:
            return 'WARNING'
        return 'CRITICAL'

    def get_performance_risk_multiplier(self) -> float:
        n = self._norm
        if not n.get('dpe_enabled', False) or len(self._trade_results) < 10:
            return 1.0
        perf = self.get_dynamic_performance()
        if perf.get('health') == 'CRITICAL':
            return 0.5
        elif perf.get('health') == 'WARNING':
            return 0.75
        return 1.0

    # ══════════════════════════════════════════════════════════════════
    # MAIN ANALYSIS
    # ══════════════════════════════════════════════════════════════════

    def analyze(self, symbol: str, df: pd.DataFrame,
                current_spread: float = 0,
                current_equity: float = 0,
                peak_equity: float = 0,
                tick_data: Dict = None) -> Dict:
        try:
            df = self.calculate_indicators(df)
            if len(df) < 2:
                return self._hold(df.iloc[-1]['close'] if len(df) > 0 else 0)

            latest = df.iloc[-1]
            prev   = df.iloc[-2]
            n      = self._norm

            if current_equity > 0:
                self.update_supervisor(current_equity)

            # Gate 0: Safe Mode
            if self.is_safe_mode():
                return self._hold(float(latest['close']), "supervisor_safe_mode")

            # Gate 1: Weekend Shield
            if not self.check_weekend_shield():
                return self._hold(float(latest['close']), "weekend_shield")

            # Gate 1b: Session Filter (LME dual-session aware)
            if not self.check_session_filter():
                return self._hold(float(latest['close']), "session_filter_block")

            # Gate 1c: Daily Profit Target
            if current_equity > 0 and peak_equity > 0:
                if not self.check_daily_profit_target(current_equity, peak_equity):
                    return self._hold(float(latest['close']), "daily_profit_target_reached")

            # Gate 2: News Block
            if not self.check_news_block(symbol):
                return self._hold(float(latest['close']), "news_block")

            # Gate 3: Spread Filter (LME: points-based)
            if not self.check_spread_filter(current_spread, df):
                return self._hold(float(latest['close']), "spread_too_wide")

            # Gate 3b: LME min free margin
            if self._format == "lme" and not self.check_min_free_margin():
                return self._hold(float(latest['close']), "insufficient_free_margin")

            # Gate 4: AI Capital Allocator
            if not self.check_pair_enabled(symbol):
                return self._hold(float(latest['close']), "aca_pair_disabled")

            # ── Base Scoring ──────────────────────────────────────────
            buy_score, sell_score = self._evaluate_conditions(latest, prev, df)

            # ── LIM Adjustment ────────────────────────────────────────
            lim_score = self.compute_liquidity_imbalance_score(df, current_spread)
            if n.get('lim_enabled', False):
                extreme_th = n.get('lim_extreme_threshold', 0.8)
                strong_th  = n.get('lim_strong_threshold',  0.6)
                lim_bonus  = n['scoring'].get('lim_bonus', 0)
                if lim_score >= extreme_th:
                    factor = n.get('lim_risk_reduction', 0.7)
                    buy_score  = int(buy_score  * factor)
                    sell_score = int(sell_score * factor)
                elif lim_score >= strong_th:
                    bonus = int(lim_bonus * lim_score)
                    buy_score  += bonus
                    sell_score += bonus

            # ── Risk Multipliers ──────────────────────────────────────
            dd_mult         = self.compute_drawdown_risk_multiplier(current_equity, peak_equity) \
                              if current_equity > 0 and peak_equity > 0 else 1.0
            vol_mult        = self.compute_volatility_risk_multiplier(df)
            supervisor_mult = self.get_supervisor_risk_multiplier()
            perf_mult       = self.get_performance_risk_multiplier()

            sentiment_score = 0.0
            sentiment_mult  = 1.0
            if n.get('cars_enabled', False) and tick_data:
                sentiment_score = self.compute_risk_sentiment(tick_data)
                sentiment_mult  = self.sentiment_to_risk_multiplier(sentiment_score)

            aca_pair_risk_mult = 1.0
            if n.get('aca_enabled', False):
                base_risk = (n['risk_per_trade_min'] + n['risk_per_trade_max']) / 2
                adj_risk  = self.get_pair_risk_allocation(symbol, base_risk)
                aca_pair_risk_mult = adj_risk / base_risk if base_risk > 0 else 1.0

            total_risk_mult = dd_mult * vol_mult * supervisor_mult * perf_mult * sentiment_mult

            # ── Decision ─────────────────────────────────────────────
            min_conf = n.get('min_confidence', 60)
            signal   = {
                'action':           'HOLD',
                'confidence':       0,
                'price':            float(latest['close']),
                'timestamp':        datetime.now(),
                'strategy':         self.strategy_name,
                'format':           self._format,
                'indicators':       self._extract_indicators(latest),
                'lim_score':        lim_score,
                'dd_multiplier':    dd_mult,
                'vol_multiplier':   vol_mult,
                'sentiment_score':  sentiment_score,
                'sentiment_mult':   sentiment_mult,
                'supervisor_mult':  supervisor_mult,
                'perf_mult':        perf_mult,
                'aca_mult':         aca_pair_risk_mult,
                'risk_multiplier':  total_risk_mult,
                'hold_reason':      '',
                'safe_mode':        self._safe_mode,
                'frozen':           self._supervisor_frozen,
            }

            if buy_score > sell_score and buy_score >= min_conf:
                sl, tp = self._exit_levels(float(latest['close']), 'BUY', symbol, latest, df)
                signal.update({'action': 'BUY', 'confidence': buy_score,
                               'stop_loss': sl, 'take_profit': tp})

            elif sell_score > buy_score and sell_score >= min_conf:
                sl, tp = self._exit_levels(float(latest['close']), 'SELL', symbol, latest, df)
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

    def _evaluate_conditions(self, latest: pd.Series, prev: pd.Series,
                              df: pd.DataFrame = None) -> Tuple[int, int]:
        buy_score  = 0
        sell_score = 0
        scoring    = self._norm.get('scoring', {})
        n          = self._norm

        def w(key, default=20):
            return scoring.get(key, default)

        # MA/EMA Cross
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

        # RSI
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

        # MACD
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

        # Bollinger Bands
        bbu = latest.get('bb_upper')
        bbl = latest.get('bb_lower')
        cls = latest.get('close')
        if bbu is not None and bbl is not None and not pd.isna(bbu):
            s = w('bollinger_bands', 15)
            if float(cls) < float(bbl):
                buy_score  += s
            elif float(cls) > float(bbu):
                sell_score += s

        # Stochastic (legacy)
        stk = latest.get('stoch_k')
        if stk is not None and not pd.isna(stk):
            s = w('stochastic', 12)
            if float(stk) < 20:
                buy_score  += s
            elif float(stk) > 80:
                sell_score += s

        # Momentum
        mom = latest.get('momentum')
        if mom is not None and not pd.isna(mom):
            s = w('momentum', 10)
            if float(mom) > 0:
                buy_score  += s
            else:
                sell_score += s

        # LME-specific signals
        if self._format == "lme":
            # Volume spike confirmation
            vol_spike = latest.get('volume_spike')
            if vol_spike is not None and bool(vol_spike):
                s = w('volume_spike', 15)
                # Volume spike in direction of trend
                ema_f = latest.get('ema_fast')
                ema_s = latest.get('ema_slow')
                if ema_f is not None and ema_s is not None and not pd.isna(ema_f):
                    if float(ema_f) > float(ema_s):
                        buy_score  += s
                    else:
                        sell_score += s

            # Rejection candle
            rej = latest.get('rejection_candle')
            if rej is not None and bool(rej):
                s = w('rejection_candle', 15)
                # Rejection at support → BUY, at resistance → SELL
                if float(cls) < float(latest.get('ema_slow', float(cls))):
                    buy_score  += s   # rejection at support
                else:
                    sell_score += s   # rejection at resistance

            # Trend filter via ema_trend
            ema_trend = latest.get('ema_trend')
            if ema_trend is not None and not pd.isna(ema_trend):
                s = w('session_break', 10)
                if float(cls) > float(ema_trend):
                    buy_score  += s
                else:
                    sell_score += s

        return buy_score, sell_score

    # ══════════════════════════════════════════════════════════════════
    # EXIT LEVELS  (LME: dynamic target in points)
    # ══════════════════════════════════════════════════════════════════

    def _exit_levels(self, entry: float, action: str,
                     symbol: str, latest: pd.Series,
                     df: pd.DataFrame = None) -> Tuple[float, float]:
        n = self._norm
        sym_upper = symbol.upper()
        if 'XAU' in sym_upper or 'GOLD' in sym_upper:
            pip_value = 0.01
        elif 'JPY' in sym_upper:
            pip_value = 0.01
        else:
            pip_value = 0.0001

        if self._format == "lme" and n.get('dynamic_target_enabled', True) and df is not None:
            # Use ATR-based SL
            atr_val = latest.get('atr')
            if atr_val is not None and not pd.isna(atr_val):
                atr     = float(atr_val)
                mult    = self.get_grid_atr_multiplier(df)
                sl_dist = atr * mult

                # Dynamic TP in points
                tp_points = self.get_dynamic_target_points(df)
                tp_dist   = tp_points * pip_value

                if action == 'BUY':
                    return round(entry - sl_dist, 5), round(entry + tp_dist, 5)
                else:
                    return round(entry + sl_dist, 5), round(entry - tp_dist, 5)

        # ATR-based exit (advanced/legacy)
        atr_val = latest.get('atr')
        if n.get('use_atr_exit', False) and atr_val is not None and not pd.isna(atr_val):
            atr     = float(atr_val)
            sl_dist = atr * float(n.get('atr_multiplier_sl', 1.5))
            tp_dist = atr * float(n.get('atr_multiplier_tp', 2.5))
            if action == 'BUY':
                return round(entry - sl_dist, 5), round(entry + tp_dist, 5)
            else:
                return round(entry + sl_dist, 5), round(entry - tp_dist, 5)

        sl_dist = n.get('stop_loss_pips', 20) * pip_value
        tp_dist = n.get('take_profit_pips', 40) * pip_value
        if action == 'BUY':
            return round(entry - sl_dist, 5), round(entry + tp_dist, 5)
        else:
            return round(entry + sl_dist, 5), round(entry - tp_dist, 5)

    # ══════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _safe_period(self, val, default: int) -> int:
        if val is None:
            return default
        if isinstance(val, dict):
            for key in ('period', 'value', 'span', 'length', 'window'):
                if key in val:
                    return int(val[key])
            first = next(iter(val.values()), default)
            try:
                return int(first)
            except:
                return default
        try:
            return int(val)
        except:
            return default

    def _parse_pips(self, value, default=20) -> int:
        try:
            if isinstance(value, (int, float)):
                return int(value)
            nums = re.findall(r'\d+', str(value))
            return int(nums[0]) if nums else default
        except:
            return default

    def _extract_indicators(self, latest: pd.Series) -> Dict:
        keys = ['ema_fast', 'ema_slow', 'ema_trend', 'ma_fast', 'ma_slow',
                'rsi', 'macd', 'macd_signal', 'macd_histogram',
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
            'vol_multiplier': 1.0, 'sentiment_score': 0.0,
            'sentiment_mult': 1.0, 'supervisor_mult': 1.0,
            'perf_mult': 1.0, 'aca_mult': 1.0, 'risk_multiplier': 1.0,
            'safe_mode': self._safe_mode,
            'frozen': self._supervisor_frozen,
        }

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════════

    def get_risk_parameters(self) -> Dict:
        n = self._norm
        kelly_cfg = self.strategy_config.get('unique_features', {}).get('kelly_optimization', {})
        return {
            'risk_per_trade_min':        n['risk_per_trade_min'],
            'risk_per_trade_max':        n['risk_per_trade_max'],
            'max_leverage':              n.get('max_leverage', 100),
            'max_drawdown_limit':        n['max_drawdown_limit'],
            'daily_loss_limit':          n.get('daily_loss_limit', 0),
            'max_positions':             n['max_positions'],
            'portfolio_heat_cap':        self.get_portfolio_heat_cap(),
            'kelly_base':                kelly_cfg.get('base_kelly', 0.25) if kelly_cfg else 0.25,
            'compounding':               n.get('compounding', False),
            'max_single_position_loss':  n.get('max_single_position_loss', -5.0),
            'daily_profit_target':       n.get('daily_profit_target', 0.0),
            # LME extras
            'lot_mode':                  n.get('lot_mode', 'risk_based'),
            'fixed_lot':                 n.get('fixed_lot', None),
            'magic_number':              n.get('magic_number', 234000),
            'buy_magic':                 n.get('buy_magic', n.get('magic_number', 234000)),
            'sell_magic':                n.get('sell_magic', n.get('magic_number', 234000)),
            'max_orders_total':          n.get('max_orders_total', n['max_positions']),
            'max_orders_per_side':       n.get('max_orders_per_side', n['max_positions'] // 2),
            'max_slippage_points':       n.get('max_slippage_points', 20),
        }

    def get_strategy_info(self) -> Dict:
        n = self._norm
        return {
            'name':      self.strategy_name,
            'format':    self._format,
            'philosophy': self.strategy_config.get('core_philosophy',
                          self.strategy_config.get('engine_core', {}).get('mode', 'N/A')),
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
                'ai_allocator':     n.get('aca_enabled',            False),
                'risk_sentiment':   n.get('cars_enabled',           False),
                'supervisor':       n.get('supervisor_enabled',     False),
                'perf_engine':      n.get('dpe_enabled',            False),
                'compounding':      n.get('compounding',            False),
                # LME-specific
                'session_filter':   n.get('session_filter_enabled', False),
                'dynamic_target':   n.get('dynamic_target_enabled', False),
                'partial_tp':       n.get('partial_tp_enabled',     False),
                'grid_engine':      self._format == "lme",
            },
        }

    def get_pair_scores(self) -> Dict:
        return dict(self._pair_scores)

    def get_performance_summary(self) -> Dict:
        return self.get_dynamic_performance()