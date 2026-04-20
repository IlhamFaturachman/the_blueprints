import logging
import time
import os
import threading
from datetime import datetime, timezone
from market_discovery_internal.utils import _safe_float, _safe_div, _clamp, send_telegram_alert, load_telegram_template
from market_discovery_internal.config import (
    HYBRID_TAKE_PROFIT_MIN_PRICE, HYBRID_TAKE_PROFIT_MULTIPLIER,
    HYBRID_STOP_LOSS_MULTIPLIER, HYBRID_LATE_WINDOW_HOURS,
    HYBRID_MIN_CONFIDENCE_TO_HOLD, HYBRID_CONFIDENCE_EDGE_SCALE,
    PAPER_STAKE_USD, PAPER_BASE_WALLET, PAPER_MAX_OPEN_PER_CITY,
    MARKET_MAX_SPREAD_GATE, MIN_STAKE_THRESHOLD, PAPER_STAKE_INCLUSIVE,
    WHIPLASH_COOLDOWN_HOURS, STRATEGY_MIN_STAKE_USD, STRATEGY_MIN_SHARES
)
from market_discovery_internal.parsing import _normalize_city_key
from market_discovery_internal.analysis import decide_entry_bucket
from market_discovery_internal.database_manager import db
from market_discovery_internal.reporting import parse_utc_datetime

logger = logging.getLogger(__name__)

# [MODUL L] Heartbeat State
_HEARTBEAT_SENT = False
_heartbeat_lock = threading.Lock()


def parse_discovery_markets(
    markets_raw,
    *,
    parse_market_fn,
    max_spread=None,
    min_volume_24hr=None,
):
    """Parse raw markets and collect skip metrics used by discovery diagnostics.

    Exact-bracket markets are now included (previously V1-skipped).
    Liquidity gate: markets with spread > max_spread or volume < min_volume_24hr
    are skipped to avoid illiquid exact-bracket positions.
    """
    from market_discovery_internal.config import MARKET_MAX_SPREAD_GATE, MARKET_MIN_VOLUME_24HR
    _max_spread = max_spread if max_spread is not None else MARKET_MAX_SPREAD_GATE
    _min_vol = min_volume_24hr if min_volume_24hr is not None else MARKET_MIN_VOLUME_24HR

    parsed = []
    skipped_markets = 0
    exact_skipped = 0  # kept for diagnostics compat — now counts liquidity-gated exact markets
    daily_skip_reasons = {
        "daily_date_mismatch": 0,
        "daily_min_hours_not_met": 0,
        "too_close_to_resolve": 0,
    }

    for raw in markets_raw:
        parsed_result = parse_market_fn(raw, return_skip_reason=True)
        if isinstance(parsed_result, tuple) and len(parsed_result) == 2:
            parsed_market, skip_reason = parsed_result
        else:
            parsed_market = parsed_result
            skip_reason = None

        if not parsed_market:
            skipped_markets += 1
            if skip_reason in daily_skip_reasons:
                daily_skip_reasons[skip_reason] += 1
            continue

        # Liquidity gate for exact-bracket markets: need tight spread + real volume
        if parsed_market["direction"] == "exact":
            spread = parsed_market.get("gamma_spread")
            vol24 = parsed_market.get("volume_24hr", 0.0)
            accepting = parsed_market.get("gamma_accepting_orders", True)
            best_ask = parsed_market.get("best_ask")

            if not accepting:
                exact_skipped += 1
                continue
            if best_ask is None or best_ask <= 0:
                exact_skipped += 1
                continue
            if spread is None or float(spread) > _max_spread:
                exact_skipped += 1
                continue
            if float(vol24) < _min_vol:
                exact_skipped += 1
                continue

        parsed.append(parsed_market)

    return parsed, skipped_markets, exact_skipped, daily_skip_reasons


def enrich_discovery_markets(
    parsed,
    *,
    perf_counter_fn,
    elapsed_ms_fn,
    prefetch_forecasts_fn,
    fetch_forecast_with_cache_fn,
    build_weather_evidence_fn,
    is_weather_evidence_valid_fn,
    calculate_edge_fn,
    maybe_apply_ai_decision_fn,
    resolve_station_with_ai_fn,
    discovery_forecast_prefetch_min_keys,
    discovery_forecast_prefetch_max_workers,
):
    """Enrich parsed markets with forecast/edge and keep forecast cache diagnostics."""
    failed_cities = []
    failed_city_keys = set()
    enriched = []
    forecast_cache = {}
    forecast_cache_stats = {"hits": 0, "misses": 0}

    # [MODUL A] Precision Station Resolution (Pre-Enrichment)
    total_parsed = len(parsed)
    for i, market in enumerate(parsed, 1):
        # Progress log for station resolution if many markets
        if total_parsed > 50 and i % 50 == 0:
            print(f"[MODUL A] Resolving stations: {i}/{total_parsed} markets processed...", flush=True)

        # If ICAO is already explicit via regex, we skip AI to save cost.
        if not market.get("icao_explicit"):
            ai_icao = resolve_station_with_ai_fn(market.get("city"), market.get("description"))
            if ai_icao:
                market["icao_code"] = ai_icao
                market["icao_explicit"] = True # Mark as explicit so we don't re-run AI

    print(f"[MODUL K] Prefetching forecasts for {len(parsed)} unique slots...", flush=True)
    prefetch_started = perf_counter_fn()
    forecast_prefetch = prefetch_forecasts_fn(
        cache_keys=[(market.get("city"), market.get("date"), market.get("icao_code")) for market in parsed],
        cache=forecast_cache,
        min_keys=discovery_forecast_prefetch_min_keys,
        max_workers=discovery_forecast_prefetch_max_workers,
    )
    forecast_prefetch_ms = elapsed_ms_fn(prefetch_started)

    enrich_started = perf_counter_fn()
    for i, market in enumerate(parsed, 1):
        # Progress log for enrichment
        if total_parsed > 20 and i % 25 == 0:
             print(f"[MODUL C] Enrichment progress: {i}/{total_parsed} markets...", flush=True)

        # [MODUL L] Rate Limit Shield: Dampen burst requests to provider
        if i % 5 == 0:
            time.sleep(0.5)

        city = market["city"]
        date = market["date"]
        forecast_temp = fetch_forecast_with_cache_fn(
            city,
            date,
            forecast_cache,
            stats=forecast_cache_stats,
            icao_override=market.get("icao_code"),
        )
        evidence = build_weather_evidence_fn(city, date, forecast_temp)
        evidence_valid = is_weather_evidence_valid_fn(evidence)

        if forecast_temp is None:
            if city not in failed_city_keys:
                failed_city_keys.add(city)
                failed_cities.append(city)
            continue

        edge_result = calculate_edge_fn(market, forecast_temp)
        if edge_result:
            # Merge market metadata into edge result so downstream filters/entry have full context
            enriched_item = {**market, **edge_result}
            enriched_item["weather_evidence"] = evidence
            enriched_item["weather_evidence_valid"] = evidence_valid
            enriched_item["weather_evidence_quality_score"] = evidence.get("quality_score")
            enriched_item["weather_evidence_age_hours"] = evidence.get("age_hours")
            # [PACK B] Compute market regime and attach gates
            try:
                from market_discovery_internal.pricing import compute_regime_score
                r_class, r_score, r_gates = compute_regime_score(enriched_item, evidence)
                enriched_item["regime_class"] = r_class
                enriched_item["regime_score"] = r_score
                enriched_item["regime_gates"] = r_gates
            except Exception:
                enriched_item["regime_class"] = "neutral"
                enriched_item["regime_score"] = 0.5
                enriched_item["regime_gates"] = {"min_prob": 0.72, "min_edge": 0.22, "max_price": 0.60}
            enriched.append(maybe_apply_ai_decision_fn(enriched_item))
    enrich_ms = elapsed_ms_fn(enrich_started)

    return {
        "enriched": enriched,
        "failed_cities": failed_cities,
        "forecast_cache": forecast_cache,
        "forecast_cache_stats": forecast_cache_stats,
        "forecast_prefetch": forecast_prefetch,
        "forecast_prefetch_ms": forecast_prefetch_ms,
        "enrich_ms": enrich_ms,
    }


def run_discovery_cycle(
    inspect=False,
    aggressive_scan=False,
    *,
    perf_counter_fn,
    elapsed_ms_fn,
    fetch_markets_fn,
    parse_discovery_markets_fn,
    enrich_discovery_markets_fn,
    filter_opportunities_fn,
    daily_resolve_only,
    daily_min_hours_to_resolve,
):
    """Run one discovery cycle and return structured results."""
    cycle_started = perf_counter_fn()
    print(f"--- [FETCH] Starting market scan (Aggressive: {aggressive_scan}) ---", flush=True)

    fetch_started = perf_counter_fn()
    markets_raw = fetch_markets_fn(inspect=inspect, aggressive_scan=aggressive_scan)
    print(f"--- [FETCH] Found {len(markets_raw)} markets. Parsing...", flush=True)
    fetch_ms = elapsed_ms_fn(fetch_started)

    parse_started = perf_counter_fn()
    parsed, skipped_markets, exact_skipped, daily_skip_reasons = parse_discovery_markets_fn(markets_raw)
    parse_ms = elapsed_ms_fn(parse_started)

    enrich_result = enrich_discovery_markets_fn(parsed)
    enriched = enrich_result["enriched"]
    failed_cities = enrich_result["failed_cities"]
    forecast_cache = enrich_result["forecast_cache"]
    forecast_cache_stats = enrich_result["forecast_cache_stats"]
    forecast_prefetch = enrich_result["forecast_prefetch"]
    forecast_prefetch_ms = enrich_result["forecast_prefetch_ms"]
    enrich_ms = enrich_result["enrich_ms"]

    filter_started = perf_counter_fn()
    opportunities = filter_opportunities_fn(enriched)
    filter_ms = elapsed_ms_fn(filter_started)

    performance = {
        "total_ms": elapsed_ms_fn(cycle_started),
        "fetch_markets_ms": fetch_ms,
        "parse_ms": parse_ms,
        "forecast_prefetch_ms": forecast_prefetch_ms,
        "enrich_ms": enrich_ms,
        "filter_ms": filter_ms,
        "forecast_cache": {
            "size": len(forecast_cache),
            "hits": int(forecast_cache_stats.get("hits", 0)),
            "misses": int(forecast_cache_stats.get("misses", 0)),
        },
        "forecast_prefetch": {
            "eligible": int(forecast_prefetch.get("eligible", 0)),
            "attempted": int(forecast_prefetch.get("attempted", 0)),
            "successful": int(forecast_prefetch.get("successful", 0)),
            "failed": int(forecast_prefetch.get("failed", 0)),
            "workers": int(forecast_prefetch.get("workers", 0)),
            "skipped": bool(forecast_prefetch.get("skipped", True)),
        },
    }

    return {
        "markets_raw": markets_raw,
        "parsed": parsed,
        "enriched": enriched,
        "opportunities": opportunities,
        "failed_cities": failed_cities,
        "skipped_markets": skipped_markets,
        "exact_skipped": exact_skipped,
        "daily_skip_reasons": daily_skip_reasons,
        "daily_resolve_only": daily_resolve_only,
        "daily_min_hours_to_resolve": daily_min_hours_to_resolve,
        "aggressive_scan": aggressive_scan,
        "performance": performance,
    }


