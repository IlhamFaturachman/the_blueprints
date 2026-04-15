# Operator Tuning Runbook

Date: 2026-04-14
Scope: discovery + paper cycle runtime tuning using existing telemetry output.

## Goal

Tune runtime knobs without changing strategy intent:
1. Keep behavior deterministic and stable.
2. Improve opportunity quality and discovery throughput.
3. Use cycle/report telemetry as the primary feedback loop.

## Safe Baseline

Use this baseline first:
1. DAILY_RESOLVE_ONLY=true
2. DAILY_MIN_HOURS_TO_RESOLVE=0
3. PAPER_ENTRY_MIN_PRICE=0.20
4. PAPER_ENTRY_MAX_PRICE=0.30
5. PAPER_MAX_OPEN_POSITIONS=5
6. PAPER_MAX_OPEN_PER_CITY=1
7. PAPER_MIN_CITY_DIVERSITY=5
8. DISCOVERY_FORECAST_PREFETCH_MIN_KEYS=4
9. DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS=4
10. PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS=3
11. PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS=4

## How To Observe

Use these commands in order:
1. ./run_paper_5usd.sh --paper --aggresive
2. ./run_paper_5usd.sh --paper-report-json

Repeat for multiple cycles, then inspect:
1. last_cycle_performance
2. rolling_acceptance_metrics
3. rolling_city_coverage_metrics
4. recent_journal

## Key Signals And Actions

### A) Discovery Latency

Watch:
1. discovery_ms
2. forecast_prefetch stats (eligible, attempted, successful, failed, workers, skipped)

Action:
1. If discovery_ms high and prefetch skipped often, lower DISCOVERY_FORECAST_PREFETCH_MIN_KEYS by 1.
2. If attempted is high but successful is low, avoid increasing workers and inspect upstream API reliability first.
3. If attempted/successful ratio is healthy and discovery_ms remains high, increase DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS gradually (max +1 step each change).

### B) Position Validation Cost

Watch:
1. position_prefetch_ms
2. position_management_ms
3. position_forecast_prefetch stats

Action:
1. If open positions are many and position_management_ms grows, reduce PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS.
2. If workers are high but successful rate does not improve, roll workers back.

### C) Entry Quality

Watch:
1. opportunity_to_open_rate
2. candidate_to_open_rate
3. close_win_rate
4. closed_realized_pnl_total_usd

Action:
1. If opportunities exist but opens stay near zero, verify entry bounds are not too narrow.
2. If close_win_rate weak over many cycles, do not widen entry band immediately; first verify forecast/data quality and city concentration.

### D) City Diversification

Watch:
1. unique_opportunity_cities
2. unique_candidate_cities
3. unique_opened_cities
4. cycles_below_target

Action:
1. If cycles_below_target remains high for long windows, keep PAPER_MAX_OPEN_PER_CITY=1 and focus on discovery breadth.
2. Do not relax city cap before verifying sustained quality in closed outcomes.

## Change Discipline

Apply one change at a time:
1. Change only one knob.
2. Run at least 5 to 10 cycles.
3. Compare against previous window.
4. Keep the change only if both stability and quality improve.

## Rollback Rules

Revert recent tuning changes immediately when:
1. discovery_ms regresses materially across multiple cycles.
2. close_win_rate drops and closed PnL weakens for the same observation window.
3. forecast prefetch failed count spikes persistently.

## Notes

1. Daily mode is strict by design and may produce zero-opportunity cycles.
2. Zero-opportunity cycles are valid outcomes when market set does not satisfy constraints.
3. Prefer consistency over aggressive parameter swings.
