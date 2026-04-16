"""
market_discovery.py — Polymarket weather market discovery tool.
JARVIS Z EDITION (Phase 2 Refactored)

Usage:
    python market_discovery.py --inspect   # dump raw API samples and exit
    python market_discovery.py             # run full discovery
    python market_discovery.py --paper-loop # run continuous paper trading
"""

import os
import sys
import time
import signal
import threading
import multiprocessing
import fcntl
from datetime import datetime, timezone

# Add parent directory to path to ensure internal package is importable
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from market_discovery_internal.config import (
    TARGET_CITIES, DAILY_RESOLVE_ONLY, DAILY_MIN_HOURS_TO_RESOLVE,
    STRATEGY_MAX_YES_PRICE, STRATEGY_MIN_MODEL_PROB, STRATEGY_MIN_EDGE,
    PAPER_ENTRY_MIN_PRICE, PAPER_ENTRY_MAX_PRICE, PAPER_STAKE_USD,
    PAPER_STATE_FILE, PAPER_LOOP_INTERVAL_SECONDS, PAPER_LOOP_CONTINUE_ON_ERROR,
    PAPER_LOOP_ERROR_BACKOFF_SECONDS, PAPER_LOOP_MAX_ERROR_BACKOFF_SECONDS,
    PAPER_MAX_OPEN_POSITIONS, PAPER_MIN_CITY_DIVERSITY, PAPER_JOURNAL_MAX_ENTRIES,
    DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN, DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES,
    DISCOVERY_FORECAST_PREFETCH_MIN_KEYS, DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS,
    PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS, PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS,
    HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT, THRESHOLD_RE, WEATHER_CONTEXT_RE,
    DIRECTION_CANDIDATE_RE, WS_STALE_DETECTION_MINUTES
)
from market_discovery_internal.utils import (
    fetch_with_retry, _safe_float, _safe_div, _clamp, _parse_iso_utc,
    _load_json_blob, _save_json_blob
)
from market_discovery_internal.parsing import (
    parse_market, _market_search_text, _has_target_city, _match_target_city,
    _is_temperature_market_candidate, _log_unmatched, _normalize_city_key
)
from market_discovery_internal.pricing import (
    fetch_orderbook_quote, calculate_edge, _events_to_markets, 
    _compute_market_implied_prob, _enrich_markets_missing_prices,
    _extract_top_orderbook_price
)
from market_discovery_internal.forecasting import (
    fetch_forecast, fetch_forecast_with_cache, prefetch_forecasts,
    forecast_still_valid, position_to_market
)
from market_discovery_internal.analysis import (
    build_weather_evidence, is_weather_evidence_valid, _sonnet_entry_analysis,
    _haiku_position_monitor, decide_entry_bucket, _maybe_apply_ai_decision,
    _record_ai_usage_cost
)
from market_discovery_internal.discovery import (
    fetch_markets, filter_opportunities
)
from market_discovery_internal.cycles import (
    run_discovery_cycle, run_paper_trading_cycle, parse_discovery_markets,
    enrich_discovery_markets, _ensure_take_profit_target, evaluate_hybrid_exit,
    _position_confidence_score, close_paper_position, build_paper_position,
    build_open_position_inventory, build_entry_candidates,
    append_opened_positions_from_candidates
)
from market_discovery_internal.cli import (
    parse_cli_mode_flags, run_main_discovery_mode, run_main_paper_report_mode,
    run_main_paper_loop_mode, run_main_paper_single_mode
)
from market_discovery_internal.diagnostics import (
    build_discovery_diagnostics, collect_discovery_evidence_and_ai,
    collect_discovery_bucket_counts, classify_discovery_rejection_reason,
    analyze_discovery_raw_market
)
from market_discovery_internal.output import (
    print_discovery_diagnostics, print_opportunities, print_summary,
    print_paper_cycle_summary, print_paper_state_report
)
from market_discovery_internal.reporting import (
    build_paper_state_report, build_cycle_journal_entry,
    build_rolling_acceptance_metrics, build_rolling_city_coverage_metrics,
    build_city_coverage_metrics, build_cycle_acceptance_metrics,
    parse_utc_datetime
)
from market_discovery_internal.state_persistence import (
    load_paper_state, save_paper_state
)

# ---------------------------------------------------------------------------
# Dependency Wiring (Glue Logic)
# ---------------------------------------------------------------------------

