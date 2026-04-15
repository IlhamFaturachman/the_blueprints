# Market Discovery and Hybrid Paper Trading - Design Spec
**Date:** 2026-04-14
**Status:** Implemented Baseline (Claude handoff)
**Scope:** [market_discovery.py](market_discovery.py) with discovery, diagnostics, and paper hybrid loop

---

## Overview

Current baseline combines two layers in one file:
1. Discovery engine for temperature-weather markets (fetch -> parse -> forecast -> edge -> filter).
2. Paper-trading engine with hybrid exits (+100% target, stop loss, confidence-gated hold).

Primary objective remains identifying underpriced YES contracts using forecast-based scoring.

---

## Runtime Modes

The CLI in [market_discovery.py](market_discovery.py) supports:

1. `--inspect`: print raw weather-event structure and exit.
2. Default mode: run discovery and print opportunities plus run summary.
3. `--diagnose`: print discovery drop-off diagnostics per stage.
4. `--paper`: run one paper trading cycle.
5. `--paper-loop`: run periodic paper trading loop.
6. `--aggressive`: force aggressive discovery scan behavior for that run.
7. Compatibility alias: `--aggresive` is accepted and treated as `--aggressive`.
8. `--paper-report`: print persisted paper state, cycle journal, and rolling acceptance metrics.
9. `--paper-report-json` (or `--paper-report --json`): print the same paper report payload in JSON for machine-readable monitoring.

---

## Discovery Architecture

Pipeline executed by `run_discovery_cycle()`:

1. `fetch_markets()`
2. `parse_market()`
3. optional forecast prefetch warm-up for larger city/date batches
4. `fetch_forecast()`
5. `calculate_edge()`
6. `filter_opportunities()`
7. `print_opportunities()` and `print_summary()`

### 1) `fetch_markets(inspect=False, aggressive_scan=False)`

Current data source:
- Endpoint: `https://gamma-api.polymarket.com/events/pagination`
- Params include: `tag_slug=weather`, `active=true`, `closed=false`, `archived=false`

Behavior:
1. Flattens event payloads into market list.
2. Applies temperature candidate heuristic.
3. Scans additional pages using offset when needed.
4. Returns all scanned markets as fallback when no direct candidate passes.

### Daily Resolve Mode (UTC)

When `DAILY_RESOLVE_ONLY=true`, discovery keeps only temperature markets that:
1. Resolve on the same UTC calendar date as the current run time.
2. Have not resolved yet (`hours_until_resolve > 0`).

`DAILY_MIN_HOURS_TO_RESOLVE` is optional tightening control; default is `0` (no extra minimum-hours gate).

Default behavior remains unchanged because daily mode is opt-in (default `false`).

### 2) `parse_market(raw)`

Parses market dict into a structured contract:
- `city`, `date`, `end_date`
- `threshold`, `unit`, `direction`
- `yes_price`, `token_id`, `hours_until_resolve`

Notes:
1. Uses combined text from question/title/slug/description/tags.
2. Direction now supports `above`, `below`, and `exact` with dedicated keyword sets.
3. Token extraction uses `clobTokenIds` first-entry as YES token.
4. Markets outside forecast window are skipped.

### 3) `fetch_forecast(city, date)`

Uses Open-Meteo daily max temperature forecast for configured cities.

For larger parsed batches, discovery now does guarded parallel prefetch to warm cache:
1. Prefetch is only attempted when unique city/date keys pass configured minimum threshold.
2. Prefetch stores successful lookups only.
3. Failures remain uncached so retry behavior on later markets is preserved.

### 4) `calculate_edge(market, forecast_temp)`

Rule-based V1 scoring:
1. Converts units when market is in F.
2. `above`: pass when `forecast >= threshold`.
3. `below`: pass when `forecast < threshold`.
4. `exact`: intentionally skipped by discovery orchestrator.

Output includes `model_prob`, `edge`, and forecast trace values.

### 5) `filter_opportunities(markets, max_yes_price, min_model_prob, min_edge)`

Runtime-tunable gate with defaults:
1. `yes_price < 0.35`
2. `model_prob >= 0.70`
3. `edge >= 0.35`

---

## Hybrid Paper Trading Architecture

Main orchestration: `run_paper_trading_cycle()`

Flow:
1. Run discovery.
2. Optionally prefetch open-position forecast keys when eligible batch size is large enough.
3. Update existing open positions with latest market prices.
3. Apply `evaluate_hybrid_exit()` decision.
4. Open new positions within configured entry bounds.
5. Persist state to JSON.

### Entry Defaults

1. `PAPER_ENTRY_MIN_PRICE=0.20`
2. `PAPER_ENTRY_MAX_PRICE=0.30`
3. Position sizing via `PAPER_STAKE_USD`

### City Diversification Entry Policy

Entry selection now applies city-level concentration control before opening new positions:

1. Best-per-city candidate selection:
   - If multiple entry-eligible opportunities exist for the same city in one cycle, only one is allowed.
   - Ranking order is deterministic: entry confidence descending, edge descending, then cheaper YES price.