def run_paper_trading_cycle(
    min_price,
    max_price,
    stake_usd,
    state_path,
    force_aggressive_scan,
    *,
    perf_counter_fn,
    now_utc_fn,
    elapsed_ms_fn,
    load_paper_state_fn,
    run_discovery_cycle_fn,
    prefetch_forecasts_fn,
    ensure_take_profit_target_fn,
    forecast_still_valid_fn,
    position_confidence_score_fn,
    update_paper_position_fn,
    close_paper_position_fn,
    fetch_orderbook_quote_fn,
    build_open_position_inventory_fn,
    build_entry_candidates_fn,
    append_opened_positions_from_candidates_fn,
    build_city_coverage_metrics_fn,
    build_cycle_acceptance_metrics_fn,
    build_rolling_acceptance_metrics_fn,
    build_rolling_city_coverage_metrics_fn,
    build_cycle_journal_entry_fn,
    save_paper_state_fn,
    discovery_enable_auto_aggressive_scan,
    discovery_auto_aggressive_after_empty_cycles,
    paper_position_forecast_prefetch_min_keys,
    paper_position_forecast_prefetch_max_workers,
    paper_entry_min_price,
    paper_entry_max_price,
    paper_max_open_positions,
    paper_min_city_diversity,
    paper_journal_max_entries,
    haiku_position_monitor_fn=None,
    haiku_monitor_min_confidence=0.75,
    allow_new_entries=True,
    entry_gate_reason="active",
    ws_watcher=None,
    last_ws_update_at=None,
    ws_stale_detection_minutes=15,
    on_position_opened_fn=None,
):
    """Run one paper-trading cycle: discover, manage exits, open new positions."""
    global _HEARTBEAT_SENT
    cycle_started = perf_counter_fn()  # Top-level timer for total cycle duration
    state_load_started = perf_counter_fn()
    now_dt = now_utc_fn()
    state = load_paper_state_fn(path=state_path)
    state_load_ms = elapsed_ms_fn(state_load_started)

    state_meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    
    # [MODUL L] Process Heartbeat
    print(f"--- CYCLE START: {now_dt.strftime('%H:%M:%S')} (Aggressive: {force_aggressive_scan}) ---", flush=True)
    
    with _heartbeat_lock:
        if not _HEARTBEAT_SENT:
            send_telegram_alert(
                "🚀 <b>Bot Master Active: Resumed</b>\n"
                f"Siklus pertama dimulai pada {now_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC.\n"
                "Mode: Paper Trading (Zero-Barrier Configuration)"
            )
            _HEARTBEAT_SENT = True
    
    # [MODUL L] Process Watchdog Pulse
    db.update_heartbeat("main_loop")

    # [MODUL L] Memory Watchdog — alert if RSS > 850 MB (one-shot per cooldown period)
    try:
        import resource
        _rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On Linux, ru_maxrss is in KB; on macOS, it's in bytes
        import sys as _sys
        _rss_mb = _rss_kb / 1024.0 if _sys.platform != "darwin" else _rss_kb / (1024.0 * 1024.0)
        _MEM_ALERT_THRESHOLD_MB = 850
        _MEM_ALERT_COOLDOWN_CYCLES = 12  # ~1h at 5-min cycles
        _mem_alert_count = state_meta.get("_mem_alert_count", 0)
        if _rss_mb > _MEM_ALERT_THRESHOLD_MB:
            if _mem_alert_count == 0:
                send_telegram_alert(
                    f"⚠️ <b>MEMORY WATCHDOG: HIGH RAM</b> ⚠️\n\n"
                    f"RSS Memory: <b>{_rss_mb:.0f} MB</b> (threshold {_MEM_ALERT_THRESHOLD_MB} MB)\n"
                    f"Bot masih berjalan, tetapi performa mungkin terdampak.\n"
                    f"Pertimbangkan restart jika terus meningkat."
                )
            state_meta["_mem_alert_count"] = (_mem_alert_count + 1) % _MEM_ALERT_COOLDOWN_CYCLES
        else:
            state_meta["_mem_alert_count"] = 0
    except Exception:
        pass
    
    # [MODUL L] Monitor Warmer Health
    warmer_heartbeat_raw = db.get_heartbeat("warmer")
    if warmer_heartbeat_raw:
        try:
            from market_discovery_internal.reporting import parse_utc_datetime
            whb = parse_utc_datetime(warmer_heartbeat_raw)
            if whb:
                stale_hours = (datetime.now(timezone.utc) - whb).total_seconds() / 3600.0
                if stale_hours > 6.0: # 3x the normal 2-hour interval
                    if not state_meta.get("warmer_silent_fail_alert_sent"):
                        send_telegram_alert(
                            "⚠️ <b>WATCHDOG: WARMER STAGNANT</b> ⚠️\n\n"
                            f"Layanan Gudang Data Warmer tidak berdetak selama <b>{stale_hours:.1f} jam</b>.\n"
                            "Penyebaran data forecast mungkin tertunda. Harap cek proses background."
                        )
                        state_meta["warmer_silent_fail_alert_sent"] = True
                else:
                    state_meta["warmer_silent_fail_alert_sent"] = False
        except Exception as e:
            logger.error(f"[WATCHDOG] Error checking warmer heartbeat: {e}")

    # [DAILY RESET] Check BEFORE updating last_cycle_at so we compare previous vs now
    _prev_cycle_at_raw = state_meta.get("last_cycle_at")
    if _prev_cycle_at_raw:
        from market_discovery_internal.reporting import parse_utc_datetime
        _prev_cycle_dt = parse_utc_datetime(_prev_cycle_at_raw)
        if _prev_cycle_dt and _prev_cycle_dt.date() < now_dt.date():
            # New day — reset daily session baseline to current actual wallet
            _daily_sess = state_meta.get("daily_session") or {}
            if isinstance(_daily_sess, dict):
                _current_total = float(state_meta.get("current_wallet", PAPER_BASE_WALLET))
                _daily_sess["baseline_wallet"] = _current_total
                state_meta["daily_session"] = _daily_sess
                logger.info(f"[DAILY-RESET] New day detected. CB baseline reset to ${_current_total:.2f}")
            state_meta["circuit_breaker_alert_sent"] = False
    elif not state_meta.get("daily_session", {}).get("baseline_wallet"):
        # Fresh start — initialize daily_session baseline from current_wallet
        _current_total = float(state_meta.get("current_wallet", PAPER_BASE_WALLET))
        state_meta["daily_session"] = {"baseline_wallet": _current_total}
        logger.info(f"[DAILY-RESET] Fresh session initialized. CB baseline = ${_current_total:.2f}")

    # [MODUL L] Initial UI Touch: Tell the Dashboard we are starting a cycle
    state_meta["last_cycle_at"] = now_dt.isoformat()
    state["updated_at"] = now_dt.isoformat()
    state["meta"] = state_meta
    save_paper_state_fn(state, path=state_path)

    empty_temperature_cycles = int(state_meta.get("empty_temperature_cycles", 0) or 0)

    # [PACK E] Auto-Tuner: compute per-city adjustments from trade history
    try:
        auto_tuner_state = compute_auto_tuner_adjustments()
        blacklisted_cities = {c for c, v in auto_tuner_state.items() if v.get("blacklisted")}
        if blacklisted_cities:
            logger.warning(f"[AUTO-TUNER] Blacklisted cities this cycle: {blacklisted_cities}")
        state_meta["auto_tuner"] = {
            "computed_at": now_dt.isoformat(),
            "adjustments": auto_tuner_state,
            "blacklisted": list(blacklisted_cities),
        }
    except Exception as _e:
        auto_tuner_state = {}
        blacklisted_cities = set()
        logger.warning(f"[AUTO-TUNER] Failed: {_e}")

    # [PACK F] Weekly profit attribution report (once per 7 days)
    try:
        last_report_at_str = state_meta.get("last_attribution_report_at", "")
        _do_report = False  # default: never report on fresh start
        if not last_report_at_str:
            # First run — record timestamp but don't send yet (wait 7 days)
            state_meta["last_attribution_report_at"] = now_dt.isoformat()
        else:
            from market_discovery_internal.reporting import parse_utc_datetime
            _last_r = parse_utc_datetime(last_report_at_str)
            if _last_r and (now_dt - _last_r).total_seconds() >= 7 * 86400:
                _do_report = True
        if _do_report:
            _report = build_profit_attribution_report()
            if _report and _report.get("trades_analyzed", 0) > 0:
                _leakers = _report.get("top_leakers_city", [])[:3]
                _earners = _report.get("top_earners_city", [])[:3]
                _reasons = _report.get("by_exit_reason", [])
                _leak_text = "\n".join(
                    f"  • {r['key']}: ${r['total_pnl']:+.2f} ({r['win_rate']*100:.0f}% WR, {r['count']} trades)"
                    for r in _leakers
                ) or "  (nok data)"
                _earn_text = "\n".join(
                    f"  • {r['key']}: ${r['total_pnl']:+.2f} ({r['win_rate']*100:.0f}% WR, {r['count']} trades)"
                    for r in _earners
                ) or "  (no data)"
                _reason_text = "\n".join(
                    f"  • {r['key']}: ${r['total_pnl']:+.2f} ({r['count']} trades)"
                    for r in _reasons
                ) or "  (no data)"
                send_telegram_alert(
                    f"📊 <b>WEEKLY PROFIT ATTRIBUTION REPORT</b> 📊\n\n"
                    f"📉 <b>Top Leakers (City):</b>\n{_leak_text}\n\n"
                    f"📈 <b>Top Earners (City):</b>\n{_earn_text}\n\n"
                    f"🏁 <b>Exit Reasons:</b>\n{_reason_text}\n\n"
                    f"<i>{_report['trades_analyzed']} trades analyzed</i>"
                )
                state_meta["last_attribution_report_at"] = now_dt.isoformat()
                state_meta["last_attribution_report"] = _report
    except Exception as _e:
        logger.warning(f"[PACK-F] Attribution report failed: {_e}")

    auto_aggressive = (
        discovery_enable_auto_aggressive_scan
        and empty_temperature_cycles >= max(0, discovery_auto_aggressive_after_empty_cycles)
    )
    use_aggressive_scan = force_aggressive_scan or auto_aggressive

    discovery_started = perf_counter_fn()
    
    # --- HYBRID GUARD (FAILOVER) ---
    ws_stale = False
    if last_ws_update_at is not None:
        elapsed_since_ws = (time.time() - last_ws_update_at.value) / 60.0
        if elapsed_since_ws > ws_stale_detection_minutes:
            ws_stale = True
            logger.warning("[GUARD] WebSocket STALE (%.1fm). Switching to Aggressive Scan.", elapsed_since_ws)

            # [FIX] Hard-restart WS process if stale for >2x detection window.
            # This recovers from zombie subprocess or broken pipe scenarios.
            if elapsed_since_ws > (ws_stale_detection_minutes * 2) and ws_watcher:
                logger.warning("[GUARD] WS stale for %.1fm (>2x threshold). HARD RESTARTING WS process.", elapsed_since_ws)
                try:
                    ws_watcher.stop()
                    time.sleep(2)
                    ws_watcher.start()
                    # Reset timestamp so next cycle doesn't immediately re-trigger
                    last_ws_update_at.value = time.time()
                    logger.info("[GUARD] WS process hard-restarted successfully.")
                except Exception as _ws_restart_err:
                    logger.error("[GUARD] WS hard-restart failed: %s", _ws_restart_err)

    # --- SUPERVISOR LOGIC ---
    if ws_watcher and hasattr(ws_watcher, "_process") and ws_watcher._process:
        if not ws_watcher._process.is_alive():
            logger.warning("[SUPERVISOR] PriceWatcher process DIED. Restarting...")
            ws_watcher.start()
            # [FIX] Reset timestamp after restart to prevent false stale detection
            if last_ws_update_at is not None:
                last_ws_update_at.value = time.time()

    # Sync subscriptions to MPC process
    if ws_watcher and hasattr(ws_watcher, "update_subscriptions"):
        open_ids = {str(p["token_id"]) for p in state.get("positions", []) if p.get("status") == "open"}
        ws_watcher.update_subscriptions(open_ids)

    # Use aggressive scan if WS is stale or force_aggressive_scan is True
    use_aggressive_scan = force_aggressive_scan or auto_aggressive or ws_stale
    
    discovery = run_discovery_cycle_fn(inspect=False, aggressive_scan=use_aggressive_scan)
    discovery_ms = elapsed_ms_fn(discovery_started)

    quote_cache = {}

    def _get_orderbook_quote(token_id):
        token = str(token_id or "").strip()
        if not token:
            return None
        if token in quote_cache:
            return quote_cache[token]

        quote = fetch_orderbook_quote_fn(token) if callable(fetch_orderbook_quote_fn) else None
        quote_cache[token] = quote if isinstance(quote, dict) else None
        return quote_cache[token]

    market_by_token = {market["token_id"]: market for market in discovery["parsed"] if market.get("token_id")}
    next_open_positions = []
    next_history = list(state.get("history", []))
    closed_this_cycle = []
    position_forecast_cache = {}
    position_forecast_cache_stats = {"hits": 0, "misses": 0}

    position_prefetch_started = perf_counter_fn()
    position_forecast_prefetch = prefetch_forecasts_fn(
        cache_keys=[
            (position.get("city"), position.get("date"), position.get("icao_code"))
            for position in state.get("positions", [])
            if position.get("status") == "open"
        ],
        cache=position_forecast_cache,
        min_keys=paper_position_forecast_prefetch_min_keys,
        max_workers=paper_position_forecast_prefetch_max_workers,
    )
    position_forecast_prefetch_ms = elapsed_ms_fn(position_prefetch_started)

    position_management_started = perf_counter_fn()
    haiku_monitor_calls = 0
    haiku_monitor_forced_exits = 0
    for position in state.get("positions", []):
        position = ensure_take_profit_target_fn(position)

        if position.get("status") != "open":
            next_history.append(position)
            continue

        token_id = position.get("token_id")
        live_market = market_by_token.get(token_id)

        if not live_market:
            next_open_positions.append(position)
            continue

        quote = _get_orderbook_quote(token_id)
        raw_bid = (quote or {}).get("best_bid")
        raw_ask = (quote or {}).get("best_ask")
        if raw_bid is None:
            raw_bid = live_market.get("best_bid")
        if raw_ask is None:
            raw_ask = live_market.get("best_ask")

        # Position evaluation uses best_bid (what you could actually sell for)
        # because the CLOB best_ask can contain rogue/complement orders
        # (e.g. best_ask=$0.99 for a 6-cent token, causing mid=(0.01+0.99)/2=0.50).
        # Only use mid-price when ask is reasonably close to entry price.
        try:
            bid_val = float(raw_bid) if raw_bid is not None else 0.0
            ask_val = float(raw_ask) if raw_ask is not None else 0.0
        except (TypeError, ValueError):
            bid_val = 0.0
            ask_val = 0.0

        entry_price = float(position.get("entry_price", 0))

        # Reject completely empty orderbooks that only have extreme fallback limits
        # (bid=0.01, ask=0.99) which cause false 1-cent stop losses.
        if bid_val <= 0.01 and ask_val >= 0.98:
            bid_val = 0.0
            ask_val = 0.0
            
        # Ignore 1-cent bids as active market evaluation criteria if entry point was high.
        # A 1-cent bid is a market maker placeholder, not a real sellable price.
        if bid_val <= 0.01 and entry_price > 0.05:
            bid_val = 0.0

        # [LIVE-LIKE] Exit evaluation always uses bid (what you'd receive selling on CLOB).
        # Mid-price was optimistic and not representative of real execution price.
        # WS exit path already uses bid — align main cycle to match.
        ask_sane_limit = max(entry_price * 3.0, 0.60)  # ask above 3x entry or 60c is rogue

        if bid_val > 0:
            current_yes_price = bid_val
        elif ask_val > 0 and ask_val <= ask_sane_limit:
            # No bid available — fall back to sane ask as reference only
            current_yes_price = ask_val
        else:
            last_price = position.get("last_price")
            if last_price is not None:
                try:
                    current_yes_price = float(last_price)
                except (TypeError, ValueError):
                    next_open_positions.append(position)
                    continue
            else:
                next_open_positions.append(position)
                continue

        if current_yes_price <= 0:
            next_open_positions.append(position)
            continue

        hours_until_resolve = live_market.get("hours_until_resolve")

        monitor_result = None
        if callable(haiku_position_monitor_fn):
            try:
                _cache_key = (position.get("city"), position.get("date"), position.get("icao_code"))
                current_forecast_temp = position_forecast_cache.get(_cache_key)
                monitor_result = haiku_position_monitor_fn(
                    position,
                    current_yes_price=current_yes_price,
                    hours_until_resolve=hours_until_resolve,
                    current_forecast_temp_c=current_forecast_temp,
                )
            except TypeError:
                # Backward-compat: support monitor callbacks that accept only position.
                monitor_result = haiku_position_monitor_fn(position)
            except Exception:
                monitor_result = None
            finally:
                haiku_monitor_calls += 1

        if isinstance(monitor_result, dict):
            monitor_action = str(monitor_result.get("action") or "hold").strip().lower()
            try:
                monitor_confidence = float(monitor_result.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                monitor_confidence = 0.0

            if monitor_action == "close" and monitor_confidence >= float(haiku_monitor_min_confidence):
                forced_closed = close_paper_position_fn(
                    position=position,
                    exit_price=current_yes_price,
                    reason="haiku_monitor_exit",
                    now_utc=now_dt,
                )
                forced_closed["haiku_monitor_confidence"] = round(monitor_confidence, 4)
                forced_closed["haiku_monitor_reasoning"] = str(monitor_result.get("reasoning") or "")
                closed_this_cycle.append(forced_closed)
                next_history.append(forced_closed)
                state_meta["cash"] = round(float(state_meta.get("cash", 0.0)) + float(forced_closed.get("net_exit_value", forced_closed.get("exit_value", 0.0))), 4)
                haiku_monitor_forced_exits += 1
                continue

        # [MODUL G] Exit Sniper (Aggressive sweeps) — checked BEFORE forecast call to avoid wasted API calls
        if current_yes_price >= 0.90:
            forced_closed = close_paper_position_fn(
                position=position,
                exit_price=current_yes_price,
                reason="sniper_take_profit",
                now_utc=now_dt,
            )
            closed_this_cycle.append(forced_closed)
            next_history.append(forced_closed)
            state_meta["cash"] = round(float(state_meta.get("cash", 0.0)) + float(forced_closed.get("net_exit_value", forced_closed.get("exit_value", 0.0))), 4)
            continue

        forecast_valid = forecast_still_valid_fn(
            position,
            current_yes_price,
            hours_until_resolve,
            forecast_cache=position_forecast_cache,
            forecast_cache_stats=position_forecast_cache_stats,
        )

        if not forecast_valid:
            forced_closed = close_paper_position_fn(
                position=position,
                exit_price=current_yes_price,
                reason="sniper_stop_loss_thesis_broken",
                now_utc=now_dt,
            )
            closed_this_cycle.append(forced_closed)
            next_history.append(forced_closed)
            state_meta["cash"] = round(float(state_meta.get("cash", 0.0)) + float(forced_closed.get("net_exit_value", forced_closed.get("exit_value", 0.0))), 4)
            continue

        confidence_score = position_confidence_score_fn(position, current_yes_price, forecast_valid)

        updated_position, decision = update_paper_position_fn(
            position=position,
            current_yes_price=current_yes_price,
            forecast_still_valid=forecast_valid,
            hours_until_resolve=hours_until_resolve,
            now_utc=now_dt,
            confidence_score=confidence_score,
        )

        if decision["action"] == "hold_to_resolve" and hours_until_resolve is not None and hours_until_resolve <= 0:
            settle_price = 1.0 if forecast_valid else 0.0
            updated_position = close_paper_position_fn(
                position=updated_position,
                exit_price=settle_price,
                reason="resolved_after_hold",
                now_utc=now_dt,
            )

        if updated_position.get("status") == "closed":
            closed_this_cycle.append(updated_position)
            next_history.append(updated_position)
            # [ACCOUNTING] Credit cash with net exit proceeds (after fees) when position closes in main cycle
            net_exit_val = float(updated_position.get("net_exit_value", updated_position.get("exit_value", 0.0)))
            state_meta["cash"] = round(float(state_meta.get("cash", 0.0)) + net_exit_val, 4)

            # [MODUL N] Telegram Notification for Closure using Template
            pnl = float(updated_position.get("realized_pnl_usd", 0.0))
            roi = float(updated_position.get("realized_roi_pct", 0.0))
            _cash_display = float(state_meta.get("cash", 0.0))
            _exit_slug = updated_position.get("market_slug", "")
            _exit_url = f"https://polymarket.com/event/{_exit_slug}" if _exit_slug else "https://polymarket.com"
            _exit_fee = float(updated_position.get("exit_fee_usd", 0.0))
            _net_exit = float(updated_position.get("net_exit_value", float(updated_position.get("exit_value", 0.0))))
            _entry_price = float(updated_position.get("entry_price", 0.0))
            _exit_price = float(updated_position.get("exit_price", 0.0))
            _exit_value = float(updated_position.get("exit_value", 0.0))
            _entry_fee = float(updated_position.get("entry_fee_usd", 0.0))
            _opened_at = updated_position.get("opened_at")
            _closed_at = updated_position.get("closed_at")
            try:
                from market_discovery_internal.reporting import parse_utc_datetime as _pudt
                _dur_s = int((datetime.now(timezone.utc) - _pudt(_opened_at)).total_seconds()) if _opened_at else 0
                _dur_h, _dur_m = _dur_s // 3600, (_dur_s % 3600) // 60
                _duration_str = f"{_dur_h}h {_dur_m}m" if _dur_h else f"{_dur_m}m"
            except Exception:
                _duration_str = "N/A"
            _tpl_name = "exit_profit" if pnl > 0 else "exit_loss"
            msg = load_telegram_template(
                category="execution",
                type_name=_tpl_name,
                market_name=updated_position.get('market_question', 'Unknown'),
                city=updated_position.get('city', 'N/A').upper(),
                date=updated_position.get('date', ''),
                entry_price=f"{_entry_price:.4f}",
                exit_price=f"{_exit_price:.4f}",
                exit_value=f"{_exit_value:.4f}",
                exit_fee=f"{_exit_fee:.4f}",
                net_exit=f"{_net_exit:.4f}",
                pnl_usd=f"{pnl:+.4f}",
                roi=f"{roi:+.2f}",
                duration=_duration_str,
                close_reason=(updated_position.get('close_reason') or 'unknown').replace('_', ' ').upper(),
                cash=f"{_cash_display:.4f}",
                url=_exit_url,
            )
            send_telegram_alert(msg)
        else:
            next_open_positions.append(updated_position)
    position_management_ms = elapsed_ms_fn(position_management_started)

    min_bound = paper_entry_min_price if min_price is None else float(min_price)
    max_bound = paper_entry_max_price if max_price is None else float(max_price)

    open_token_ids, open_city_counts = build_open_position_inventory_fn(next_open_positions)

    available_slots = max(paper_max_open_positions - len(open_token_ids), 0)

    entry_selection_started = perf_counter_fn()
    
    # [FIX] Whiplash Shield: Build a blacklist of recently force-closed positions to avoid re-entry whiplash.
    recent_exit_blacklist = set()
    _now_ts_whiplash = now_dt.timestamp()
    
    # Combined pool: memory history + database history
    try:
        db_history = db.get_recent_trade_history(limit=50)
    except Exception:
        db_history = []
        
    combined_history = list(next_history) + list(db_history)
    
    for h_pos in combined_history:
        try:
            closed_at_str = h_pos.get("closed_at")
            if not closed_at_str: continue
            closed_dt = parse_utc_datetime(closed_at_str)
            if not closed_dt: continue
            # [FIX] Ensure closed_dt is timezone-aware (assume UTC if naive)
            if closed_dt.tzinfo is None:
                closed_dt = closed_dt.replace(tzinfo=timezone.utc)
            
            age_hours = (_now_ts_whiplash - closed_dt.timestamp()) / 3600.0
            if age_hours <= WHIPLASH_COOLDOWN_HOURS:
                reason = str(h_pos.get("reason", "") or h_pos.get("close_reason", "")).lower()
                # Blacklist if closed by AI monitor or Stop Loss (to prevent immediate whiplash)
                if any(r in reason for r in ["haiku_monitor_exit", "stop_loss", "broken_thesis"]):
                    token_id = str(h_pos.get("token_id"))
                    recent_exit_blacklist.add(token_id)
        except Exception:
            continue
    if recent_exit_blacklist:
        logger.warning(f"[WHIPLASH-SHIELD] Active blacklist for {len(recent_exit_blacklist)} tokens: {recent_exit_blacklist}")

    (
        entry_candidates,
        bucket_counts,
        opportunity_city_keys,
        candidate_city_keys,
    ) = build_entry_candidates_fn(
        opportunities=discovery["opportunities"],
        open_token_ids=open_token_ids,
        open_city_counts=open_city_counts,
        min_bound=min_bound,
        max_bound=max_bound,
        recent_exit_blacklist=recent_exit_blacklist,
    )

    effective_allow_new_entries = bool(allow_new_entries)
    effective_entry_gate_reason = str(entry_gate_reason or "active")
    daily_session = state_meta.get("daily_session") if isinstance(state_meta.get("daily_session"), dict) else {}
    
    # Calculate current capital: Use state meta, then daily session, then config default
    state_base_wallet = float(state_meta.get("base_wallet", float(daily_session.get("baseline_wallet", PAPER_BASE_WALLET) or PAPER_BASE_WALLET)))
    realized_after_position_management = sum(
        float(position.get("realized_pnl_usd", 0.0) or 0.0)
        for position in next_history
    )
    wallet_after_position_management = round(state_base_wallet + realized_after_position_management, 4)

    if effective_allow_new_entries and daily_session:
        target_wallet = float(daily_session.get("target_wallet", 0.0) or 0.0)
        if target_wallet > 0 and wallet_after_position_management >= target_wallet:
            effective_allow_new_entries = False
            effective_entry_gate_reason = "daily_target_reached"

    # [MODUL L] Circuit Breaker — trigger on daily drawdown >= 15% of baseline
    _daily_baseline = float(daily_session.get("baseline_wallet", state_base_wallet) or state_base_wallet)
    _daily_loss = _daily_baseline - wallet_after_position_management
    
    # [FIX] Transition to 15% dynamic drawdown shield as requested by user
    _drawdown_limit = round(_daily_baseline * 0.15, 2)
    _drawdown_limit = max(1.50, _drawdown_limit) # Floor at 1.50 for safety on small tiers

    # [FIX] Reset circuit breaker alert flag if it's a new day
    last_cycle_str = str(state_meta.get("last_cycle_at", ""))
    try:
        if last_cycle_str:
            last_date = datetime.fromisoformat(last_cycle_str).date()
            if last_date < now_dt.date():
                state_meta.pop("circuit_breaker_alert_sent", None)
    except Exception:
        pass

    if effective_allow_new_entries and _daily_loss >= _drawdown_limit:
        effective_allow_new_entries = False
        effective_entry_gate_reason = "circuit_breaker_tripped"
        if not state_meta.get("circuit_breaker_alert_sent"):
            msg = load_telegram_template(
                category="risk",
                type_name="circuit_breaker",
                reason_text="Max Daily Drawdown (15%) Reached",
                current_loss=f"{_daily_loss:.2f}",
                limit_amount=f"{_drawdown_limit:.2f}",
                baseline=f"{_daily_baseline:.2f}",
                cash=f"{wallet_after_position_management:.2f}"
            )
            send_telegram_alert(msg)
            state_meta["circuit_breaker_alert_sent"] = True

    # [MODUL D] Compounding & Slot Expansion (Updated Tiers)
    current_tier_max_slots = paper_max_open_positions
    current_stake_usd = float(stake_usd)
    new_tier = 1
    
    if wallet_after_position_management >= 20.0:
        # TIER 2 ($20-$100): Stake 15% but capped by total exposure safety
        new_tier = 2
        current_tier_max_slots = 15
        current_stake_usd = round(wallet_after_position_management * 0.15, 2)
    elif wallet_after_position_management >= 5.0:
        # TIER 1 ($5-$20): Stake $1-$2, Slots 5-8
        new_tier = 1
        current_tier_max_slots = 8 if wallet_after_position_management >= 12.0 else 5
        current_stake_usd = 2.0 if wallet_after_position_management >= 12.0 else 1.0

    # [SAFE LEVERAGE CAP] Ensure Total Exposure <= Available Cash
    # Use actual cash balance. Formula: Total Equity - Current Deployment Cost.
    _open_exposure = sum(float(p.get("cost_basis", 0.0) or 0.0) for p in next_open_positions)
    available_cash = round(wallet_after_position_management - _open_exposure, 4)

    from market_discovery_internal.config import POLYMARKET_TAKER_FEE_RATE
    _MAX_EFFECTIVE_OVERHEAD = 1.5 * (1.0 + POLYMARKET_TAKER_FEE_RATE)  # ~1.575
    
    max_total_exposure = max(0.0, available_cash)
    if max_total_exposure <= 0.0:
        # No cash left — block all new entries this cycle
        effective_allow_new_entries = False
        effective_entry_gate_reason = "insufficient_cash"
        logger.warning("[ACCOUNTING] Available cash $%.4f <= 0, blocking new entries.", available_cash)
    elif (current_stake_usd * _MAX_EFFECTIVE_OVERHEAD * current_tier_max_slots) > max_total_exposure:
        current_stake_usd = round(max_total_exposure / (current_tier_max_slots * _MAX_EFFECTIVE_OVERHEAD), 2)

    # [FIX] Block entries if the calculated stake falls below the hard floor
    if effective_allow_new_entries and current_stake_usd < MIN_STAKE_THRESHOLD:
        effective_allow_new_entries = False
        effective_entry_gate_reason = "insufficient_cash"
        logger.warning("[ACCOUNTING] Calculated stake $%.2f < floor $%.2f, blocking new entries.", current_stake_usd, MIN_STAKE_THRESHOLD)

    # Notify on Tier Change
    prev_tier = state_meta.get("current_tier", 1)
    if new_tier != prev_tier:
        msg = load_telegram_template(
            category="system",
            type_name="tier_update",
            new_tier=new_tier,
            wallet_balance=wallet_after_position_management,
            current_stake_usd=current_stake_usd,
            max_slots=current_tier_max_slots
        )
        send_telegram_alert(msg)
    state_meta["current_tier"] = new_tier

    available_slots = max(current_tier_max_slots - len(open_token_ids), 0)

    if effective_allow_new_entries:
        # [PACK E] Filter blacklisted cities from candidates before entry
        _filtered_candidates = entry_candidates
        if blacklisted_cities:
            _filtered_candidates = [
                c for c in entry_candidates
                if str(c.get("opportunity", {}).get("city", "")).lower() not in blacklisted_cities
            ]
            _n_filtered = len(entry_candidates) - len(_filtered_candidates)
            if _n_filtered:
                logger.warning(f"[AUTO-TUNER] Filtered {_n_filtered} candidates from blacklisted cities.")

        opened_this_cycle, opened_city_keys, _ = append_opened_positions_from_candidates_fn(
            entry_candidates=_filtered_candidates,
            next_open_positions=next_open_positions,
            open_token_ids=open_token_ids,
            open_city_counts=open_city_counts,
            available_slots=available_slots,
            stake_usd=current_stake_usd,
            min_bound=min_bound,
            max_bound=max_bound,
            fetch_orderbook_quote_fn=_get_orderbook_quote,
            is_paper_trading=True,
            available_cash=available_cash,
            on_position_opened_fn=on_position_opened_fn,
        )
        # [ACCOUNTING] Debit cash for each newly opened position
        for pos in opened_this_cycle:
            state_meta["cash"] = round(float(state_meta.get("cash", wallet_after_position_management)) - float(pos.get("cost_basis", 0.0)), 4)

        # [MODUL N] Telegram Notification for Entries
        for pos in opened_this_cycle:
            # [MODUL K] Immediate Persistence: Save to DB before alerting
            try:
                db.add_position(pos)
                logger.info(f"[PERSISTENCE] Successfully saved new entry for {pos.get('city')} to SQLite.")
            except Exception as e:
                logger.error(f"[PERSISTENCE] FAILED to save new entry for {pos.get('city')}: {e}")

            # Brainstorming: Add Link, Reason, Forecast Context, and Resolution Time
            slug = pos.get('market_slug')
            link = f"https://polymarket.com/event/{slug}" if slug else "N/A"
            reason = pos.get('entry_bucket_reason', 'Criteria met')
            forecast = pos.get('forecast_temp_c', 'N/A')
            
            # Calculate hours until resolve for better context
            end_dt = parse_utc_datetime(pos.get('end_date'))
            
            # Handle potentially negative or None resolution time gracefully in alerts
            if end_dt:
                diff_sec = (end_dt - now_dt).total_seconds()
                hours_left = round(diff_sec / 3600, 1)
                hours_label = f"{hours_left}h lagi" if hours_left > 0 else "⚡ Resolving NOW"
            else:
                hours_label = "N/A"
            
            # Confidence Meter Visual
            prob = float(pos.get('entry_model_prob', 0))
            meter = "🟢" * int(prob * 5) + "⚪" * (5 - int(prob * 5))

            # Natural language reason similar to Web UI
            prob_pct = f"{prob*100:.1f}%"
            side = pos.get('direction').upper()
            target_desc = f"<b>{side} {pos.get('threshold')}{pos.get('unit')}</b>"
            city_upper = pos.get('city', 'N/A').upper()
            
            sensor_label = f" ({pos.get('icao_code')})" if pos.get('icao_code') else ""
            _entry_prob = float(pos.get('entry_model_prob', 0))
            _entry_edge = float(pos.get('entry_edge', 0))
            _entry_meter = "🟢" * int(_entry_prob * 5) + "⚪" * (5 - int(_entry_prob * 5))
            _entry_fee_amt = float(pos.get('entry_fee_usd', 0.0))
            _entry_shares_cost = float(pos.get('shares_cost', pos.get('cost_basis', 0)))
            msg = load_telegram_template(
                category="execution",
                type_name="entry",
                market_name=pos.get('market_question', 'N/A'),
                city=pos.get('city', 'N/A').upper(),
                date=pos.get('date', ''),
                target_desc=target_desc,
                outcome=f"{pos.get('direction', 'YES').upper()} (Target {pos.get('threshold')}{pos.get('unit')})",
                prob=f"{_entry_prob*100:.1f}",
                edge=f"{_entry_edge*100:.1f}",
                meter=_entry_meter,
                strategy=pos.get('entry_bucket', 'SWING').replace('_candidate', '').upper(),
                forecast=f"{pos.get('forecast_temp_c', 'N/A')}",
                hours_left=hours_label,
                price=f"{pos.get('entry_price', 0):.4f}",
                stop_loss=f"{pos.get('stop_loss_price', 0):.4f}",
                tp=f"{pos.get('target_price', 0):.4f}",
                quantity=f"{pos.get('quantity', 0):.2f}",
                stake=f"{_entry_shares_cost:.4f}",
                entry_fee=f"{_entry_fee_amt:.4f}",
                cash=f"{float(state_meta.get('cash', wallet_after_position_management)):.4f}",
                url=link
            )
            send_telegram_alert(msg)
    else:
        opened_this_cycle = []
        opened_city_keys = set()
    entry_selection_ms = elapsed_ms_fn(entry_selection_started)

    city_coverage_metrics = build_city_coverage_metrics_fn(
        opportunity_city_keys=opportunity_city_keys,
        candidate_city_keys=candidate_city_keys,
        opened_city_keys=opened_city_keys,
        min_city_target=paper_min_city_diversity,
    )

    if discovery["parsed"]:
        empty_temperature_cycles = 0
    else:
        empty_temperature_cycles += 1

    metrics_persist_started = perf_counter_fn()

    cycle_metrics = build_cycle_acceptance_metrics_fn(
        discovery=discovery,
        bucket_counts=bucket_counts,
        opened_positions=opened_this_cycle,
        closed_positions=closed_this_cycle,
    )

    rolling_metrics = build_rolling_acceptance_metrics_fn(
        previous=state_meta.get("acceptance_metrics_rolling"),
        cycle_metrics=cycle_metrics,
        bucket_counts=bucket_counts,
    )

    rolling_city_coverage_metrics = build_rolling_city_coverage_metrics_fn(
        previous=state_meta.get("city_coverage_rolling"),
        city_coverage=city_coverage_metrics,
    )

    performance = {
        "total_ms": elapsed_ms_fn(cycle_started),
        "state_load_ms": state_load_ms,
        "discovery_ms": discovery_ms,
        "position_prefetch_ms": position_forecast_prefetch_ms,
        "position_management_ms": position_management_ms,
        "entry_selection_ms": entry_selection_ms,
        "metrics_persist_ms": 0.0,
        "position_forecast_cache": {
            "size": len(position_forecast_cache),
            "hits": int(position_forecast_cache_stats.get("hits", 0)),
            "misses": int(position_forecast_cache_stats.get("misses", 0)),
        },
        "position_forecast_prefetch": {
            "eligible": int(position_forecast_prefetch.get("eligible", 0)),
            "attempted": int(position_forecast_prefetch.get("attempted", 0)),
            "successful": int(position_forecast_prefetch.get("successful", 0)),
            "failed": int(position_forecast_prefetch.get("failed", 0)),
            "workers": int(position_forecast_prefetch.get("workers", 0)),
            "skipped": bool(position_forecast_prefetch.get("skipped", True)),
        },
        "haiku_monitor": {
            "calls": int(haiku_monitor_calls),
            "forced_exits": int(haiku_monitor_forced_exits),
            "min_confidence": float(haiku_monitor_min_confidence),
        },
    }

    cycle_payload = {
        "opened": opened_this_cycle,
        "closed": closed_this_cycle,
        "open_positions": next_open_positions,
        "state_path": state_path,
        "discovery": discovery,
        "min_bound": min_bound,
        "max_bound": max_bound,
        "bucket_counts": bucket_counts,
        "city_coverage_metrics": city_coverage_metrics,
        "used_aggressive_scan": use_aggressive_scan,
        "entry_gate_open": bool(effective_allow_new_entries),
        "entry_gate_reason": str(effective_entry_gate_reason),
        "empty_temperature_cycles": empty_temperature_cycles,
        "performance": performance,
    }

    cycle_entry = build_cycle_journal_entry_fn(
        now_utc=now_dt,
        cycle_metrics=cycle_metrics,
        bucket_counts=bucket_counts,
        cycle=cycle_payload,
        city_coverage_metrics=city_coverage_metrics,
        performance_metrics=performance,
        meta=state_meta,
    )

    max_entries = max(1, int(paper_journal_max_entries))
    previous_journal = state.get("cycle_journal") if isinstance(state.get("cycle_journal"), list) else []
    next_cycle_journal = (previous_journal + [cycle_entry])[-max_entries:]

    next_meta = {
        **state_meta,
        "empty_temperature_cycles": empty_temperature_cycles,
        "last_cycle_at": now_dt.isoformat(),
        "last_cycle_metrics": cycle_metrics,
        "last_entry_gate_open": bool(effective_allow_new_entries),
        "last_entry_gate_reason": str(effective_entry_gate_reason),
        "acceptance_metrics_rolling": rolling_metrics,
        "city_coverage_rolling": rolling_city_coverage_metrics,
        "last_cycle_performance": performance,
        "current_wallet": float(wallet_after_position_management),
    }

    # [FIX] Cap history to last 500 entries to prevent unbounded memory growth
    _MAX_HISTORY_ENTRIES = 500
    if len(next_history) > _MAX_HISTORY_ENTRIES:
        next_history = next_history[-_MAX_HISTORY_ENTRIES:]

    next_state = {
        "positions": next_open_positions,
        "history": next_history,
        "cycle_journal": next_cycle_journal,
        "updated_at": now_dt.isoformat(),
        "meta": next_meta,
    }
    save_paper_state_fn(next_state, path=state_path)

    performance["metrics_persist_ms"] = elapsed_ms_fn(metrics_persist_started)
    performance["total_ms"] = elapsed_ms_fn(cycle_started)

    return {
        **cycle_payload,
        "acceptance_metrics": cycle_metrics,
        "rolling_acceptance_metrics": rolling_metrics,
        "rolling_city_coverage_metrics": rolling_city_coverage_metrics,
        "journal_entry": cycle_entry,
    }


# ---------------------------------------------------------------------------
# Trading Logic & Position Management (Restored from Backup)
# ---------------------------------------------------------------------------

def _compute_take_profit_price(entry_price):
    """Compute take-profit target price for +100% policy (2x entry by default)."""
    entry = _safe_float(entry_price, 0.0)
    if entry <= 0:
        return HYBRID_TAKE_PROFIT_MIN_PRICE
    return round(min(entry * HYBRID_TAKE_PROFIT_MULTIPLIER, 1.0), 4)


def _ensure_take_profit_target(position):
    """Normalize legacy positions so all open positions follow +100% TP policy if swing."""
    normalized = {**position}
    entry_price = _safe_float(normalized.get("entry_price"), 0.0)
    
    # Preserve or initialize strategy
    if "target_strategy" not in normalized:
        # Default legacy to swing
        normalized["target_strategy"] = "swing"
        
    if entry_price <= 0:
        return normalized

    target_price = _compute_take_profit_price(entry_price)
    normalized["target_price"] = target_price
    normalized["target_price_low"] = target_price
    normalized["target_price_high"] = target_price
    normalized["target_policy"] = "take_profit_100pct"
    return normalized


def build_paper_position(opportunity, stake_usd=PAPER_STAKE_USD):
    """Create a paper position from an opportunity candidate."""
    from market_discovery_internal.pricing import (
        calculate_depth_adjusted_stake,
        calculate_taker_fee,
    )
    from market_discovery_internal.config import POLYMARKET_TAKER_FEE_RATE

    token_id = opportunity.get("token_id")
    # Liquidity depth check moved to append_opened_positions_from_candidates
    entry_price = _safe_float(opportunity.get("entry_price"), _safe_float(opportunity.get("yes_price"), 0.0))
    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")

    # [PAPER REALISM] Implement 1% Slippage Buffer
    # For paper trading, we simulate paying slightly more than best_ask (slippage).
    effective_price = round(entry_price * 1.01, 4)

    # [LIVE-READY] Quantity Calculation with Integers and Floors
    # Rule: Must be at least STRATEGY_MIN_SHARES and at least STRATEGY_MIN_STAKE_USD in cost.
    # Total = Q * P * (1 + fee_overhead). Q = Total / (P * (1+overhead))
    _fee_mult = 1.0 + POLYMARKET_TAKER_FEE_RATE * (1.0 - effective_price)
    
    # Initial target quantity from stake or $1.00 floor
    target_usd = max(float(stake_usd), STRATEGY_MIN_STAKE_USD)
    _raw_qty = target_usd / (effective_price * _fee_mult)
    
    # Enforce integer rounding (ceil) and share floor
    import math
    quantity = float(max(STRATEGY_MIN_SHARES, math.ceil(_raw_qty)))

    shares_cost = round(quantity * effective_price, 4)
    entry_fee_usd = calculate_taker_fee(quantity, effective_price, POLYMARKET_TAKER_FEE_RATE)
    cost_basis = round(shares_cost + entry_fee_usd, 4)
    
    # [LIVE-READY] Final Floor Check: If cost_basis < $1.00 (e.g. very cheap shares), 
    # we must buy more shares to reach $1.00.
    if cost_basis < STRATEGY_MIN_STAKE_USD:
        # Calculate how many more shares needed to hit $1.00 cost
        _needed_qty = math.ceil(STRATEGY_MIN_STAKE_USD / (effective_price * _fee_mult))
        quantity = float(max(quantity, _needed_qty))
        shares_cost = round(quantity * effective_price, 4)
        entry_fee_usd = calculate_taker_fee(quantity, effective_price, POLYMARKET_TAKER_FEE_RATE)
        cost_basis = round(shares_cost + entry_fee_usd, 4)
    
    # [HONEST ACCOUNTING] Cost integrity floor
    # We no longer "shave" the cost_basis to fit the stake_usd (previous cause of PnL hallucination).
    # If the cost_basis exceeds the target stake due to share floors (5 shares), 
    # we accept the higher cost here; the calling function handles total budget limits.

    target_price = _compute_take_profit_price(effective_price)
    entry_yes_reference = _safe_float(opportunity.get("yes_price"), effective_price)
    entry_price_source = str(opportunity.get("entry_price_source") or "yes_price")
    entry_quote_best_bid = _safe_float(opportunity.get("entry_quote_best_bid"), 0.0)
    entry_quote_best_ask = _safe_float(opportunity.get("entry_quote_best_ask"), 0.0)

    position = {
        "status": "open",
        "city": opportunity["city"],
        "token_id": opportunity["token_id"],
        "market_question": opportunity.get("market_question", ""),
        "direction": opportunity["direction"],
        "threshold": opportunity["threshold"],
        "unit": opportunity["unit"],
        "date": opportunity["date"],
        "end_date": opportunity.get("end_date"),
        "market_slug": opportunity.get("market_slug", ""),
        "entry_price": effective_price,
        "entry_price_source": entry_price_source,
        "entry_yes_reference": entry_yes_reference,
        "quantity": quantity,
        "shares_cost": shares_cost,
        "entry_fee_usd": entry_fee_usd,
        "cost_basis": cost_basis,
        "target_price": target_price,
        "target_price_low": target_price,
        "target_price_high": target_price,
        "target_policy": "take_profit_100pct",
        "target_strategy": opportunity.get("strategy", "swing"),
        "stop_loss_price": round(effective_price * HYBRID_STOP_LOSS_MULTIPLIER, 4),
        "entry_model_prob": opportunity.get("model_prob"),
        "entry_edge": opportunity.get("edge"),
        "forecast_temp_c": opportunity.get("forecast_temp_c"),
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }

    if entry_quote_best_bid > 0:
        position["entry_quote_best_bid"] = round(entry_quote_best_bid, 4)
        position["last_price"] = round(entry_quote_best_bid, 4)
    if entry_quote_best_ask > 0:
        position["entry_quote_best_ask"] = round(entry_quote_best_ask, 4)

    return position


def _position_confidence_score(position, current_yes_price, forecast_still_valid):
    """Estimate confidence (0-1) that holding remains favorable."""
    if not forecast_still_valid:
        return 0.0

    base_prob = position.get("entry_model_prob")
    if base_prob is None:
        base_prob = 1.0
    base_prob = max(0.0, min(float(base_prob), 1.0))

    current_price = max(0.0, min(float(current_yes_price), 1.0))
    edge_now = max(base_prob - current_price, 0.0)
    edge_scale = HYBRID_CONFIDENCE_EDGE_SCALE if HYBRID_CONFIDENCE_EDGE_SCALE > 0 else 1.0
    edge_component = min(edge_now / edge_scale, 1.0)
    price_component = 1.0 - min(abs(current_price - 0.50) / 0.50, 1.0)

    score = (0.70 * base_prob) + (0.20 * edge_component) + (0.10 * price_component)
    return round(max(0.0, min(score, 1.0)), 4)


def _hours_until_resolve_from_end_date(end_date, now_utc=None):
    """Compute hours until resolve from ISO datetime string."""
    if not end_date:
        return None

    try:
        end_dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
    except ValueError:
        return None

    now_dt = now_utc or datetime.now(timezone.utc)
    return (end_dt - now_dt).total_seconds() / 3600


def evaluate_hybrid_exit(
    position,
    current_yes_price,
    forecast_still_valid,
    hours_until_resolve=None,
    now_utc=None,
    confidence_score=None,
):
    """
    [PACK D] Advanced hybrid exit strategy:
     1) Take-profit at +100% from entry (2x by default).
     2) Stop-loss when current price <= configured stop-loss price.
     3) [PACK D] Partial TP protection: at 1.5x entry, move stop to break-even.
     4) [PACK D] Trailing stop: if price retraces 15%+ from peak after 20% profit.
     5) [PACK D] Thesis decay exit: if H<2 and confidence < 0.45, exit.
     6) If within late window (H-2 by default):
        - forecast valid + confidence >= min threshold -> hold to resolve
        - otherwise -> sell
     7) Otherwise hold and wait.
    """
    price = float(current_yes_price)
    target_price = float(position.get("target_price", _compute_take_profit_price(position.get("entry_price"))))
    stop_loss_price = float(position.get("stop_loss_price", 0.0))
    entry_price = float(position.get("entry_price", price))

    if confidence_score is None:
        confidence_score = 1.0 if forecast_still_valid else 0.0
    confidence_score = max(0.0, min(float(confidence_score), 1.0))
    strategy = position.get("target_strategy", "swing")

    # [PACK D] Track peak price for trailing stop
    peak_price = float(position.get("peak_price") or price)
    updated_peak = max(peak_price, price)

    # [PACK D] Partial TP: at 1.5x entry, lock in profit by moving stop to break-even
    _PARTIAL_TP_MULT = 1.5 - 1e-9  # epsilon tolerance for float precision
    partial_tp_taken = bool(position.get("partial_tp_taken", False))
    partial_tp_triggered = False
    if not partial_tp_taken and entry_price > 0 and price >= entry_price * _PARTIAL_TP_MULT:
        # Move stop_loss to entry_price (break-even) to protect the profit
        stop_loss_price = max(stop_loss_price, entry_price)
        partial_tp_triggered = True
        # (The updated peak and partial_tp_taken flag are returned in peak_updates)

    # [PACK D] Trailing stop: trigger if price drops 15% from peak AND peak was 20%+ profitable
    # AND price is back at or below entry — avoids premature exit on normal market oscillation.
    # Regular stop_loss handles downside below entry independently.
    _TRAILING_TRIGGER = 1.20   # position must have been 20%+ profitable to arm trailing stop
    _TRAILING_PCT = 0.15       # retrace 15% from peak triggers the stop
    if (entry_price > 0
            and updated_peak >= entry_price * _TRAILING_TRIGGER
            and price < updated_peak * (1.0 - _TRAILING_PCT)
            and price <= entry_price):
        return {
            "action": "sell",
            "reason": "trailing_stop",
            "target_price": target_price,
            "confidence_score": confidence_score,
            "peak_price": round(updated_peak, 4),
            "partial_tp_taken": partial_tp_taken or (price >= entry_price * 1.5),
            "stop_loss_price": round(stop_loss_price, 4),
        }

    # Stop loss cooldown: don't fire within HYBRID_STOP_LOSS_COOLDOWN_HOURS of entry.
    # Prevents spread noise and early market uncertainty from triggering a premature exit.
    _stop_loss_armed = True
    _opened_at = position.get("opened_at")
    if _opened_at and now_utc is not None:
        try:
            from market_discovery_internal.config import HYBRID_STOP_LOSS_COOLDOWN_HOURS
            from datetime import timezone as _tz
            _opened_dt = datetime.fromisoformat(str(_opened_at).replace("Z", "+00:00"))
            _now_aware = now_utc if now_utc.tzinfo is not None else now_utc.replace(tzinfo=_tz.utc)
            _age_hours = (_now_aware - _opened_dt).total_seconds() / 3600
            if _age_hours < HYBRID_STOP_LOSS_COOLDOWN_HOURS:
                _stop_loss_armed = False
        except Exception:
            pass

    if _stop_loss_armed and price <= stop_loss_price:
        return {
            "action": "sell",
            "reason": "stop_loss",
            "target_price": target_price,
            "confidence_score": confidence_score,
            "peak_price": round(updated_peak, 4),
            "partial_tp_taken": partial_tp_taken,
            "stop_loss_price": round(stop_loss_price, 4),
        }

    if price >= target_price and strategy == "swing":
        return {
            "action": "sell",
            "reason": "take_profit_100pct",
            "target_price": target_price,
            "confidence_score": confidence_score,
            "peak_price": round(updated_peak, 4),
            "partial_tp_taken": True,
            "stop_loss_price": round(stop_loss_price, 4),
        }

    hours = hours_until_resolve
    if hours is None:
        hours = _hours_until_resolve_from_end_date(position.get("end_date"), now_utc=now_utc)

    # [PACK D] Thesis decay exit: late stage + low confidence → exit early
    if hours is not None and hours <= 2.0 and confidence_score < 0.45:
        return {
            "action": "sell",
            "reason": "thesis_decay_exit",
            "target_price": target_price,
            "confidence_score": confidence_score,
            "peak_price": round(updated_peak, 4),
            "partial_tp_taken": partial_tp_taken,
            "stop_loss_price": round(stop_loss_price, 4),
        }

    if hours is not None and hours <= HYBRID_LATE_WINDOW_HOURS:
        if not forecast_still_valid:
            return {
                "action": "sell",
                "reason": "late_window_forecast_invalid",
                "target_price": target_price,
                "confidence_score": confidence_score,
                "peak_price": round(updated_peak, 4),
                "partial_tp_taken": partial_tp_taken,
                "stop_loss_price": round(stop_loss_price, 4),
            }

        if confidence_score >= HYBRID_MIN_CONFIDENCE_TO_HOLD:
            return {
                "action": "hold_to_resolve",
                "reason": "late_window_confidence_pass",
                "target_price": target_price,
                "confidence_score": confidence_score,
                "peak_price": round(updated_peak, 4),
                "partial_tp_taken": partial_tp_taken,
                "stop_loss_price": round(stop_loss_price, 4),
            }

        return {
            "action": "sell",
            "reason": "late_window_confidence_below_min",
            "target_price": target_price,
            "confidence_score": confidence_score,
            "peak_price": round(updated_peak, 4),
            "partial_tp_taken": partial_tp_taken,
            "stop_loss_price": round(stop_loss_price, 4),
        }

    return {
        "action": "hold",
        "reason": "await_target",
        "target_price": target_price,
        "confidence_score": confidence_score,
        "peak_price": round(updated_peak, 4),
        "partial_tp_taken": partial_tp_taken or (price >= entry_price * _PARTIAL_TP_MULT),
        "stop_loss_price": round(stop_loss_price, 4),
    }


def close_paper_position(position, exit_price, reason, now_utc=None):
    """Close an open paper position, compute realized PnL, and record calibration + history."""
    from market_discovery_internal.pricing import calculate_taker_fee
    from market_discovery_internal.config import POLYMARKET_TAKER_FEE_RATE

    closed = {**position}
    resolved_at = now_utc or datetime.now(timezone.utc)
    price = float(exit_price)
    quantity = float(closed["quantity"])
    exit_value = round(price * quantity, 4)
    exit_fee_usd = calculate_taker_fee(quantity, price, POLYMARKET_TAKER_FEE_RATE)
    net_exit_value = round(exit_value - exit_fee_usd, 4)
    pnl_usd = round(net_exit_value - float(closed["cost_basis"]), 4)
    roi_pct = round((pnl_usd / float(closed["cost_basis"])) * 100, 4) if closed["cost_basis"] else 0.0

    # [PACK F] Compute spread cost attribution at close
    entry_price = float(closed.get("entry_price", 0.0))
    entry_bid = float(closed.get("entry_quote_best_bid") or 0.0)
    qty = float(closed.get("quantity", 0.0))
    spread_cost = round((entry_price - entry_bid) * qty, 4) if entry_bid > 0 else 0.0

    closed.update(
        {
            "status": "closed",
            "last_price": price,
            "exit_price": price,
            "exit_value": exit_value,
            "exit_fee_usd": exit_fee_usd,
            "net_exit_value": net_exit_value,
            "realized_pnl_usd": pnl_usd,
            "realized_roi_pct": roi_pct,
            "closed_at": resolved_at.isoformat(),
            "close_reason": reason,
            "spread_cost_usd": spread_cost,                   # [PACK F] attribution
            "city_cluster": closed.get("city", "unknown"),   # [PACK F] attribution
        }
    )

    # [PACK A] Record calibration outcome + [PACK F] persist trade history
    outcome = 1 if price >= 0.9 else (0 if price <= 0.1 else None)
    if outcome is not None:
        try:
            city = str(closed.get("city", "")).lower()
            direction = str(closed.get("direction", ""))
            horizon_bin = closed.get("horizon_bin", 2)
            price_bin = closed.get("price_bin", 5)
            raw_prob = float(closed.get("raw_prob") or closed.get("entry_model_prob") or 0.5)
            db.update_calibration(city, direction, horizon_bin, price_bin, outcome, raw_prob)
        except Exception:
            pass
    try:
        db.record_trade_history(closed)
    except Exception:
        pass

    return closed


def update_paper_position(
    position,
    current_yes_price,
    forecast_still_valid,
    hours_until_resolve=None,
    now_utc=None,
    confidence_score=None,
):
    """Apply hybrid exit decision and return updated position plus decision."""
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=current_yes_price,
        forecast_still_valid=forecast_still_valid,
        hours_until_resolve=hours_until_resolve,
        now_utc=now_utc,
        confidence_score=confidence_score,
    )

    updated = {
        **position,
        "last_price": float(current_yes_price),
        "last_confidence_score": decision.get("confidence_score"),
    }
    # [PACK D] Persist trailing stop state
    if "peak_price" in decision:
        updated["peak_price"] = decision["peak_price"]
    if "partial_tp_taken" in decision:
        updated["partial_tp_taken"] = decision["partial_tp_taken"]
    # [FIX] Persist stop_loss_price changes (e.g. partial TP raising stop to break-even)
    if "stop_loss_price" in decision:
        updated["stop_loss_price"] = decision["stop_loss_price"]

    if decision["action"] == "sell":
        updated = close_paper_position(
            position=updated,
            exit_price=float(current_yes_price),
            reason=decision["reason"],
            now_utc=now_utc,
        )

    return updated, decision