def wired_run_discovery_cycle(inspect=False, aggressive_scan=False):
    """Bridge function to inject all dependencies into the internal discovery loop."""
    return run_discovery_cycle(
        inspect=inspect,
        aggressive_scan=aggressive_scan,
        perf_counter_fn=time.perf_counter,
        elapsed_ms_fn=lambda started: (time.perf_counter() - started) * 1000,
        fetch_markets_fn=fetch_markets,
        parse_discovery_markets_fn=lambda raw: parse_discovery_markets(
            raw, parse_market_fn=parse_market
        ),
        enrich_discovery_markets_fn=lambda parsed: enrich_discovery_markets(
            parsed,
            perf_counter_fn=time.perf_counter,
            elapsed_ms_fn=lambda started: (time.perf_counter() - started) * 1000,
            prefetch_forecasts_fn=lambda **kwargs: prefetch_forecasts(fetch_forecast_fn=fetch_forecast, **kwargs),
            fetch_forecast_with_cache_fn=lambda **kwargs: fetch_forecast_with_cache(fetch_forecast_fn=fetch_forecast, **kwargs),
            build_weather_evidence_fn=build_weather_evidence,
            is_weather_evidence_valid_fn=is_weather_evidence_valid,
            calculate_edge_fn=calculate_edge,
            maybe_apply_ai_decision_fn=_maybe_apply_ai_decision,
            discovery_forecast_prefetch_min_keys=DISCOVERY_FORECAST_PREFETCH_MIN_KEYS,
            discovery_forecast_prefetch_max_workers=DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS,
        ),
        filter_opportunities_fn=lambda enriched: filter_opportunities(
            enriched,
            fetch_forecast_fn=fetch_forecast,
            max_yes_price=STRATEGY_MAX_YES_PRICE,
            min_model_prob=STRATEGY_MIN_MODEL_PROB,
            min_edge=STRATEGY_MIN_EDGE
        ),
        daily_resolve_only=DAILY_RESOLVE_ONLY,
        daily_min_hours_to_resolve=DAILY_MIN_HOURS_TO_RESOLVE
    )

def wired_run_paper_trading_cycle(force_aggressive_scan=False):
    """Bridge function to inject all dependencies into the internal paper trading loop."""
    return run_paper_trading_cycle(
        min_price=PAPER_ENTRY_MIN_PRICE,
        max_price=PAPER_ENTRY_MAX_PRICE,
        stake_usd=PAPER_STAKE_USD,
        state_path=PAPER_STATE_FILE,
        force_aggressive_scan=force_aggressive_scan,
        perf_counter_fn=time.perf_counter,
        now_utc_fn=lambda: datetime.now(timezone.utc),
        elapsed_ms_fn=lambda started: (time.perf_counter() - started) * 1000,
        load_paper_state_fn=load_paper_state,
        run_discovery_cycle_fn=wired_run_discovery_cycle,
        prefetch_forecasts_fn=lambda **kwargs: prefetch_forecasts(fetch_forecast_fn=fetch_forecast, **kwargs),
        ensure_take_profit_target_fn=_ensure_take_profit_target, 
        forecast_still_valid_fn=lambda **kwargs: forecast_still_valid(
            fetch_forecast_with_cache_fn=lambda **k: fetch_forecast_with_cache(fetch_forecast_fn=fetch_forecast, **k),
            build_weather_evidence_fn=build_weather_evidence,
            is_weather_evidence_valid_fn=is_weather_evidence_valid,
            position_to_market_fn=position_to_market,
            calculate_edge_fn=calculate_edge,
            **kwargs
        ),
        position_confidence_score_fn=_position_confidence_score, 
        update_paper_position_fn=evaluate_hybrid_exit, 
        close_paper_position_fn=close_paper_position,
        fetch_orderbook_quote_fn=fetch_orderbook_quote,
        build_open_position_inventory_fn=build_open_position_inventory,
        build_entry_candidates_fn=build_entry_candidates,
        append_opened_positions_from_candidates_fn=append_opened_positions_from_candidates,
        build_city_coverage_metrics_fn=build_city_coverage_metrics,
        build_cycle_acceptance_metrics_fn=build_cycle_acceptance_metrics,
        build_rolling_acceptance_metrics_fn=build_rolling_acceptance_metrics,
        build_rolling_city_coverage_metrics_fn=build_rolling_city_coverage_metrics,
        build_cycle_journal_entry_fn=build_cycle_journal_entry,
        save_paper_state_fn=save_paper_state,
        discovery_enable_auto_aggressive_scan=DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN,
        discovery_auto_aggressive_after_empty_cycles=DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES,
        paper_position_forecast_prefetch_min_keys=PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS,
        paper_position_forecast_prefetch_max_workers=PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS,
        paper_entry_min_price=PAPER_ENTRY_MIN_PRICE,
        paper_entry_max_price=PAPER_ENTRY_MAX_PRICE,
        paper_max_open_positions=PAPER_MAX_OPEN_POSITIONS,
        paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
        paper_journal_max_entries=PAPER_JOURNAL_MAX_ENTRIES,
        haiku_position_monitor_fn=_haiku_position_monitor,
        haiku_monitor_min_confidence=HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT,
        ws_watcher=_ws_watcher,
        last_ws_update_at=_last_ws_update_at,
        ws_stale_detection_minutes=WS_STALE_DETECTION_MINUTES,
    )

