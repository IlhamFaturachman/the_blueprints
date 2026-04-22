# The Blueprints — Standard Analysis & Implementation Workflow

**Created:** 22 April 2026
**Purpose:** Ensure every code change is thoroughly analyzed, safe, and production-ready before implementation.

---

## The 5-Step Workflow

Every code change — whether a bug fix, feature, or optimization — MUST follow these 5 steps in order. No shortcuts.

### Step 1: Deep Analyze the Problem

Before proposing any solution:

- **Identify the exact symptoms** — what is happening, when, how often
- **Trace the full code path** — from entry point to the symptom, reading every function involved
- **Check all related components** — state management, database, WebSocket, risk engine, Kelly, Telegram, dashboard
- **Identify ALL root causes** — not just the obvious one. There may be multiple contributing factors
- **Check the logs** — what does the bot actually output? Are there silent failures?
- **Quantify the impact** — how much time/money/opportunity is lost?

**Output:** A clear problem statement with root cause(s) identified.

### Step 2: Propose Changes

For each proposed change:

- **Describe what changes** — exact file, exact line, exact code
- **Describe why it fixes the problem** — the causal link between the change and the fix
- **Estimate the impact** — how much improvement (time saved, opportunities gained, etc.)
- **List alternatives considered** — and why this approach was chosen

**Output:** A concrete implementation plan with exact code changes.

### Step 3: Deep Re-Check for Side Effects

Before implementing, verify the proposed changes will NOT:

- **Break existing logic** — trace every consumer of the changed function/variable
- **Cause race conditions** — check thread safety, concurrent access, shared state
- **Break tests** — check which tests exercise the changed code paths
- **Affect other components** — state persistence, WebSocket, risk engine, Kelly, database
- **Introduce new failure modes** — what happens if the new code fails? Is there a fallback?
- **Change behavior for existing markets** — do above/below markets still work the same?

**Output:** A safety assessment for each change (SAFE / LOW RISK / HIGH RISK).

### Step 3b: Critical Self-Questioning (Think Like the User)

After proposing changes, STOP and question your own proposals critically:

- **"What if the value I'm checking doesn't exist in the dict?"** — verify `.get()` defaults
- **"What if this variable is used LATER in the function but I only defined it inside an if-block?"** — trace ALL references to every variable you touch (the `regime_class` UnboundLocalError lesson)
- **"Does the function I'm modifying get called from OTHER places I haven't checked?"** — grep for ALL call sites, not just the one you're fixing
- **"Is my threshold/multiplier actually correct for the edge cases?"** — test with real numbers (e.g., prob=0.05, prob=0.40, entry=$0.01, entry=$0.50)
- **"Would this change make the bot behave unrealistically for paper trading?"** — paper trading should simulate real market conditions
- **"If I add a new code path for exact markets, does the EXIT logic also handle exact markets?"** — entry and exit must be consistent (the forecast_still_valid lesson)
- **"Am I only fixing the symptom or the root cause?"** — trace the full chain, not just the first error

**This step catches bugs that Step 3 misses.** Step 3 checks "will it break something?" Step 3b checks "is my fix actually correct and complete?"

**Output:** A list of critical questions asked and answered for each change.

### Step 4: Verify Live-Trading Compatibility

Every change must be evaluated against the question: **"Would this work correctly in live trading with real money?"**

- **Does it preserve real-life market simulation?** — paper trading should behave as close to live as possible
- **Does it need to be changed before going live?** — if yes, document it clearly
- **Does it affect order execution?** — entry prices, slippage, fees, fill rates
- **Does it affect risk management?** — stop loss, take profit, circuit breaker, Kelly sizing
- **Is it config-gated?** — can it be disabled instantly via .env if something goes wrong?

**Output:** A live-trading compatibility statement for each change.

### Step 5: Implement, Test, Deploy

Only after Steps 1-4 are complete:

1. **Implement** the changes locally
2. **Run full test suite** — ALL tests must pass (currently 162)
3. **Commit with descriptive message** — include what changed, why, and what was verified
4. **Push to GitHub**
5. **Deploy to VPS** — stop bot → git pull → run tests → restart
6. **Monitor first cycle** — verify the fix works in production
7. **Check dashboard** — verify no regressions

---

## Red Lines (Never Do These)

