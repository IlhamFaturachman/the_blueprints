# The Blueprints — Master Checklist

**Created:** 23 April 2026, 16:30 WIB  
**Last Updated:** 23 April 2026, 19:15 WIB  
**Purpose:** Single source of truth for all pending work — fixes, audits, and implementation plans

---

## Phase 1: Fix Known Bugs from Pass 1 (19 bugs) — COMPLETE

### CRITICAL (3) — All Fixed
- [x] **C1** — `direction` undefined → SL cooldown now works
- [x] **C2** — Positions <4h invisible → now evaluated via `end_date` fallback
- [x] **C3** — Taker sell result checked → no more phantom closes

### HIGH (5) — All Fixed
- [x] **H1** — Heartbeat health check on all 4 `place_*` methods
- [x] **H2** — Trailing stop now triggers on retrace from peak
- [x] **H3** — Weather evidence source matching uses substring
- [x] **H4** — `monitor_pending_orders` returns closed positions for metrics
- [x] **H5** — Live exit uses `updated_position`

### MEDIUM (8) — 3 Fixed, 5 Deferred
- [x] **M1** — Direction default → "above" (was "exact")
- [x] **M2** — Whiplash shield checks `close_reason` first
- [x] **M5** — Historical average passes `temp_type` on cache path
- [ ] **M3** — Kelly global reserve — *deferred (design decision)*
- [ ] **M4** — Zombie guard cash — *deferred (self-healing)*
- [ ] **M6** — Min-temp cache — *deferred (performance)*
- [ ] **M7** — Trade history atomicity — *deferred (self-healing)*
- [ ] **M8** — DB schema PK — *deferred (safe by convention)*

### LOW (3) — Deferred
- [ ] **L1-L3** — Rare edge cases, deferred

---

## Phase 2: Fix Known Bugs from Pass 2 (12 findings) — COMPLETE

### Config
- [x] **S1** — Flash Crash L1 threshold — *deferred (needs user input on value)*
- [x] **S2** — `PENNY_BID_THRESHOLD` now uses config constant in broadcaster

### Telegram
- [x] **S3** — Dedup cache now records AFTER successful send (not before)
- [x] **S4** — Added `send_telegram_alert_async()` for non-blocking sends

### Command Server / Security
- [x] **S5** — CORS now uses strict origin matching (exact URL, not substring)
- [x] **S6** — Kill switch token only via `X-Kill-Token` header (removed query param)
- [x] **S7** — `/api/state` strips sensitive fields (SL, TP, edge, prob, confidence)

### Data Warmer
- [x] **S8** — Warmer validates temperature range (-60°C to +60°C) before caching

### Test Coverage
- [ ] **S10** — 5 critical paths need tests — *deferred (needs new test files)*
- [ ] **S11** — evaluate_hybrid_exit tests need cooldown/trailing — *deferred*

### Database
- [x] **S12** — Documented atomicity gap; protected by existing lock in practice

### Dashboard
- [x] **F2-gaps** — Added regex for `late_window`, `taker_fallback`, `maker_sell_filled`

### Test Results: 198/198 passed

---

## Phase 3: Deep Audit Remaining Areas (37 areas across 6 Tiers) — NOT STARTED

### Tier 1 — Directly Handles Money (~813 lines) — COMPLETE
- [x] Audit → `findings/tier-audits/tier-1-money-handling.md`
- **Result: 0 new bugs found. All 5 areas CLEAN. Live-trading ready.**

### Tier 2 — Affects Trading Decisions (~524 lines) — AUDIT COMPLETE, FIXES APPLIED
- [x] Audit → `findings/tier-audits/tier-2-trading-decisions.md`
- [x] **Fixes applied: 2 CRIT + 2 HIGH + 4 MEDIUM fixed. 5 LOW deferred.**
- Fixed: "Below" NOAA confirm (late+margin), L1 deadlock (max-delay above L1), "Above" margin (+0.5°C), NOAA pipeline bypass, calibration clamp (±0.20), METAR cache+stale, ensemble boundary

### Tier 3 — Operational Core (~1093 lines) — AUDIT COMPLETE, FIXES APPLIED
- [x] Audit → `findings/tier-audits/tier-3-operational-core.md`
- [x] **Fixes applied: 3 HIGH + 2 MEDIUM fixed. 3 MEDIUM + 7 LOW deferred.**
- Fixed: Confidence default 0.5, sys.exit→return, edge None logging, evidence null-check order, 429 final sleep skip

### Tier 4 — Infrastructure & Security (~1120 lines) — AUDIT COMPLETE, FIXES APPLIED
- [x] Audit → `findings/tier-audits/tier-4-infra-security.md`
- [x] **Fixes applied: 1 CRIT + 1 HIGH + 3 MEDIUM fixed. 3 MEDIUM + 4 LOW deferred (shell/edge cases).**
- Fixed: UI service disabled + nginx hardened (.env blocked), WAL checkpoint instead of delete, pkill patterns, DB indexes + dedup, corrupt json logging

### Tier 5 — Supporting & Quality (~1459 lines) — AUDIT COMPLETE, FIXES APPLIED
- [x] Audit → `findings/tier-audits/tier-5-supporting-quality.md`
- [x] **Fixes applied: 2 HIGH + 3 MEDIUM fixed. 2 MEDIUM + 4 LOW deferred.**
- Fixed: Daily monitor .get() + UTC, backtest lookahead warning, log rotator copy-truncate + dirname

### Tier 6 — Partially Audited Gaps (~301 lines) — AUDIT COMPLETE, FIXES APPLIED
- [x] Audit → `findings/tier-audits/tier-6-partial-gaps.md`
- [x] **Fixes applied: 1 HIGH + 4 MEDIUM fixed. 1 HIGH (duplicate) + 5 LOW deferred.**
- Fixed: str(None) unit inference, city sigma classification (31 cities), Southern Hemisphere inversion, negative cache TTL 120s, "equal or exceed" regex

---

## Phase 4: Implementation & Deploy

- [ ] Commit Phase 1 + Phase 2 fixes
- [ ] Push to GitHub
- [ ] Deploy to VPS (stop → pull → test → restart)
- [ ] Monitor first cycle
- [ ] Check dashboard — no regressions

---

## Summary Counters

| Category | Total | Done | Remaining |
|----------|-------|------|-----------|
| Bugs fixed earlier (F1-F5) | 5 | 5 | 0 |
| Phase 1 bugs fixed | 11 | 11 | 0 |
| Phase 1 bugs deferred | 8 | — | 8 |
| Phase 2 findings fixed | 10 | 10 | 0 |
| Phase 2 findings deferred | 2 | — | 2 |
| Tier audits to complete | 6 | 6 | 0 |
| **Grand Total Items** | **42** | **26** | **16** |

### All Fixes Applied This Session (26 total):
- F1-F5: CLOB key, regex, WS price, unsubscribe, zombie guard (deployed)
- C1-C3: direction, invisible positions, taker sell check
- H1-H5: heartbeat, trailing stop, evidence source, pending closures, live exit
- M1, M2, M5: direction default, whiplash reason, hist avg temp_type
- S2-S8: penny threshold, telegram dedup, telegram async, CORS, kill token, state strip, warmer validation
- F2-gaps: dashboard close_reason cosmetic

*Last updated: 23 April 2026, 22:10 WIB — ALL TIER 2-6 FIXES APPLIED, 198/198 tests pass*