# ---------------------------------------------------------------------------
# Recovered Trading Logic (from Archiv)
# ---------------------------------------------------------------------------

def build_open_position_inventory(open_positions):
    """Build token and per-city occupancy maps for currently open positions."""
    open_token_ids = {str(position.get("token_id")) for position in open_positions if position.get("status") == "open"}
    open_city_counts = {}

    for position in open_positions:
        if position.get("status") != "open":
            continue

        city_key = _normalize_city_key(position.get("city"))
        if city_key:
            open_city_counts[city_key] = open_city_counts.get(city_key, 0) + 1

    return open_token_ids, open_city_counts


def _city_candidate_rank(opportunity, bucket):
    """Rank candidates by confidence, then edge, then cheaper YES price."""
    confidence = _safe_float(bucket.get("confidence"), 0.0)
    edge = _safe_float(opportunity.get("edge"), 0.0)
    yes_price = _safe_float(opportunity.get("yes_price"), 1.0)
    return (confidence, edge, -yes_price)


def build_entry_candidates(
    opportunities,
    open_token_ids,
    open_city_counts,
    min_bound,
    max_bound,
    recent_exit_blacklist=None,
):
    """Build ranked entry candidates while enforcing one-best-candidate per city."""
    bucket_counts = {
        "reject": 0,
        "watchlist": 0,
        "enter_swing": 0,
        "enter_hold_candidate": 0,
    }

    opportunity_city_keys = {
        city_key
        for city_key in (
            _normalize_city_key(opportunity.get("city"))
            for opportunity in opportunities
        )
        if city_key
    }
    candidate_city_keys = set()
    seen_event_slugs = set()
    best_candidate_by_city = {}
    cityless_candidates = []

    for index, opportunity in enumerate(opportunities):
        bucket = decide_entry_bucket(opportunity, min_bound, max_bound)
        bucket_name = bucket.get("bucket", "reject")
        bucket_counts[bucket_name] = bucket_counts.get(bucket_name, 0) + 1

        if bucket_name not in {"enter_swing", "enter_hold_candidate"}:
            continue

        token_id = opportunity.get("token_id")
        price = _safe_float(opportunity.get("yes_price"), 0.0)
        slug = opportunity.get("market_slug")

        if str(token_id) in open_token_ids:
            continue
        
        # [FIX] Whiplash Shield: Filter out recently force-closed markets
        if recent_exit_blacklist and str(token_id) in recent_exit_blacklist:
            continue

        if price < min_bound or price > max_bound:
            continue
        
        # [MODUL L] Anti-Correlation Guard: One market per specific Weather Event
        # Composite Key: City + Date + Unit (e.g., "London-2026-04-18-degC")
        event_key = f"{opportunity.get('city')}-{opportunity.get('date')}-{opportunity.get('unit')}"
        if event_key in seen_event_slugs:
            continue
        seen_event_slugs.add(event_key)
        city_key = _normalize_city_key(opportunity.get("city"))
        if city_key:
            if open_city_counts.get(city_key, 0) >= PAPER_MAX_OPEN_PER_CITY:
                continue
            candidate_city_keys.add(city_key)

        candidate = {
            "index": index,
            "city_key": city_key,
            "opportunity": opportunity,
            "bucket": bucket,
            "bucket_name": bucket_name,
            "rank": _city_candidate_rank(opportunity, bucket),
        }

        if city_key is None:
            cityless_candidates.append(candidate)
            continue

        current_best = best_candidate_by_city.get(city_key)
        if (
            current_best is None
            or candidate["rank"] > current_best["rank"]
            or (candidate["rank"] == current_best["rank"] and candidate["index"] < current_best["index"])
        ):
            best_candidate_by_city[city_key] = candidate

    entry_candidates = list(best_candidate_by_city.values()) + cityless_candidates
    entry_candidates.sort(
        key=lambda item: (
            item["rank"][0],
            item["rank"][1],
            item["rank"][2],
            -item["index"],
        ),
        reverse=True,
    )

    return entry_candidates, bucket_counts, opportunity_city_keys, candidate_city_keys