- **Never skip Step 3** (side effect check) — this is where most bugs are caught
- **Never deploy without running tests** — even for "trivial" changes
- **Never modify production code directly on VPS** — always edit locally, test, commit, push, pull
- **Never bypass the liquidity check for live trading** — paper trading only
- **Never remove safety guards** (circuit breaker, whiplash shield, consensus gate) without explicit approval
- **Never commit .env files** — they contain secrets and are gitignored for a reason

---

## Checklist Template

For every change, fill in this checklist before implementing:

```
[ ] Problem clearly identified with root cause
[ ] Exact code changes documented
[ ] Side effects checked against: state, DB, WS, risk, Kelly, Telegram, dashboard
[ ] Thread safety verified (if touching shared state)
[ ] All existing tests still pass
[ ] New tests added (if new behavior introduced)
[ ] Live-trading compatible (or clearly documented as paper-only)
[ ] Config-gated (if risky, can be disabled via .env)
[ ] Commit message describes what, why, and verification status
[ ] First production cycle monitored after deployment
```

---

## Example 1: METAR Cache Fix (22 April 2026)

1. **Problem:** 368 NOAA METAR API calls per cycle for 125 markets. Same ICAO station called 10+ times. Each call takes 1-2 seconds. Total: ~400 seconds wasted.

2. **Proposed Change:** Add in-memory cache with 5-minute TTL and thread lock to `fetch_noaa_metar()`.

3. **Side Effect Check:**
   - NOAA override logic uses METAR data — 5-min cache vs 30-60 min METAR update frequency = negligible staleness
   - Override has 5°C safety margin on contradictions — temperature doesn't change 5°C in 5 minutes
   - Thread safety: used `threading.Lock` (same pattern as existing `_NEGATIVE_CACHE_LOCK`)
   - No impact on state, DB, WS, risk, Kelly, Telegram, or dashboard

4. **Live-Trading Compatibility:** Safe for live trading. Even recommended — reduces API load and avoids rate limiting from aviationweather.gov.

5. **Implementation:** 15 lines added to `forecasting.py`. 162/162 tests pass. Deployed to VPS. First cycle verified: METAR calls reduced from 368 to ~30.

---

## Example 2: Exact-Bracket Exit System Fix (22 April 2026) — Lesson in Step 3b

### What Happened (The Mistake Chain)

1. We added exact-bracket market support (entry path). The bot found 36 opportunities. 
2. But the EXIT path was never adapted for exact markets:
   - `forecast_still_valid` used 0.50 threshold → exact markets (prob 0.10-0.38) always returned 0.0 → immediate closure
   - Confidence formula used raw `base_prob` → exact markets always scored 0.14-0.26 → guaranteed late-window exit
   - Take-profit at 2x → $0.05 entry sold at $0.10, capturing only 5% of max profit
   - `position_to_market` missing `temp_type` → NOAA override applied wrong logic to min-temp positions

3. We also introduced an `UnboundLocalError` by moving `regime_class` inside an if-block without checking if it was used later.

### What Step 3b Would Have Caught

If we had asked these questions BEFORE deploying the exact-bracket entry support:

- **"Does the EXIT logic also handle exact markets?"** → Would have found the `forecast_still_valid` threshold incompatibility
- **"Is my threshold actually correct for exact markets?"** → Would have found the confidence formula producing 0.14-0.26
- **"If I add a new code path, is every variable still defined?"** → Would have caught the `regime_class` UnboundLocalError
- **"Does `position_to_market` pass all the fields that downstream functions need?"** → Would have found the missing `temp_type`

### The Fix

4 targeted changes, all scoped to exact markets only, zero impact on above/below:
- Fix A: `forecast_still_valid` — exact markets use 0.05/0.20 thresholds
- Fix B: Take-profit — exact markets use 8x multiplier (config-gated)
- Fix C: Confidence — normalize base_prob for exact markets (0.05-0.40 → 0.0-1.0)
- Fix D: `position_to_market` — add `temp_type` field

### Lesson

**When adding support for a new market type, trace the ENTIRE lifecycle: discovery → parsing → forecasting → edge calculation → filtering → entry selection → position build → monitoring → exit → settlement.** Fixing only the entry path without adapting the exit path creates a trap: positions open successfully but then get immediately destroyed by incompatible exit logic.