# ---------------------------------------------------------------------------
# Global Background Components (WS & Monitoring)
# ---------------------------------------------------------------------------

_price_update_queue = multiprocessing.Queue()
_ws_watcher = None
_ws_broadcaster = None
_last_ws_update_at = multiprocessing.Value('d', 0.0)
_state_lock = threading.Lock()

def _start_background_services():
    global _ws_watcher, _ws_broadcaster
    from market_discovery_internal.ws_price_watcher import PriceWatcher, make_ws_exit_callback
    from market_discovery_internal.ws_broadcaster import WsBroadcaster
    from market_discovery_internal.config import (
        WS_PRICE_WATCHER_URL, WS_WATCHDOG_TIMEOUT_SECONDS, 
        WS_BROADCAST_HOST, WS_BROADCAST_PORT, WS_PING_INTERVAL_SECONDS,
        WS_RECONNECT_DELAY_SECONDS, WS_PING_INTERVAL_SECONDS as WS_FEED_PING_INTERVAL
    )
    import threading

    # 1. Broadcaster (Web UI Relay)
    _ws_broadcaster = WsBroadcaster(
        host=WS_BROADCAST_HOST, 
        port=WS_BROADCAST_PORT,
        ping_interval_seconds=WS_PING_INTERVAL_SECONDS
    )
    _ws_broadcaster.start()

    # 2. Price Watcher (Multiprocessing Source)
    _ws_watcher = PriceWatcher(
        url=WS_PRICE_WATCHER_URL,
        update_queue=_price_update_queue,
        reconnect_delay=WS_RECONNECT_DELAY_SECONDS,
        ping_interval=WS_FEED_PING_INTERVAL,
        watchdog_timeout=WS_WATCHDOG_TIMEOUT_SECONDS
    )
    # [WIRING] Initial subscription sync
    try:
        from market_discovery_internal.state_persistence import load_paper_state
        from market_discovery_internal.config import PAPER_STATE_FILE
        state = load_paper_state(PAPER_STATE_FILE)
        token_ids = {p["token_id"] for p in state.get("positions", []) if p.get("status") == "open"}
        _ws_watcher.update_subscriptions(token_ids)
        print(f"[WS-WIRING] Initial sync: monitoring {len(token_ids)} active tokens")
    except Exception as exc:
        print(f"[WS-WIRING] Initial sync failed: {exc}")

    _ws_watcher.start()

    # 3. Queue Consumer (Main Process Sink)
    exit_callback = make_ws_exit_callback(
        state_path=PAPER_STATE_FILE,
        lock=_state_lock, # Use the shared state lock
        broadcaster=_ws_broadcaster
    )

    def _consumer_loop():
        global _last_ws_update_at
        while True:
            try:
                # Blocks until update arrives
                token_id, price = _price_update_queue.get()
                _last_ws_update_at.value = time.time()
                
                # Update UI
                if _ws_broadcaster:
                    _ws_broadcaster.broadcast_price(token_id, price)
                
                # Check Exits (Atomic)
                exit_callback(token_id, price)
            except Exception as exc:
                print(f"[WS-CONSUMER] Error: {exc}")

    t = threading.Thread(target=_consumer_loop, daemon=True, name="WsQueueConsumer")
    t.start()
    print("[WS-WIRING] Background services initialized (Isolated Mode)")

def _stop_background_services():
    if _ws_watcher: _ws_watcher.stop()
    if _ws_broadcaster: _ws_broadcaster.stop()

# ---------------------------------------------------------------------------
# Main Entry Point & Hardening
# ---------------------------------------------------------------------------