def append_opened_positions_from_candidates(
    entry_candidates,
    next_open_positions,
    open_token_ids,
    open_city_counts,
    available_slots,
    stake_usd,
    min_bound,
    max_bound,
    fetch_orderbook_quote_fn,
    is_paper_trading=True,
    available_cash=None,
    on_position_opened_fn=None, # [FIX] Added missing signature
):
    """Open positions from ranked candidates while respecting city and slot limits."""
    opened_this_cycle = []
    opened_city_keys = set()
    remaining_cash = float(available_cash) if available_cash is not None else None

    for candidate in entry_candidates:
        if available_slots <= 0:
            break

        opportunity = candidate["opportunity"]
        bucket = candidate["bucket"]
        bucket_name = candidate["bucket_name"]
        city_key = candidate["city_key"]
        token_id = opportunity.get("token_id")

        if str(token_id) in open_token_ids:
            continue
        if city_key and open_city_counts.get(city_key, 0) >= PAPER_MAX_OPEN_PER_CITY:
            continue

        # Use Gamma-provided prices first (already in opportunity from parse_market).
        # Only fall back to live CLOB fetch if Gamma data is absent.
        best_ask = _safe_float(opportunity.get("best_ask"), 0.0)
        best_bid = _safe_float(opportunity.get("best_bid"), 0.0)
        if best_ask <= 0:
            quote = fetch_orderbook_quote_fn(token_id) if callable(fetch_orderbook_quote_fn) else None
            # fetch_orderbook_quote returns {"ask": ..., "bid": ...} keys
            best_ask = _safe_float((quote or {}).get("ask") or (quote or {}).get("best_ask"), 0.0)
            best_bid = _safe_float((quote or {}).get("bid") or (quote or {}).get("best_bid"), 0.0)

        # Spread gate: skip illiquid markets at entry time
        if best_ask > 0 and best_bid > 0:
            live_spread = best_ask - best_bid
            if live_spread > MARKET_MAX_SPREAD_GATE:
                continue

        # Entry must use executable buy-side price (best ask).
        if best_ask <= 0:
            continue
        if best_ask < min_bound or best_ask > max_bound:
            continue

        # [MODUL L] Liquidity Awareness: Adjust stake based on depth and max 3% slippage
        from market_discovery_internal.config import MAX_ACCEPTABLE_SLIPPAGE, MIN_STAKE_THRESHOLD
        from market_discovery_internal.pricing import calculate_depth_adjusted_stake

        from market_discovery_internal.config import PAPER_BYPASS_LIQUIDITY_CHECK
        if PAPER_BYPASS_LIQUIDITY_CHECK:
            dynamic_stake = float(stake_usd)
        else:
            dynamic_stake = calculate_depth_adjusted_stake(token_id, stake_usd, max_slippage_pct=MAX_ACCEPTABLE_SLIPPAGE)
            if dynamic_stake <= 0:
                logger.warning(f"[LIQUIDITY] Skipping {token_id} - Insufficient depth for safe entry (Slippage Threat).")
                continue
            if dynamic_stake < MIN_STAKE_THRESHOLD:
                logger.warning(f"[DUST-FILTER] Skipping {token_id} - Dynamic stake ${dynamic_stake:.2f} < floor ${MIN_STAKE_THRESHOLD:.2f}.")
                continue

        if dynamic_stake < float(stake_usd):
            logger.info(f"[LIQUIDITY] Scaling down stake for {token_id}: ${stake_usd} -> ${dynamic_stake}")

        # [PACK B] Regime gate: apply per-market regime thresholds
        regime_class = opportunity.get("regime_class", "neutral")
        regime_gates = opportunity.get("regime_gates") or {"min_prob": 0.72, "min_edge": 0.22, "max_price": 0.60}
        _r_min_prob = float(regime_gates.get("min_prob", 0.72))
        _r_min_edge = float(regime_gates.get("min_edge", 0.22))
        _r_max_price = float(regime_gates.get("max_price", 0.60))
        _r_model_prob = float(opportunity.get("model_prob", 0.0))
        _r_edge = float(opportunity.get("edge", 0.0))
        if _r_model_prob < _r_min_prob or _r_edge < _r_min_edge or best_ask > _r_max_price:
            logger.debug(f"[REGIME-{regime_class.upper()}] Skipping {token_id}: "
                         f"prob={_r_model_prob:.3f}<{_r_min_prob:.3f} or "
                         f"edge={_r_edge:.3f}<{_r_min_edge:.3f} or "
                         f"price={best_ask:.3f}>{_r_max_price:.3f}")
            continue

        # [FIX] Fixed stake: always use exact configured stake_usd.
        # Dynamic risk-weighting and depth scaling caused inconsistent spend per trade.
        # User expectation: $1 stake = exactly $1 total (cost + fee).
        risk_weighted_stake = float(stake_usd)
        _risk_multiplier = 1.0

        # [PACK C] Correlation cap: if city already has an open position, reduce stake 30%
        if city_key and open_city_counts.get(city_key, 0) >= 1:
            risk_weighted_stake = round(risk_weighted_stake * 0.70, 2)
            _risk_multiplier *= 0.70

        entry_opportunity = {
            **opportunity,
            "entry_price": round(best_ask, 4),
            "entry_price_source": "buy_ask",
            "entry_yes_reference": _safe_float(opportunity.get("yes_price"), best_ask),
            "entry_quote_best_ask": round(best_ask, 4),
        }
        if best_bid > 0:
            entry_opportunity["entry_quote_best_bid"] = round(best_bid, 4)

        position = build_paper_position(entry_opportunity, stake_usd=risk_weighted_stake)

        # [ACCOUNTING GUARD] Hard block if cost_basis exceeds remaining cash
        position_cost = float(position.get("cost_basis", 0.0))
        if remaining_cash is not None and position_cost > remaining_cash:
            logger.warning(
                "[ACCOUNTING] Skipping %s — cost_basis $%.4f > remaining cash $%.4f",
                token_id, position_cost, remaining_cash,
            )
            continue

        position["entry_bucket"] = bucket_name
        position["entry_bucket_reason"] = bucket.get("reason")
        position["entry_confidence_score"] = bucket.get("confidence")
        position["entry_ai_status"] = opportunity.get("ai_status", "off")
        # [PACK A] Store calibration bins for later outcome recording
        position["horizon_bin"] = opportunity.get("horizon_bin", 2)
        position["price_bin"] = opportunity.get("price_bin", 5)
        position["raw_prob"] = opportunity.get("raw_prob", opportunity.get("model_prob"))
        # [PACK B] Regime context
        position["entry_regime_class"] = regime_class
        position["entry_regime_score"] = opportunity.get("regime_score", 0.5)
        # [PACK C] Sizing audit trail
        position["risk_multiplier"] = round(_risk_multiplier, 4)

        if opportunity.get("ai_bucket") is not None:
            position["entry_ai_bucket"] = opportunity.get("ai_bucket")
        if opportunity.get("ai_confidence") is not None:
            position["entry_ai_confidence"] = opportunity.get("ai_confidence")
        if bucket.get("ai_override_applied"):
            position["entry_ai_override_applied"] = True

        if remaining_cash is not None:
            remaining_cash -= position_cost

        next_open_positions.append(position)
        opened_this_cycle.append(position)
        
        # [FIX] Invoke instant callback for WebSocket refresh
        if on_position_opened_fn:
            try:
                on_position_opened_fn(position)
            except Exception as _cb_err:
                logger.error(f"[WS-CALLBACK] Error: {_cb_err}")

        open_token_ids.add(str(token_id))
        if city_key:
            open_city_counts[city_key] = open_city_counts.get(city_key, 0) + 1
            opened_city_keys.add(city_key)
        available_slots -= 1

    return opened_this_cycle, opened_city_keys, available_slots


