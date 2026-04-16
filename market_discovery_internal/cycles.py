import logging
import time
from datetime import datetime, timezone
from market_discovery_internal.utils import _safe_float, _safe_div, _clamp, send_telegram_alert
from market_discovery_internal.config import (
    HYBRID_TAKE_PROFIT_MIN_PRICE, HYBRID_TAKE_PROFIT_MULTIPLIER,
    HYBRID_STOP_LOSS_MULTIPLIER, HYBRID_LATE_WINDOW_HOURS,
    HYBRID_MIN_CONFIDENCE_TO_HOLD, HYBRID_CONFIDENCE_EDGE_SCALE,
    PAPER_STAKE_USD, PAPER_BASE_WALLET, PAPER_MAX_OPEN_PER_CITY,
    MARKET_MAX_SPREAD_GATE
)
from market_discovery_internal.parsing import _normalize_city_key
from market_discovery_internal.analysis import decide_entry_bucket

logger = logging.getLogger(__name__)


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
    discovery_forecast_prefetch_min_keys,
    discovery_forecast_prefetch_max_workers,
):
    """Enrich parsed markets with forecast/edge and keep forecast cache diagnostics."""
    failed_cities = []
    failed_city_keys = set()
    enriched = []
    forecast_cache = {}
    forecast_cache_stats = {"hits": 0, "misses": 0}

    prefetch_started = perf_counter_fn()
    forecast_prefetch = prefetch_forecasts_fn(
        cache_keys=[(market.get("city"), market.get("date")) for market in parsed],
        cache=forecast_cache,
        min_keys=discovery_forecast_prefetch_min_keys,
        max_workers=discovery_forecast_prefetch_max_workers,
    )
    forecast_prefetch_ms = elapsed_ms_fn(prefetch_started)

    enrich_started = perf_counter_fn()
    for market in parsed:
        city = market["city"]
        date = market["date"]
        forecast_temp = fetch_forecast_with_cache_fn(
            city,
            date,
            forecast_cache,
            stats=forecast_cache_stats,
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
            edge_result["weather_evidence"] = evidence
            edge_result["weather_evidence_valid"] = evidence_valid
            edge_result["weather_evidence_quality_score"] = evidence.get("quality_score")
            edge_result["weather_evidence_age_hours"] = evidence.get("age_hours")
            enriched.append(maybe_apply_ai_decision_fn(edge_result))
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

    fetch_started = perf_counter_fn()
    markets_raw = fetch_markets_fn(inspect=inspect, aggressive_scan=aggressive_scan)
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
):
    """Run one paper-trading cycle: discover, manage exits, open new positions."""
    cycle_started = perf_counter_fn()

    state_load_started = perf_counter_fn()
    now = now_utc_fn()
    state = load_paper_state_fn(path=state_path)
    state_load_ms = elapsed_ms_fn(state_load_started)

    state_meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    
    # [DAILY RESET] reset baseline_wallet at 00:00 UTC
    last_cycle_at_raw = state_meta.get("last_cycle_at")
    now_utc = datetime.now(timezone.utc)
    if last_cycle_at_raw:
        from market_discovery_internal.reporting import parse_utc_datetime
        last_cycle_dt = parse_utc_datetime(last_cycle_at_raw)
        if last_cycle_dt and last_cycle_dt.date() < now_utc.date():
            # Reset daily session for the new day
            daily_session = state_meta.get("daily_session", {})
            if isinstance(daily_session, dict):
                current_total = float(state_meta.get("current_wallet", PAPER_BASE_WALLET))
                daily_session["baseline_wallet"] = current_total
                state_meta["daily_session"] = daily_session
                logger.info(f"Daily session reset: new baseline is ${current_total:.2f}")

    empty_temperature_cycles = int(state_meta.get("empty_temperature_cycles", 0) or 0)

    auto_aggressive = (
        discovery_enable_auto_aggressive_scan
        and empty_temperature_cycles >= max(0, discovery_auto_aggressive_after_empty_cycles)
    )
    use_aggressive_scan = force_aggressive_scan or auto_aggressive

    discovery_started = perf_counter_fn()
    
    # --- HYBRID GUARD (FAILOVER) ---
    ws_stale = False
    if last_ws_update_at is not None and len(state.get("positions", [])) > 0:
        elapsed_since_ws = (time.time() - last_ws_update_at.value) / 60.0
        if elapsed_since_ws > ws_stale_detection_minutes:
            ws_stale = True
            logger.warning("[GUARD] WebSocket STALE (%.1fm). Switching to Aggressive Scan.", elapsed_since_ws)

    # --- SUPERVISOR LOGIC ---
    if ws_watcher and hasattr(ws_watcher, "_process") and ws_watcher._process:
        if not ws_watcher._process.is_alive():
            logger.warning("[SUPERVISOR] PriceWatcher process DIED. Restarting...")
            ws_watcher.start()

    # Sync subscriptions to MPC process
    if ws_watcher and hasattr(ws_watcher, "update_subscriptions"):
        open_ids = {p["token_id"] for p in state.get("positions", []) if p.get("status") == "open"}
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
            (position.get("city"), position.get("date"))
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

        ask_sane_limit = max(entry_price * 3.0, 0.60)  # ask above 3x entry or 60c is rogue

        if bid_val > 0 and ask_val > 0 and ask_val <= ask_sane_limit:
            # Both sides are reasonable — use mid-price
            current_yes_price = (bid_val + ask_val) / 2.0
        elif bid_val > 0:
            # Use bid (conservative: what you could sell for)
            current_yes_price = bid_val
        elif ask_val > 0 and ask_val <= ask_sane_limit:
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
                monitor_result = haiku_position_monitor_fn(
                    position,
                    current_yes_price=current_yes_price,
                    hours_until_resolve=hours_until_resolve,
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
                    now_utc=now,
                )
                forced_closed["haiku_monitor_confidence"] = round(monitor_confidence, 4)
                forced_closed["haiku_monitor_reasoning"] = str(monitor_result.get("reasoning") or "")
                closed_this_cycle.append(forced_closed)
                next_history.append(forced_closed)
                haiku_monitor_forced_exits += 1
                continue

        forecast_valid = forecast_still_valid_fn(
            position,
            current_yes_price,
            hours_until_resolve,
            forecast_cache=position_forecast_cache,
            forecast_cache_stats=position_forecast_cache_stats,
        )
        
        # [MODUL G] Exit Sniper (Aggressive sweeps)
        if current_yes_price >= 0.90:
            forced_closed = close_paper_position_fn(
                position=position,
                exit_price=current_yes_price,
                reason="sniper_take_profit",
                now_utc=now,
            )
            closed_this_cycle.append(forced_closed)
            next_history.append(forced_closed)
            continue
            
        if not forecast_valid:
            forced_closed = close_paper_position_fn(
                position=position,
                exit_price=current_yes_price,
                reason="sniper_stop_loss_thesis_broken",
                now_utc=now,
            )
            closed_this_cycle.append(forced_closed)
            next_history.append(forced_closed)
            continue

        confidence_score = position_confidence_score_fn(position, current_yes_price, forecast_valid)

        updated_position, decision = update_paper_position_fn(
            position=position,
            current_yes_price=current_yes_price,
            forecast_still_valid=forecast_valid,
            hours_until_resolve=hours_until_resolve,
            now_utc=now,
            confidence_score=confidence_score,
        )

        if decision["action"] == "hold_to_resolve" and hours_until_resolve is not None and hours_until_resolve <= 0:
            settle_price = 1.0 if forecast_valid else 0.0
            updated_position = close_paper_position_fn(
                position=updated_position,
                exit_price=settle_price,
                reason="resolved_after_hold",
                now_utc=now,
            )

        if updated_position.get("status") == "closed":
            closed_this_cycle.append(updated_position)
            next_history.append(updated_position)
            
            # [MODUL N] Telegram Notification for Closure
            pnl = float(updated_position.get("realized_pnl_usd", 0.0))
            roi = float(updated_position.get("realized_roi_pct", 0.0))
            emoji = "✅" if pnl > 0 else "❌"
            result_text = "CUAN" if pnl > 0 else "LOSS"
            msg = (
                f"{emoji} **POSITION CLOSED: {result_text}** {emoji}\n\n"
                f"📍 *City*: {updated_position.get('city')}\n"
                f"❓ *Market*: {updated_position.get('market_question')}\n"
                f"💰 *PnL*: **USD {pnl:+.2f} ({roi:+.1f}%)**\n"
                f"📉 *Exit Price*: USD {updated_position.get('exit_price'):.4f}\n"
                f"🏁 *Reason*: {updated_position.get('close_reason', 'unknown')}"
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

    # [MODUL L] Circuit Breaker
    if effective_allow_new_entries and wallet_after_position_management <= 3.50:
        effective_allow_new_entries = False
        effective_entry_gate_reason = "circuit_breaker_tripped"
        if not state_meta.get("circuit_breaker_alert_sent"):
            send_telegram_alert(
                f"⚡ **CIRCUIT BREAKER: HALTED** ⚡\n\n"
                f"⚠️ *Reason*: Daily Loss Limit Reached\n"
                f"📉 *Current Wallet*: USD {wallet_after_position_management:.2f}\n\n"
                f"Protokol risiko aktif: bot berhenti buka posisi baru sampai reset harian."
            )
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

    # [SAFE LEVERAGE CAP] Ensure Total Exposure <= 100% Wallet
    # If Total Exposure (stake * total_slots) > Wallet, reduce stake.
    max_total_exposure = wallet_after_position_management
    if (current_stake_usd * current_tier_max_slots) > max_total_exposure:
        current_stake_usd = round(max_total_exposure / current_tier_max_slots, 2)

    # Notify on Tier Change
    prev_tier = state_meta.get("current_tier", 1)
    if new_tier != prev_tier:
        send_telegram_alert(
            f"📊 **SYSTEM TIER: UPDATE** 📊\n\n"
            f"📈 *New Level*: **TIER {new_tier}**\n"
            f"💰 *Vault Balance*: USD {wallet_after_position_management:.2f}\n"
            f"⚔️ *Stake per Pos*: USD {current_stake_usd:.2f}\n"
            f"📦 *Max Slots*: {current_tier_max_slots}\n\n"
            f"Strategi penempatan disesuaikan otomatis."
        )
    state_meta["current_tier"] = new_tier

    available_slots = max(current_tier_max_slots - len(open_token_ids), 0)

    if effective_allow_new_entries:
        opened_this_cycle, opened_city_keys, _ = append_opened_positions_from_candidates_fn(
            entry_candidates=entry_candidates,
            next_open_positions=next_open_positions,
            open_token_ids=open_token_ids,
            open_city_counts=open_city_counts,
            available_slots=available_slots,
            stake_usd=current_stake_usd,
            min_bound=min_bound,
            max_bound=max_bound,
            fetch_orderbook_quote_fn=_get_orderbook_quote,
        )
        # [MODUL N] Telegram Notification for Entries
        for pos in opened_this_cycle:
            # Brainstorming: Add Link, Reason, Forecast Context, and Resolution Time
            slug = pos.get('market_slug')
            link = f"https://polymarket.com/event/{slug}" if slug else "N/A"
            reason = pos.get('entry_bucket_reason', 'Criteria met')
            forecast = pos.get('forecast_temp_c', 'N/A')
            
            # Calculate hours until resolve for better context
            from market_discovery_internal.reporting import parse_utc_datetime
            now_utc = datetime.now(timezone.utc)
            end_dt = parse_utc_datetime(pos.get('end_date'))
            hours_left = round((end_dt - now_utc).total_seconds() / 3600, 1) if end_dt else "N/A"
            
            # Confidence Meter Visual
            prob = float(pos.get('entry_model_prob', 0))
            meter = "🟢" * int(prob * 5) + "⚪" * (5 - int(prob * 5))

            msg = (
                f"🚀 **NEW ENTRY: OPENED** 🚀\n\n"
                f"📊 *Bucket*: **{pos.get('entry_bucket', 'SWING').replace('_candidate', '').upper()}**\n"
                f"📍 *City*: {pos.get('city').upper()}\n"
                f"⚖️ *Target*: {pos.get('threshold')}{pos.get('unit')} {pos.get('direction').upper()}\n"
                f"🌡️ *Forecast*: {forecast}°C ({pos.get('direction').lower()} target)\n\n"
                f"💵 *Stake*: USD {pos.get('cost_basis'):.2f}\n"
                f"🏷️ *Price*: USD {pos.get('entry_price'):.4f} (TP: {pos.get('target_price'):.4f})\n"
                f"🎯 *Model*: {meter} ({prob*100:.1f}%)\n"
                f"📝 *Reason*: {reason}\n"
                f"⏳ *Resolves in*: {hours_left}h\n\n"
                f"🔗 [View on Polymarket]({link})"
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
        now_utc=now,
        cycle_metrics=cycle_metrics,
        bucket_counts=bucket_counts,
        cycle=cycle_payload,
        city_coverage_metrics=city_coverage_metrics,
        performance_metrics=performance,
    )

    max_entries = max(1, int(paper_journal_max_entries))
    previous_journal = state.get("cycle_journal") if isinstance(state.get("cycle_journal"), list) else []
    next_cycle_journal = (previous_journal + [cycle_entry])[-max_entries:]

    next_meta = {
        **state_meta,
        "empty_temperature_cycles": empty_temperature_cycles,
        "last_cycle_at": now.isoformat(),
        "last_cycle_metrics": cycle_metrics,
        "last_entry_gate_open": bool(effective_allow_new_entries),
        "last_entry_gate_reason": str(effective_entry_gate_reason),
        "acceptance_metrics_rolling": rolling_metrics,
        "city_coverage_rolling": rolling_city_coverage_metrics,
        "last_cycle_performance": performance,
        "current_wallet": float(wallet_after_position_management),
    }

    next_state = {
        "positions": next_open_positions,
        "history": next_history,
        "cycle_journal": next_cycle_journal,
        "updated_at": now.isoformat(),
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
    from market_discovery_internal.pricing import check_liquidity_depth
    
    # [MODUL E] Liquidity Depth Gate
    token_id = opportunity.get("token_id")
    if not check_liquidity_depth(token_id, stake_usd):
        raise ValueError(f"Market {token_id} rejected due to insufficient liquidity depth at Best Ask.")
        
    entry_price = _safe_float(opportunity.get("entry_price"), _safe_float(opportunity.get("yes_price"), 0.0))
    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")

    quantity = round(float(stake_usd) / entry_price, 6)
    cost_basis = round(quantity * entry_price, 4)
    target_price = _compute_take_profit_price(entry_price)
    entry_yes_reference = _safe_float(opportunity.get("yes_price"), entry_price)
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
        "entry_price": entry_price,
        "entry_price_source": entry_price_source,
        "entry_yes_reference": entry_yes_reference,
        "quantity": quantity,
        "cost_basis": cost_basis,
        "target_price": target_price,
        "target_price_low": target_price,
        "target_price_high": target_price,
        "target_policy": "take_profit_100pct",
        "target_strategy": opportunity.get("strategy", "swing"),
        "stop_loss_price": round(entry_price * HYBRID_STOP_LOSS_MULTIPLIER, 4),
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
    Hybrid exit strategy:
     1) Take-profit at +100% from entry (2x by default).
    2) Stop-loss when current price <= configured stop-loss price.
    3) If within late window (H-2 by default):
       - forecast valid + confidence >= min threshold -> hold to resolve
       - otherwise -> sell
    4) Otherwise hold and wait.
    """
    price = float(current_yes_price)
    target_price = float(position.get("target_price", _compute_take_profit_price(position.get("entry_price"))))
    stop_loss_price = float(position["stop_loss_price"])

    if confidence_score is None:
        confidence_score = 1.0 if forecast_still_valid else 0.0
    confidence_score = max(0.0, min(float(confidence_score), 1.0))
    strategy = position.get("target_strategy", "swing")
    
    # [LOG] Crucial diagnostic to see why NYC might be closing
    if "new york city" in str(position.get("city", "")).lower():
        print(f"[STRATEGY-AUDIT] {position.get('city').upper()} | Strategy: {strategy} | Price: {price} | Target: {target_price}")

    if price <= stop_loss_price:
        return {
            "action": "sell",
            "reason": "stop_loss",
            "target_price": target_price,
            "confidence_score": confidence_score,
        }

    strategy = position.get("target_strategy", "swing")
    if price >= target_price and strategy == "swing":
        return {
            "action": "sell",
            "reason": "take_profit_100pct",
            "target_price": target_price,
            "confidence_score": confidence_score,
        }

    hours = hours_until_resolve
    if hours is None:
        hours = _hours_until_resolve_from_end_date(position.get("end_date"), now_utc=now_utc)

    if hours is not None and hours <= HYBRID_LATE_WINDOW_HOURS:
        if not forecast_still_valid:
            return {
                "action": "sell",
                "reason": "late_window_forecast_invalid",
                "target_price": target_price,
                "confidence_score": confidence_score,
            }

        if confidence_score >= HYBRID_MIN_CONFIDENCE_TO_HOLD:
            return {
                "action": "hold_to_resolve",
                "reason": "late_window_confidence_pass",
                "target_price": target_price,
                "confidence_score": confidence_score,
            }

        return {
            "action": "sell",
            "reason": "late_window_confidence_below_min",
            "target_price": target_price,
            "confidence_score": confidence_score,
        }

    return {
        "action": "hold",
        "reason": "await_target",
        "target_price": target_price,
        "confidence_score": confidence_score,
    }


def close_paper_position(position, exit_price, reason, now_utc=None):
    """Close an open paper position and compute realized PnL metrics."""
    closed = {**position}
    resolved_at = now_utc or datetime.now(timezone.utc)
    price = float(exit_price)
    exit_value = round(price * float(closed["quantity"]), 4)
    pnl_usd = round(exit_value - float(closed["cost_basis"]), 4)
    roi_pct = round((pnl_usd / float(closed["cost_basis"])) * 100, 4) if closed["cost_basis"] else 0.0

    closed.update(
        {
            "status": "closed",
            "last_price": price,
            "exit_price": price,
            "exit_value": exit_value,
            "realized_pnl_usd": pnl_usd,
            "realized_roi_pct": roi_pct,
            "closed_at": resolved_at.isoformat(),
            "close_reason": reason,
        }
    )

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
    open_token_ids = {position.get("token_id") for position in open_positions if position.get("status") == "open"}
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


def build_entry_candidates(opportunities, open_token_ids, open_city_counts, min_bound, max_bound):
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

        if str(token_id) in open_token_ids:
            continue
        if price < min_bound or price > max_bound:
            continue

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
):
    """Open positions from ranked candidates while respecting city and slot limits."""
    opened_this_cycle = []
    opened_city_keys = set()

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
            best_ask = _safe_float((quote or {}).get("best_ask"), 0.0)
            best_bid = _safe_float((quote or {}).get("best_bid"), 0.0)

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

        entry_opportunity = {
            **opportunity,
            "entry_price": round(best_ask, 4),
            "entry_price_source": "buy_ask",
            "entry_yes_reference": _safe_float(opportunity.get("yes_price"), best_ask),
            "entry_quote_best_ask": round(best_ask, 4),
        }
        if best_bid > 0:
            entry_opportunity["entry_quote_best_bid"] = round(best_bid, 4)

        position = build_paper_position(entry_opportunity, stake_usd=stake_usd)
        position["entry_bucket"] = bucket_name
        position["entry_bucket_reason"] = bucket.get("reason")
        position["entry_confidence_score"] = bucket.get("confidence")
        position["entry_ai_status"] = opportunity.get("ai_status", "off")
        
        if opportunity.get("ai_bucket") is not None:
            position["entry_ai_bucket"] = opportunity.get("ai_bucket")
        if opportunity.get("ai_confidence") is not None:
            position["entry_ai_confidence"] = opportunity.get("ai_confidence")
        if bucket.get("ai_override_applied"):
            position["entry_ai_override_applied"] = True

        next_open_positions.append(position)
        opened_this_cycle.append(position)
        open_token_ids.add(str(token_id))
        if city_key:
            open_city_counts[city_key] = open_city_counts.get(city_key, 0) + 1
            opened_city_keys.add(city_key)
        available_slots -= 1

    return opened_this_cycle, opened_city_keys, available_slots
