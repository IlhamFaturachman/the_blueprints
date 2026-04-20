"""CLI helpers for market_discovery modes."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def parse_cli_mode_flags(argv):
    """Parse CLI mode flags used by main()."""
    argv = argv if isinstance(argv, list) else []
    return {
        "inspect_mode": "--inspect" in argv,
        "paper_mode": "--paper" in argv,
        "paper_loop_mode": "--paper-loop" in argv,
        "paper_report_mode": ("--paper-report" in argv) or ("--paper-report-json" in argv),
        "paper_report_json_mode": ("--paper-report-json" in argv)
        or (("--paper-report" in argv) and ("--json" in argv)),
        "diagnose_mode": "--diagnose" in argv,
        "aggressive_mode": ("--aggressive" in argv) or ("--aggresive" in argv),
    }


def run_main_paper_report_mode(
    paper_report_json_mode,
    load_paper_state_fn,
    print_paper_state_report_fn,
    paper_state_file,
):
    """Handle paper report mode in main()."""
    state = load_paper_state_fn(path=paper_state_file)
    output_format = "json" if paper_report_json_mode else "text"
    print_paper_state_report_fn(
        state=state,
        state_path=paper_state_file,
        recent_entries=5,
        output_format=output_format,
    )


def run_main_paper_loop_mode(
    aggressive_mode,
    paper_loop_interval_seconds,
    run_paper_trading_cycle_fn,
    print_paper_cycle_summary_fn,
    sleep_fn,
    continue_on_error=True,
    error_backoff_seconds=30,
    max_error_backoff_seconds=None,
    report_error_fn=None,
    ws_watcher=None,
    last_ws_update_at=None,
    ws_stale_detection_minutes=5,
    safe_float_fn=float,
    paper_min_city_diversity=1,
    kill_flag_check_fn=None,
    on_kill_fn=None,
):
    """Handle paper loop mode in main()."""
    # [MODUL U] Log Rotation Hardening
    try:
        from market_discovery_internal.log_rotator import run_rotator_thread
        run_rotator_thread("/opt/the_blueprints/logs/paper_loop.out")
    except Exception as e:
        logger.warning("[BOOT] Log rotator failed to start: %s", e)

    # [MODUL DB] Gudang Data Warmer Integration (runs in background daemon thread)
    try:
        import threading
        from market_discovery_internal.warmer import warmer
        warmer_thread = threading.Thread(target=warmer.start, name="GudangDataWarmer", daemon=True)
        warmer_thread.start()
        logger.info("[BOOT] Data Warmer started in background thread.")
    except Exception as e:
        logger.warning("[BOOT] Data Warmer failed to start: %s", e)

    # [MODUL U] IP Reputation Guard (Cool-off)
    logger.info("Starting paper loop every %ds. (Cool-off for 30s)...", paper_loop_interval_seconds)
    sleep_fn(30)

    consecutive_errors = 0
    backoff_base = max(1, int(error_backoff_seconds))
    max_backoff = max(
        backoff_base,
        int(
            max_error_backoff_seconds
            if max_error_backoff_seconds is not None
            else max(backoff_base, paper_loop_interval_seconds)
        ),
    )

    try:
        import time
        while True:
            # [MODUL J] Kill-Switch check — runs before every cycle
            if callable(kill_flag_check_fn) and kill_flag_check_fn():
                logger.info("[MODUL J] Kill flag detected. Shutting down bot...")
                if callable(on_kill_fn):
                    try:
                        on_kill_fn()
                    except Exception:
                        logger.warning("[MODUL J] on_kill callback failed", exc_info=True)
                return

            # Dynamic Fallback: Check if WebSocket is healthy
            current_interval = paper_loop_interval_seconds
            is_stale = False
            if last_ws_update_at is not None:
                since_last = time.time() - last_ws_update_at.value
                # If WS was active but hasn't updated in X minutes, it's stale
                if since_last > (ws_stale_detection_minutes * 60):
                    is_stale = True
                    # Fallback to balanced 120s polling
                    current_interval = min(current_interval, 120)

                    # [FIX] Hard-restart WS from loop level if stale >2x threshold
                    if since_last > (ws_stale_detection_minutes * 60 * 2) and ws_watcher:
                        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                        logger.warning("[%s] WS stale >%dm. Hard-restarting WS process...", timestamp, ws_stale_detection_minutes*2)
                        try:
                            ws_watcher.stop()
                            time.sleep(2)
                            ws_watcher.start()
                            last_ws_update_at.value = time.time()
                            logger.info("[%s] WS process restarted successfully.", timestamp)
                        except Exception as _e:
                            logger.error("[%s] WS restart failed: %s", timestamp, _e)
            
            use_aggressive = aggressive_mode or is_stale
            if is_stale:
                timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                logger.warning("[%s] WS Stale (%ds). Using Hybrid Fallback (%ds).", timestamp, int(since_last), current_interval)

            try:
                cycle = run_paper_trading_cycle_fn(force_aggressive_scan=use_aggressive)
                print_paper_cycle_summary_fn(cycle, safe_float_fn, paper_min_city_diversity)
                consecutive_errors = 0
                sleep_fn(current_interval)
            except Exception as error:
                if not continue_on_error:
                    raise

                consecutive_errors += 1
                retry_in_seconds = min(
                    max_backoff,
                    backoff_base * (2 ** min(consecutive_errors - 1, 6)),
                )
                timestamp_utc = datetime.now(timezone.utc).isoformat()
                logger.error(
                    "[%s] Paper cycle failed (%d consecutive): %s",
                    timestamp_utc, consecutive_errors, error
                )
                logger.info("Retrying in %ds...", retry_in_seconds)

                if callable(report_error_fn):
                    try:
                        report_error_fn(
                            error=error,
                            consecutive_errors=consecutive_errors,
                            retry_in_seconds=retry_in_seconds,
                        )
                    except Exception:
                        logger.warning("[ERROR-REPORT] report_error_fn callback failed", exc_info=True)

                sleep_fn(retry_in_seconds)
    except KeyboardInterrupt:
        logger.info("Paper loop stopped.")


def run_main_paper_single_mode(
    aggressive_mode,
    run_paper_trading_cycle_fn,
    print_paper_cycle_summary_fn,
    safe_float_fn=float,
    paper_min_city_diversity=1
):
    """Handle one-shot paper mode in main()."""
    cycle = run_paper_trading_cycle_fn(force_aggressive_scan=aggressive_mode)
    print_paper_cycle_summary_fn(cycle, safe_float_fn, paper_min_city_diversity)


def run_main_discovery_mode(
    inspect_mode,
    aggressive_mode,
    diagnose_mode,
    run_discovery_cycle_fn,
    print_discovery_diagnostics_fn,
    print_opportunities_fn,
    print_summary_fn,
    target_cities_len,
):
    """Handle discovery/default mode in main()."""
    discovery = run_discovery_cycle_fn(inspect=inspect_mode, aggressive_scan=aggressive_mode)

    if diagnose_mode:
        print_discovery_diagnostics_fn(discovery, aggressive_scan=aggressive_mode)

    print_opportunities_fn(discovery["opportunities"])
    print_summary_fn(
        total_cities=target_cities_len,
        failed_cities=discovery["failed_cities"],
        total_markets=len(discovery["markets_raw"]),
        parsed_markets=len(discovery["parsed"]),
        skipped_markets=discovery["skipped_markets"],
        exact_skipped=discovery["exact_skipped"],
        opportunities_count=len(discovery["opportunities"]),
    )