class PIDLock:
    def __init__(self, lockfile):
        self.lockfile = lockfile
        self.fd = None

    def __enter__(self):
        self.fd = open(self.lockfile, 'w')
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd.write(str(os.getpid()))
            self.fd.flush()
        except IOError:
            print(f"[FATAL] Another instance of THE BLUEPRINTS is already running (locked by {self.lockfile})")
            sys.exit(1)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()
            try:
                os.remove(self.lockfile)
            except OSError:
                pass

def main():
    # 0. Global Signal Handlers for Graceful Shutdown
    def handle_exit_signal(signum, frame):
        print(f"\n[SIGN] Received signal {signum}. Shutting down THE BLUEPRINTS...")
        _stop_background_services()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit_signal)
    signal.signal(signal.SIGTERM, handle_exit_signal)

    # 1. Process Protection (PID Lock)
    lock_path = "/opt/the_blueprints/the_blueprints.pid"
    with PIDLock(lock_path):
        _main_protected()

def _main_protected():
    flags = parse_cli_mode_flags(sys.argv)
    
    if flags["paper_report_mode"]:
        run_main_paper_report_mode(
            paper_report_json_mode=flags["paper_report_json_mode"],
            load_paper_state_fn=load_paper_state,
            print_paper_state_report_fn=print_paper_state_report,
            paper_state_file=PAPER_STATE_FILE
        )
        return

    if flags["paper_loop_mode"]:
        _start_background_services()
        try:
            # Enterprise Logic: Monitor WS health and adjust polling interval
            run_main_paper_loop_mode(
                flags["aggressive_mode"],
                PAPER_LOOP_INTERVAL_SECONDS,
                wired_run_paper_trading_cycle,
                print_paper_cycle_summary,
                time.sleep,
                continue_on_error=PAPER_LOOP_CONTINUE_ON_ERROR,
                error_backoff_seconds=PAPER_LOOP_ERROR_BACKOFF_SECONDS,
                max_error_backoff_seconds=PAPER_LOOP_MAX_ERROR_BACKOFF_SECONDS,
                ws_watcher=_ws_watcher,
                last_ws_update_at=_last_ws_update_at,
                ws_stale_detection_minutes=WS_STALE_DETECTION_MINUTES,
                safe_float_fn=_safe_float,
                paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
            )
        finally:
            _stop_background_services()
        return

    if flags["paper_mode"]:
        run_main_paper_single_mode(
            aggressive_mode=flags["aggressive_mode"],
            run_paper_trading_cycle_fn=wired_run_paper_trading_cycle,
            print_paper_cycle_summary_fn=print_paper_cycle_summary,
            safe_float_fn=_safe_float,
            paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
        )
        return

    # Default: Discovery Mode
    run_main_discovery_mode(
        inspect_mode=flags["inspect_mode"],
        aggressive_mode=flags["aggressive_mode"],
        diagnose_mode=flags["diagnose_mode"],
        run_discovery_cycle_fn=wired_run_discovery_cycle,
        print_discovery_diagnostics_fn=lambda discovery, aggressive_scan: print_discovery_diagnostics(
            build_discovery_diagnostics(
                discovery,
                collect_discovery_evidence_and_ai_fn=collect_discovery_evidence_and_ai,
                collect_discovery_bucket_counts_fn=collect_discovery_bucket_counts,
                analyze_discovery_raw_market_fn=lambda raw: analyze_discovery_raw_market(
                    raw,
                    market_search_text_fn=_market_search_text,
                    has_target_city_fn=_has_target_city,
                    threshold_re=THRESHOLD_RE,
                    weather_context_re=WEATHER_CONTEXT_RE,
                    direction_candidate_re=DIRECTION_CANDIDATE_RE
                ),
                classify_discovery_rejection_reason_fn=classify_discovery_rejection_reason,
                decide_entry_bucket_fn=decide_entry_bucket,
                paper_entry_min_price=PAPER_ENTRY_MIN_PRICE,
                paper_entry_max_price=PAPER_ENTRY_MAX_PRICE,
                daily_resolve_only=DAILY_RESOLVE_ONLY,
                daily_min_hours_to_resolve=DAILY_MIN_HOURS_TO_RESOLVE
            ),
            aggressive_scan=aggressive_scan
        ),
        print_opportunities_fn=print_opportunities,
        print_summary_fn=print_summary,
        target_cities_len=len(TARGET_CITIES)
    )

if __name__ == "__main__":
    main()
