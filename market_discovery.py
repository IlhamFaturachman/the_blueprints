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

# ---------------------------------------------------------------------------
# EARLY PID LOCK — acquired BEFORE slow module imports to close the startup
# race window. Two instances starting within seconds of each other both rush
# through ~7s of imports; without this, both reach the late PIDLock at nearly
# the same time and may both succeed.  This early grab happens in <1ms.
# The late PIDLock class still exists for clean __exit__ / file cleanup.
# ---------------------------------------------------------------------------
_EARLY_PID_LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "the_blueprints.pid")
_early_pid_fd = None

if __name__ == "__main__":
    # Open WITHOUT truncating (O_RDWR | O_CREAT). Truncation happens AFTER
    # the flock is acquired, so the ExecStartPre script can never read an
    # empty PID file and mistake a live instance for a stale one.
    _early_pid_raw = os.open(
        _EARLY_PID_LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o644
    )
    _early_pid_fd = os.fdopen(_early_pid_raw, 'r+')
    try:
        fcntl.flock(_early_pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # We own the lock — now safe to truncate and stamp our PID.
        _early_pid_fd.seek(0)
        _early_pid_fd.truncate()
        _early_pid_fd.write(str(os.getpid()))
        _early_pid_fd.flush()
    except IOError:
        print(f"[FATAL] Another instance of THE BLUEPRINTS is already running (locked by {_EARLY_PID_LOCK_PATH})")
        _early_pid_fd.close()
        sys.exit(0)  # Duplicate — not a crash, don't trigger systemd restart loop

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
    build_weather_evidence, is_weather_evidence_valid, _haiku_entry_analysis,
    _haiku_position_monitor, decide_entry_bucket, _maybe_apply_ai_decision,
    _record_ai_usage_cost, resolve_station_with_ai
)
from market_discovery_internal.discovery import (
    fetch_markets, filter_opportunities
)
from market_discovery_internal.cycles import (
    run_discovery_cycle, run_paper_trading_cycle, parse_discovery_markets,
    enrich_discovery_markets, _ensure_take_profit_target, evaluate_hybrid_exit,
    update_paper_position, _position_confidence_score, close_paper_position,
    build_paper_position, build_open_position_inventory, build_entry_candidates,
    append_opened_positions_from_candidates, compute_auto_tuner_adjustments,
    build_profit_attribution_report,
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
            fetch_forecast_with_cache_fn=lambda city, date, cache, **kwargs: fetch_forecast_with_cache(city, date, cache, fetch_forecast_fn=fetch_forecast, **kwargs),
            build_weather_evidence_fn=build_weather_evidence,
            is_weather_evidence_valid_fn=is_weather_evidence_valid,
            calculate_edge_fn=calculate_edge,
            maybe_apply_ai_decision_fn=_maybe_apply_ai_decision,
            resolve_station_with_ai_fn=resolve_station_with_ai,
            discovery_forecast_prefetch_min_keys=DISCOVERY_FORECAST_PREFETCH_MIN_KEYS,
            discovery_forecast_prefetch_max_workers=DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS,
        ),
        filter_opportunities_fn=lambda enriched: sorted(
            [m for m in enriched
             # [PACK B] Use per-market regime gates if available, else fall back to global config
             if float(m.get("yes_price", 1.0)) <= float((m.get("regime_gates") or {}).get("max_price", STRATEGY_MAX_YES_PRICE))
             and float(m.get("model_prob", 0.0)) >= float((m.get("regime_gates") or {}).get("min_prob", STRATEGY_MIN_MODEL_PROB))
             and float(m.get("edge", -1.0)) >= float((m.get("regime_gates") or {}).get("min_edge", STRATEGY_MIN_EDGE))],
            key=lambda x: x.get("edge", 0), reverse=True
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
            fetch_forecast_with_cache_fn=lambda city, date, cache, **k: fetch_forecast_with_cache(city, date, cache, fetch_forecast_fn=fetch_forecast, **k),
            build_weather_evidence_fn=build_weather_evidence,
            is_weather_evidence_valid_fn=is_weather_evidence_valid,
            position_to_market_fn=position_to_market,
            calculate_edge_fn=calculate_edge,
            **kwargs
        ),
        position_confidence_score_fn=_position_confidence_score, 
        update_paper_position_fn=update_paper_position,
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
_last_ws_update_at = multiprocessing.Value('d', time.time())
_state_lock = threading.Lock()

_command_server = None

def _start_background_services():
    global _ws_watcher, _ws_broadcaster, _command_server
    from market_discovery_internal.ws_price_watcher import PriceWatcher, make_ws_exit_callback
    from market_discovery_internal.ws_broadcaster import WsBroadcaster
    from market_discovery_internal.command_server import start_command_server
    _command_server = start_command_server()
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
        import json
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
    if _ws_watcher:
        _ws_watcher.stop()
        if hasattr(_ws_watcher, '_process') and _ws_watcher._process:
            _ws_watcher._process.join(timeout=5)
            if _ws_watcher._process.is_alive():
                _ws_watcher._process.terminate()
    if _ws_broadcaster:
        _ws_broadcaster.stop()
    if _command_server:
        try:
            _command_server.shutdown()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Main Entry Point & Hardening
# ---------------------------------------------------------------------------

class PIDLock:
    def __init__(self, lockfile):
        self.lockfile = lockfile
        self.fd = None

    def __enter__(self):
        # If early lock was already acquired (same process), reuse _early_pid_fd.
        # Opening with 'w' would truncate the file and break the lock on Linux.
        global _early_pid_fd
        if _early_pid_fd is not None:
            # Already locked by this process via early lock — just return.
            self.fd = _early_pid_fd
            return self
        # Fallback path (e.g. running without __main__ or in tests).
        self.fd = open(self.lockfile, 'w')
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd.write(str(os.getpid()))
            self.fd.flush()
        except IOError:
            print(f"[FATAL] Another instance of THE BLUEPRINTS is already running (locked by {self.lockfile})")
            sys.exit(0)  # Duplicate — not a crash, don't trigger systemd restart loop
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _early_pid_fd
        if self.fd and self.fd is not _early_pid_fd:
            # Normal path: release and clean up the late-acquired fd.
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()
            try:
                os.remove(self.lockfile)
            except OSError:
                pass
        elif _early_pid_fd is not None:
            # Early lock path: release the early lock and clean up.
            fcntl.flock(_early_pid_fd, fcntl.LOCK_UN)
            _early_pid_fd.close()
            _early_pid_fd = None
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
            from market_discovery_internal.command_server import check_kill_flag
            from market_discovery_internal.utils import send_telegram_alert

            def _on_kill():
                send_telegram_alert(
                    "🛑 <b>[MODUL J] Emergency Kill-Switch Activated</b>\n"
                    "Bot dihentikan via Dashboard. Semua posisi terbuka tetap di state.json."
                )

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
                kill_flag_check_fn=check_kill_flag,
                on_kill_fn=_on_kill,
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