# ---------------------------------------------------------------------------
# [PACK E] Auto-Tuner Activation
# ---------------------------------------------------------------------------

def compute_auto_tuner_adjustments(min_trades=3):
    """
    [PACK E] Compute per-city edge/prob adjustments from recent trade history.

    Logic:
    - Win rate > AUTO_TUNER_AGGRESSIVE_WIN_RATE → relax min_edge by EDGE_RELAX (reward)
    - Win rate < AUTO_TUNER_CAUTION_WIN_RATE    → penalize min_edge by EDGE_PENALTY (tighten)
    - Continuous consecutive stop-loss rate > BLACKLIST threshold → blacklist city for this cycle

    Returns: {city: {"adj_edge": float, "adj_prob": float, "blacklisted": bool, "win_rate": float, "n": int}}
    """
    from market_discovery_internal.config import (
        AUTO_TUNER_ENABLED, AUTO_TUNER_MIN_TRADES, AUTO_TUNER_EDGE_RELAX,
        AUTO_TUNER_EDGE_PENALTY, AUTO_TUNER_AGGRESSIVE_WIN_RATE,
        AUTO_TUNER_CAUTION_WIN_RATE, AUTO_TUNER_BLACKLIST_PNL,
        AUTO_TUNER_BLACKLIST_STOP_LOSS_RATE,
    )
    if not AUTO_TUNER_ENABLED:
        return {}

    min_t = max(min_trades, AUTO_TUNER_MIN_TRADES)
    adjustments = {}

    try:
        from market_discovery_internal.config import TARGET_CITIES
        cities = list(TARGET_CITIES.keys())
    except Exception:
        return {}

    for city in cities:
        try:
            trades = db.get_trade_history_for_city(city, limit=20)
        except Exception:
            continue
        if len(trades) < min_t:
            continue

        wins = sum(1 for t in trades if float(t.get("pnl_usd", 0.0)) > 0)
        stop_losses = sum(1 for t in trades if "stop_loss" in str(t.get("close_reason", "")))
        n = len(trades)
        win_rate = wins / n

        # Blacklist check: too many stop losses + negative avg PnL
        avg_pnl = sum(float(t.get("pnl_usd", 0.0)) for t in trades) / n
        blacklisted = (stop_losses / n >= AUTO_TUNER_BLACKLIST_STOP_LOSS_RATE
                       and avg_pnl <= AUTO_TUNER_BLACKLIST_PNL)

        if win_rate >= AUTO_TUNER_AGGRESSIVE_WIN_RATE:
            adj_edge = -AUTO_TUNER_EDGE_RELAX   # relax (more opportunities)
            adj_prob = -AUTO_TUNER_EDGE_RELAX
        elif win_rate < AUTO_TUNER_CAUTION_WIN_RATE:
            adj_edge = AUTO_TUNER_EDGE_PENALTY   # penalize (tighter gate)
            adj_prob = AUTO_TUNER_EDGE_PENALTY * 0.5
        else:
            adj_edge = 0.0
            adj_prob = 0.0

        if blacklisted:
            adj_edge = 1.0  # effectively block this city
            adj_prob = 1.0

        adjustments[city] = {
            "adj_edge": round(adj_edge, 4),
            "adj_prob": round(adj_prob, 4),
            "win_rate": round(win_rate, 4),
            "n": n,
            "blacklisted": blacklisted,
        }

    return adjustments


