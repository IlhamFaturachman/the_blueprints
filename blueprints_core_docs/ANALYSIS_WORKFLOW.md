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

### Step 5: Implement, Test, Deploy — with Triple Verification

Only after Steps 1-4 are complete:

1. **Implement** the changes locally
2. **Run full test suite** — ALL tests must pass (currently 162)
3. **Triple Verification** (MANDATORY before commit):

   **Verification 1: API/Library Correctness**
   - Verify every external method call matches the actual library signature
   - Run programmatic checks: `inspect.signature()` for every method used
   - Confirm parameter names, types, and return values match the plan
   - Example: `post_order(order, orderType, post_only)` — verify `post_only` param exists
   
   **Verification 2: Code Location & Integration**
   - Verify every file path, function name, and line number is correct
   - Confirm no naming conflicts with existing code
   - Confirm all imports resolve correctly
   - Confirm new code doesn't shadow existing variables
   
   **Verification 3: Lifecycle Simulation**
   - Simulate the complete flow with real data types (not just mental model)
   - Test edge cases with actual values: min values, max values, zero, None
   - Verify type conversions (string→float, string→int) don't fail
   - Test the "spread = 1 tick" and "spread = 0" edge cases
   - Confirm the flow works for ALL market types (above/below, exact, lowest)

4. **Commit with descriptive message** — include what changed, why, and what was verified
5. **Push to GitHub**
6. **Deploy to VPS** — stop bot → git pull → run tests → restart
7. **Monitor first cycle** — verify the fix works in production
8. **Check dashboard** — verify no regressions

---

## Red Lines (Never Do These)

- **NEVER read, view, open, cat, or access .env files** — whether on local machine or on the VPS server. The .env contains private keys, API secrets, and wallet credentials. DO NOT TOUCH OR SEE .env under any circumstances. If you need to know what variables exist, ask the user or check .env.example.
- **Never skip Step 3** (side effect check) — this is where most bugs are caught
- **Never deploy without running tests** — even for "trivial" changes
- **Never modify production code directly on VPS** — always edit locally, test, commit, push, pull
- **Never bypass the liquidity check for live trading** — paper trading only
- **Never remove safety guards** (circuit breaker, whiplash shield, consensus gate) without explicit approval
- **Never commit .env files** — they contain secrets and are gitignored for a reason
- **Never implement code calling external libraries without running `inspect.signature()` on every method used** — documentation and memory can be wrong, only the actual installed code is truth

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

---

## Example 3: Execution Bridge Plan Review (22 April 2026) — Lesson in Triple Verification

### What Happened

We wrote a 1,022-line implementation plan for the execution bridge (live trading with maker orders). The plan looked complete — 8 phases, 18 critical questions, full side effect analysis. But a deep re-check against the ACTUAL library code found **4 critical bugs** that would have caused runtime crashes:

| Bug | What the Plan Said | What the Library Actually Does | Would Have Caused |
|---|---|---|---|
| B1 | Use `create_and_post_order()` for maker orders | `create_and_post_order` hardcodes `post_only=False` — cannot do maker | Bot pays 5% taker fee on EVERY entry (defeats entire purpose) |
| B2 | Use `MarketOrderArgs(size=shares)` for taker sell | Field is `amount`, not `size`. BUY=dollars, SELL=shares | `TypeError` crash on every urgent exit |
| B3 | `create_market_order()` creates and posts | It only signs — does NOT post. Must call `post_order()` separately | Urgent exits silently fail — positions stuck forever |
| B4 | ClobClient constructor handles auth | Without `creds=` parameter, client is L1-only. All trading ops throw `AssertionError` | Every single API call crashes |

### What Triple Verification Caught

**Verification 1 (API Correctness):** Running `inspect.signature()` on every method revealed that `create_and_post_order` has no `post_only` parameter, `MarketOrderArgs` uses `amount` not `size`, and `create_market_order` returns a signed order (not a posted one).

**Verification 2 (Code Location):** Confirmed all file paths and function names were correct. No issues here.

**Verification 3 (Lifecycle Simulation):** Creating actual `OrderArgs` and `MarketOrderArgs` objects with real values confirmed the correct field names and types. Also caught that `tick_size` must be a string literal, not a float.

### The Fix

Updated the plan (v1.1) with:
- Two-step flow for ALL orders: `create_order/create_market_order` → `post_order`
- Correct field names: `amount` for `MarketOrderArgs`, `size` for `OrderArgs`
- L2 auth: `create_or_derive_api_creds()` + `creds=` in constructor
- Type corrections: `tick_size` as string, orderbook fields cast from string

### Lesson

**Never trust a plan based on documentation or memory alone. Always verify against the ACTUAL installed library code.** Run `inspect.signature()` on every method you plan to call. Create actual objects with real values to catch type mismatches. A plan that "looks right" can have critical bugs that only surface when you check the real API signatures.

**The Triple Verification step exists because:**
- Step 3 (side effects) checks "will it break existing code?" — but doesn't verify the NEW code is correct
- Step 3b (critical thinking) checks "is the logic complete?" — but doesn't verify API signatures
- Triple Verification checks "does the code I'm about to write actually match the library I'm calling?" — this is where B1-B4 were caught

---

## Red Line Addition

**Never implement code that calls external libraries without first running `inspect.signature()` on every method used.** Documentation can be wrong. Memory can be wrong. Only the actual installed code is the truth.
