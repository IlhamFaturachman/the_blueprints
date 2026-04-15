with open("market_discovery.py", "r") as f:
    code = f.read()

# Replace load/save logic
old_load_save = """def load_paper_state(path=PAPER_STATE_FILE):
    \"\"\"Load paper-trading state from disk or return an empty state.\"\"\"
    return _load_paper_state_impl(path=path)


def save_paper_state(state, path=PAPER_STATE_FILE):
    \"\"\"Persist paper-trading state to disk.\"\"\"
    _save_paper_state_impl(state=state, path=path)"""

new_load_save = """def _update_cash_state(state):
    import os
    base_wallet = float(os.getenv("PAPER_MAX_OPEN_POSITIONS", 5)) * 1.0
    history = state.get("history", [])
    positions = state.get("positions", [])
    meta = state.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    
    realized_pnl = sum(float(p.get("realized_pnl_usd", 0.0)) for p in history)
    open_cost = sum(float(p.get("cost_basis", 0.0)) for p in positions)
    
    current_wallet = round(base_wallet + realized_pnl, 4)
    cash = round(current_wallet - open_cost, 4)
    
    meta["base_wallet"] = base_wallet
    meta["current_wallet"] = current_wallet
    meta["cash"] = cash
    meta["realized_pnl_usd"] = round(realized_pnl, 4)
    meta["open_cost_basis"] = round(open_cost, 4)
    
    state["meta"] = meta
    return state

def load_paper_state(path=PAPER_STATE_FILE):
    \"\"\"Load paper-trading state from disk or return an empty state.\"\"\"
    return _update_cash_state(_load_paper_state_impl(path=path))

def save_paper_state(state, path=PAPER_STATE_FILE):
    \"\"\"Persist paper-trading state to disk.\"\"\"
    state = _update_cash_state(state)
    _save_paper_state_impl(state=state, path=path)"""

code = code.replace(old_load_save, new_load_save)

# Replace cycle run
old_cycle = """def run_paper_trading_cycle(
    min_price=None,
    max_price=None,
    stake_usd=PAPER_STAKE_USD,
    state_path=PAPER_STATE_FILE,
    force_aggressive_scan=False,
):
    \"\"\"Run one paper-trading cycle: discover, manage exits, open new positions.\"\"\"
    try:
        state = load_paper_state(state_path)
        realized_pnl = float(state.get("realized_pnl", 0.0))
        # Initial fund = max_positions (5) * initial_stake (1) = 5
        # If the user started with different config, we assume 5.0 base.
        import os
        base_wallet = float(os.getenv("PAPER_MAX_OPEN_POSITIONS", 5)) * 1.0
        current_wallet = base_wallet + realized_pnl
        if current_wallet >= 10.0:
            stake_usd = float(int(current_wallet / 5.0))
    except Exception:
        pass

    return _run_paper_trading_cycle_impl(

        min_price=min_price,
        max_price=max_price,
        stake_usd=stake_usd,
        state_path=state_path,
        force_aggressive_scan=force_aggressive_scan,
        perf_counter_fn=time.perf_counter,
        now_utc_fn=lambda: datetime.now(timezone.utc),
        elapsed_ms_fn=_elapsed_ms,
        load_paper_state_fn=load_paper_state,
        run_discovery_cycle_fn=run_discovery_cycle,
        prefetch_forecasts_fn=_prefetch_forecasts,
        ensure_take_profit_target_fn=_ensure_take_profit_target,
        forecast_still_valid_fn=_forecast_still_valid,
        position_confidence_score_fn=_position_confidence_score,
        update_paper_position_fn=update_paper_position,
        close_paper_position_fn=close_paper_position,
        build_open_position_inventory_fn=_build_open_position_inventory,
        build_entry_candidates_fn=_build_entry_candidates,
        append_opened_positions_from_candidates_fn=_append_opened_positions_from_candidates,
        build_city_coverage_metrics_fn=_build_city_coverage_metrics,
        build_cycle_acceptance_metrics_fn=_build_cycle_acceptance_metrics,
        build_rolling_acceptance_metrics_fn=_build_rolling_acceptance_metrics,
        build_rolling_city_coverage_metrics_fn=_build_rolling_city_coverage_metrics,
        build_cycle_journal_entry_fn=_build_cycle_journal_entry,
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
    )"""

new_cycle = """def run_paper_trading_cycle(
    min_price=None,
    max_price=None,
    stake_usd=PAPER_STAKE_USD,
    state_path=PAPER_STATE_FILE,
    force_aggressive_scan=False,
):
    \"\"\"Run one paper-trading cycle: discover, manage exits, open new positions.\"\"\"
    import os
    state = load_paper_state(state_path)
    meta = state.get("meta", {})
    current_wallet = meta.get("current_wallet", float(os.getenv("PAPER_MAX_OPEN_POSITIONS", 5)) * 1.0)
    
    if current_wallet >= 10.0:
        stake_usd = float(int(current_wallet / 5.0))
        
    paper_max_open_positions = int(current_wallet // stake_usd)

    return _run_paper_trading_cycle_impl(

        min_price=min_price,
        max_price=max_price,
        stake_usd=stake_usd,
        state_path=state_path,
        force_aggressive_scan=force_aggressive_scan,
        perf_counter_fn=time.perf_counter,
        now_utc_fn=lambda: datetime.now(timezone.utc),
        elapsed_ms_fn=_elapsed_ms,
        load_paper_state_fn=load_paper_state,
        run_discovery_cycle_fn=run_discovery_cycle,
        prefetch_forecasts_fn=_prefetch_forecasts,
        ensure_take_profit_target_fn=_ensure_take_profit_target,
        forecast_still_valid_fn=_forecast_still_valid,
        position_confidence_score_fn=_position_confidence_score,
        update_paper_position_fn=update_paper_position,
        close_paper_position_fn=close_paper_position,
        build_open_position_inventory_fn=_build_open_position_inventory,
        build_entry_candidates_fn=_build_entry_candidates,
        append_opened_positions_from_candidates_fn=_append_opened_positions_from_candidates,
        build_city_coverage_metrics_fn=_build_city_coverage_metrics,
        build_cycle_acceptance_metrics_fn=_build_cycle_acceptance_metrics,
        build_rolling_acceptance_metrics_fn=_build_rolling_acceptance_metrics,
        build_rolling_city_coverage_metrics_fn=_build_rolling_city_coverage_metrics,
        build_cycle_journal_entry_fn=_build_cycle_journal_entry,
        save_paper_state_fn=save_paper_state,
        discovery_enable_auto_aggressive_scan=DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN,
        discovery_auto_aggressive_after_empty_cycles=DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES,
        paper_position_forecast_prefetch_min_keys=PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS,
        paper_position_forecast_prefetch_max_workers=PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS,
        paper_entry_min_price=PAPER_ENTRY_MIN_PRICE,
        paper_entry_max_price=PAPER_ENTRY_MAX_PRICE,
        paper_max_open_positions=paper_max_open_positions,
        paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
        paper_journal_max_entries=PAPER_JOURNAL_MAX_ENTRIES,
    )"""

code = code.replace(old_cycle, new_cycle)

with open("market_discovery.py", "w") as f:
    f.write(code)
print("cash logic patched")
