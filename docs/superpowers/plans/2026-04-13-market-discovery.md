# Market Discovery and Hybrid Paper Trading - Implementation Plan

**Date:** 2026-04-14
**Status:** In execution and stabilized
**Baseline owner:** Claude handoff followed by completion pass

---

## Objective

Finalize the current baseline without scope drift:
1. Keep Claude runtime behavior as source of truth.
2. Ensure code, tests, docs, and env contract stay consistent.
3. Close remaining handoff tasks so the repo is ready for continued iteration.

---

## Current Baseline Snapshot

Implemented in [market_discovery.py](market_discovery.py):
1. Weather events discovery using events pagination endpoint.
2. Temperature market parsing with `clobTokenIds` support.
3. Forecast enrichment and edge scoring.
4. Opportunity filtering with tunable thresholds.
5. Hybrid paper trading cycle with:
   - strict entry default 0.20-0.30,
   - take-profit band 0.50-0.60,
   - stop loss,
   - confidence-gated hold-to-resolve.
6. Diagnostics mode with drop-off reporting.
7. Aggressive scan controls for empty discovery streaks.

Automated validation baseline:
1. Full test suite currently passing.
2. Tests include fetch, parse, forecast, edge, filter, hybrid exits, paper cycle, and main CLI orchestration.

---

## Completion Tasks

- [x] Keep runtime logic aligned with Claude baseline.
- [x] Verify tests pass on current baseline.
- [x] Keep hybrid strategy active and unchanged in intent.
- [x] Keep discovery diagnostics and aggressive scan behavior intact.
- [x] Update design spec to match implemented behavior.
- [x] Perform final smoke checks for live CLI network modes (automated CLI tests passed; live diagnose endpoint run attempted and timed out on network response).
- [x] Add final release note style summary in docs.

---

## Verification Checklist

1. Unit and integration tests:
   - [tests/test_fetch_markets.py](tests/test_fetch_markets.py)
   - [tests/test_parse_market.py](tests/test_parse_market.py)
   - [tests/test_fetch_forecast.py](tests/test_fetch_forecast.py)
   - [tests/test_calculate_edge.py](tests/test_calculate_edge.py)
   - [tests/test_filter.py](tests/test_filter.py)
   - [tests/test_hybrid_exit.py](tests/test_hybrid_exit.py)
   - [tests/test_paper_cycle.py](tests/test_paper_cycle.py)
   - [tests/test_main.py](tests/test_main.py)

2. Runtime behavior checks:
   - `--inspect`
   - default discovery run
   - `--diagnose`
   - `--paper`
   - `--paper-loop`

---

## Out of Scope for This Plan

1. Live order placement via CLOB.
2. Production risk engine and fee-aware execution.
3. AI inference in trading decisions.

These remain future phases after completion of the current baseline.

---

## Release Notes (2026-04-14)

1. Locked baseline to Claude handoff behavior without adding new runtime scope.
2. Realigned design and plan documents to actual implementation (events discovery, diagnostics, hybrid paper flow).
3. Added future AI-agent config placeholders in [.env.example](.env.example) without runtime coupling.
4. Revalidated baseline with full test pass (`72 passed`).