# ---------------------------------------------------------------------------
# [PACK F] Champion-Challenger & Profit Attribution
# ---------------------------------------------------------------------------

def build_profit_attribution_report(limit=200):
    """
    [PACK F] Build profit attribution report grouped by city, bucket, and exit reason.

    Returns a dict with top leakers and top earners.
    """
    try:
        trades = db.get_recent_trade_history(limit=limit)
    except Exception:
        return {}

    if not trades:
        return {}

    # Group by city
    by_city = {}
    by_bucket = {}
    by_exit_reason = {}

    for t in trades:
        pnl = float(t.get("pnl_usd", 0.0) or 0.0)
        city = str(t.get("city") or "unknown").lower()
        bucket = str(t.get("entry_bucket") or "unknown")
        reason = str(t.get("close_reason") or "unknown")

        for group, key in [(by_city, city), (by_bucket, bucket), (by_exit_reason, reason)]:
            if key not in group:
                group[key] = {"total_pnl": 0.0, "count": 0, "wins": 0}
            group[key]["total_pnl"] = round(group[key]["total_pnl"] + pnl, 4)
            group[key]["count"] += 1
            if pnl > 0:
                group[key]["wins"] += 1

    def _sorted_desc(d):
        return sorted(
            [{"key": k, **v, "win_rate": round(v["wins"] / v["count"], 3) if v["count"] else 0}
             for k, v in d.items()],
            key=lambda x: x["total_pnl"]
        )

    city_sorted = _sorted_desc(by_city)
    bucket_sorted = _sorted_desc(by_bucket)
    reason_sorted = _sorted_desc(by_exit_reason)

    return {
        "top_leakers_city": city_sorted[:5],        # worst cities (negative PnL first)
        "top_earners_city": list(reversed(city_sorted))[:5],
        "by_exit_reason": reason_sorted,
        "by_entry_bucket": bucket_sorted,
        "trades_analyzed": len(trades),
    }
