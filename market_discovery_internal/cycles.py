"""Cycle orchestration helpers for market_discovery."""


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
            if spread is not None and float(spread) > _max_spread:
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
    allow_new_entries=True,
    entry_gate_reason="active",
):
    """Run one paper-trading cycle: discover, manage exits, open new positions."""
    cycle_started = perf_counter_fn()

    state_load_started = perf_counter_fn()
    now = now_utc_fn()
    state = load_paper_state_fn(path=state_path)
    state_load_ms = elapsed_ms_fn(state_load_started)

    state_meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    empty_temperature_cycles = int(state_meta.get("empty_temperature_cycles", 0) or 0)

    auto_aggressive = (
        discovery_enable_auto_aggressive_scan
        and empty_temperature_cycles >= max(0, discovery_auto_aggressive_after_empty_cycles)
    )
    use_aggressive_scan = force_aggressive_scan or auto_aggressive

    discovery_started = perf_counter_fn()
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
        if raw_bid is None:
            raw_bid = live_market.get("best_bid")
        if raw_bid is None:
            raw_bid = position.get("last_price")

        try:
            current_yes_price = float(raw_bid)
        except (TypeError, ValueError):
            next_open_positions.append(position)
            continue
        if current_yes_price <= 0:
            next_open_positions.append(position)
            continue

        hours_until_resolve = live_market.get("hours_until_resolve")
        forecast_valid = forecast_still_valid_fn(
            position,
            current_yes_price,
            hours_until_resolve,
            forecast_cache=position_forecast_cache,
            forecast_cache_stats=position_forecast_cache_stats,
        )
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
    if effective_allow_new_entries and daily_session:
        baseline_wallet = float(daily_session.get("baseline_wallet", 0.0) or 0.0)
        target_wallet = float(daily_session.get("target_wallet", 0.0) or 0.0)
        state_base_wallet = float(state_meta.get("base_wallet", baseline_wallet) or baseline_wallet)
        realized_after_position_management = sum(
            float(position.get("realized_pnl_usd", 0.0) or 0.0)
            for position in next_history
        )
        wallet_after_position_management = round(state_base_wallet + realized_after_position_management, 4)
        if target_wallet > 0 and wallet_after_position_management >= target_wallet:
            effective_allow_new_entries = False
            effective_entry_gate_reason = "daily_target_reached"

    if effective_allow_new_entries:
        opened_this_cycle, opened_city_keys, _ = append_opened_positions_from_candidates_fn(
            entry_candidates=entry_candidates,
            next_open_positions=next_open_positions,
            open_token_ids=open_token_ids,
            open_city_counts=open_city_counts,
            available_slots=available_slots,
            stake_usd=stake_usd,
            min_bound=min_bound,
            max_bound=max_bound,
            fetch_orderbook_quote_fn=_get_orderbook_quote,
        )
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
        cycle_city_coverage=city_coverage_metrics,
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