2. Max one open position per city:
   - New entries are blocked when a city already has an open position.
   - Current policy is no-swap: existing open positions are not replaced mid-cycle by newer same-city setups.
3. Token dedupe remains enforced independently (`token_id` uniqueness still required).
4. Daily city coverage target is soft:
   - `PAPER_MIN_CITY_DIVERSITY` (default `5`) is monitored as a warning target.
   - Trading is not blocked when discovery has fewer than the target number of cities.
   - Cycle and rolling city coverage metrics are persisted for review.

### Exit Rules (`evaluate_hybrid_exit`)

Priority order:
1. Stop loss when price <= stop loss threshold.
2. Take profit when YES reaches configured multiplier target from entry (default 2.0x).
3. Late-window decision:
   - Sell if forecast invalid.
   - Hold to resolve only if confidence >= configured minimum.
4. Otherwise hold.

### Confidence Gate

Confidence is computed by `_position_confidence_score()` from:
1. Entry model probability.
2. Current edge quality.
3. Price location component.

Hold-to-resolve requires passing `HYBRID_MIN_CONFIDENCE_TO_HOLD`.

### Paper State Journal and Acceptance Metrics

The paper state now tracks operational observability data:

1. `cycle_journal` list in state file, appended each cycle with:
   - timestamp,
   - opened/closed/open-position counts,
   - entry bucket counts,
   - close reason counts,
   - cycle acceptance rates and cycle closed PnL.
2. Rolling acceptance metrics in state `meta.acceptance_metrics_rolling`:
   - cumulative opportunities, candidates, opens, closes,
   - cumulative bucket totals,
   - rolling opportunity-to-open, candidate-to-open, and close-win rates,
   - cumulative closed realized PnL.
3. Cycle summary print includes both cycle-level and rolling rates.
4. Paper report can now be emitted as JSON (`--paper-report-json`) for external log/monitoring ingestion.
5. Paper report includes journal retention summary (recent shown, older hidden, capacity utilization, warning threshold flag).
6. Paper report includes journal age-bucket breakdown (`<24h`, `24-72h`, `>72h`, `unknown`) and rotation summary (policy, capacity status, estimated rotated-out entries, coverage hours).
7. Paper report includes anomaly counters for zero-opportunity and reject-dominant streaks with alert flags.

---

## Diagnostics

`build_discovery_diagnostics()` and `print_discovery_diagnostics()` provide:
1. Raw market counts.
2. Candidate stage drop-off.
3. Parsed/enriched/opportunity counts.
4. Rejection reason samples.
5. Daily-mode skip counters (`daily_date_mismatch`, `daily_min_hours_not_met`) when enabled.

This is the primary tool for debugging `0 opportunity` outcomes.

---

## Configuration Surface

Documented in [.env.example](.env.example):

1. Discovery controls (`DISCOVERY_*`).
2. Daily resolve controls (`DAILY_RESOLVE_ONLY`, `DAILY_MIN_HOURS_TO_RESOLVE`).
3. Discovery forecast prefetch controls (`DISCOVERY_FORECAST_PREFETCH_MIN_KEYS`, `DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS`).
4. Open-position forecast prefetch controls (`PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS`, `PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS`).
5. Strategy thresholds (`STRATEGY_*`).
6. Paper controls (`PAPER_*`).
7. City diversification controls (`PAPER_MAX_OPEN_PER_CITY`, `PAPER_MIN_CITY_DIVERSITY`).
8. Hybrid controls (`HYBRID_*`).
9. Paper journal retention (`PAPER_JOURNAL_MAX_ENTRIES`).
10. Paper report retention warning threshold (`PAPER_REPORT_RETENTION_WARN_THRESHOLD`).
11. Paper report anomaly alert tuning (`PAPER_REPORT_ANOMALY_STREAK_ALERT`, `PAPER_REPORT_REJECT_DOMINANT_RATIO`).
12. Future AI placeholders (`AI_AGENT_*`).

---

## File and Tests

Core runtime file:
1. [market_discovery.py](market_discovery.py)

Key tests:
1. [tests/test_fetch_markets.py](tests/test_fetch_markets.py)
2. [tests/test_parse_market.py](tests/test_parse_market.py)
3. [tests/test_fetch_forecast.py](tests/test_fetch_forecast.py)
4. [tests/test_calculate_edge.py](tests/test_calculate_edge.py)
5. [tests/test_filter.py](tests/test_filter.py)
6. [tests/test_hybrid_exit.py](tests/test_hybrid_exit.py)
7. [tests/test_paper_cycle.py](tests/test_paper_cycle.py)
8. [tests/test_main.py](tests/test_main.py)

---

## Out of Scope

Still out of scope for this baseline:
1. Live CLOB order execution.
2. Exchange auth/session management for production trading.
3. Fee/slippage production risk engine.
4. AI inference in trading decisions (only placeholders currently).
