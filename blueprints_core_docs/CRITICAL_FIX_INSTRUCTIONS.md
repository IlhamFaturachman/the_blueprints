# 🚨 CRITICAL FIX: Bot Stuck at 373+ Empty Cycles — Root Cause & Fix Instructions

**Date:** 2026-04-17 08:30 WIB  
**Author:** Antigravity Deep Audit  
**Status:** Bot RUNNING but DEAD — zero trades, zero PnL, 373 empty cycles  
**Server:** `root@103.253.244.158` `/opt/the_blueprints`  
**SSH:** `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158`

---

## 🔬 ROOT CAUSE ANALYSIS (Verified with Live Data)

### The Kill Chain: Why Zero Opportunities

I traced the **exact pipeline** on the live server with real market data. Here is the full data flow:

```
891 raw candidate markets (from Polymarket Gamma API)
  │
  ├─ 209 rejected: no city match / no threshold / no token_id
  ├─ 341 rejected: "too_early_to_enter" (resolve date April 18+ → hours > 14h)
  └─ 341 PARSED VALID (resolve date April 17, hours 8-14h away)
       │
       ├─ ~316 are "exact" direction markets
       │     └─ 25+ killed by VOLUME GATE (volume_24hr < 500) 
       │     └─ rest killed by SPREAD GATE (spread > 0.12)
       │
       └─ ~5 are "above/below" direction markets with price <= $0.65
             │
             └─ ALL 5 have prob=0.0 → STRATEGY_FAIL
                Example: London below 12°C, forecast=17°C → 17 < 12? NO → prob=0.0
```

### The 3 Fatal Flaws Working Together

#### FLAW 1: Binary Probability Model (THE MAIN KILLER)

**File:** `market_discovery_internal/pricing.py` → `calculate_edge()` (line 144-176)

```python
if direction == "above":
    model_prob = 1.0 if forecast > threshold else 0.0  # ← BINARY!
elif direction == "below":
    model_prob = 1.0 if forecast < threshold else 0.0  # ← BINARY!
```

**Problem:** This gives probability = **1.0 or 0.0 only**. No gradient. No uncertainty. No forecast error margin.

- Markets where the forecast **clearly supports** the direction already have YES prices near $0.90-$1.00 → **fail price gate** (too expensive)
- Markets where the forecast **doesn't support** the direction have cheap prices ($0.01-$0.15) → **prob=0.0** → fail strategy filter
- There is NO middle ground where price is affordable AND probability is high

**The Fix:** Use a **sigmoid/logistic probability model** that accounts for forecast uncertainty:

```python
import math

def calculate_edge(market, forecast_temp):
    if forecast_temp is None: return None
    
    price = market.get("yes_price")
    threshold = market.get("threshold")
    direction = market.get("direction")
    forecast = float(forecast_temp)
    
    model_prob = 0.0
    if direction == "above":
        # Sigmoid: smooth transition centered at threshold
        # k controls steepness (higher = sharper transition)
        # At forecast=threshold: prob=0.5
        # At forecast=threshold+3: prob≈0.95
        k = 1.5  # tunable steepness (~3°C for 5%→95% transition)
        model_prob = 1.0 / (1.0 + math.exp(-k * (forecast - threshold)))
    elif direction == "below":
        k = 1.5
        model_prob = 1.0 / (1.0 + math.exp(-k * (threshold - forecast)))
    elif direction == "exact":
        # Keep Gaussian for exact (already works)
        diff = abs(forecast - threshold)
        sigma = MODEL_EXACT_SIGMA_C
        model_prob = math.exp(-0.5 * (diff / sigma)**2)

    edge = model_prob - price
    return {
        "model_prob": round(model_prob, 4),
        "edge": round(edge, 4),
        "forecast": forecast
    }
```

**Why sigmoid?** With `k=1.5`:
- Forecast 3°C above threshold → P(above) ≈ 0.989
- Forecast 1°C above threshold → P(above) ≈ 0.817 (THIS can generate opportunities!)
- Forecast at threshold → P(above) = 0.500
- Forecast 1°C below threshold → P(above) ≈ 0.182
- Forecast 3°C below threshold → P(above) ≈ 0.011

This creates the **gradient zone** where the bot can find edges on markets priced $0.15-$0.40.

---

#### FLAW 2: Exact Markets All Killed by Volume Gate

**File:** `market_discovery_internal/cycles.py` → `parse_discovery_markets()` (line 59-76)

Most of the 341 valid markets are **exact bracket** markets (e.g., "Will temperature be exactly 17°C?"). These have:
- Low 24h volume (often < $500) 
- Wide spreads (> $0.12)

The `MARKET_MIN_VOLUME_24HR=500` and `MARKET_MAX_SPREAD_GATE=0.12` gates kill ALL of them.

**The Fix:** Lower the gates for paper trading (we're simulating, not risking real money):

In `/opt/the_blueprints/.env`:
```
MARKET_MIN_VOLUME_24HR=50
MARKET_MAX_SPREAD_GATE=0.20
```

Or alternatively, apply these gates only in live mode, not paper mode.

---

#### FLAW 3: NOAA Consensus Block Has Indentation Bug

**File:** `market_discovery_internal/forecasting.py` (lines 161-192)

```python
if base_avg is not None:
    hist_avg = _fetch_historical_average(city, date)
    if hist_avg is not None:
        if abs(base_avg - hist_avg) > HISTORICAL_DEVIATION_C:
            _log_anomaly(...)
            return None
    
        # BUG: This entire NOAA consensus block is INSIDE "if hist_avg is not None:"
        # but it should run independently!
            current_hour = datetime.now().hour   # ← WRONG INDENT (inside hist_avg block)
            is_peak_heat = (12 <= current_hour <= 19)
            error_margin = abs(base_avg - t_noaa)
            if t_noaa > (base_avg + 1.0):
                return None
            if is_peak_heat and error_margin > CONSENSUS_MAX_ERROR_C:
                return None
```

**Impact:** If historical data fetch fails (returns None), the NOAA ground truth validation is **completely skipped**. This means in cities where historical API is flaky, the bot would accept bad data.

**Secondary bug:** `datetime.now().hour` uses SERVER LOCAL TIME, not the city's local time. For a New York market at peak heat (12-19 EST), the server (UTC) would see hours 17-24 — partially correct but shifted.

**The Fix:**
```python
if base_avg is not None:
    # [MODUL K] Historical Anomaly Check
    hist_avg = _fetch_historical_average(city, date)
    if hist_avg is not None:
        if abs(base_avg - hist_avg) > HISTORICAL_DEVIATION_C:
            _log_anomaly(city, date, base_avg, hist_avg, "historical_anomaly")
            return None
    
    # [MODUL B] NOAA Consensus Check (INDEPENDENT of hist_avg)
    if t_noaa is not None:
        current_hour = datetime.now(timezone.utc).hour  # Use UTC consistently
        is_peak_heat = (12 <= current_hour <= 22)  # Wider UTC window covers all target cities
        error_margin = abs(base_avg - t_noaa)
        
        if t_noaa > (base_avg + 1.0):
            _log_anomaly(city, date, base_avg, t_noaa, "prediction_exceeded_by_ground_truth")
            return None
        
        if is_peak_heat and error_margin > CONSENSUS_MAX_ERROR_C:
            _log_anomaly(city, date, base_avg, t_noaa, "consensus_mismatch_during_peak")
            return None

    ft = ForecastTemp(base_avg, source)
    ...
```

---

## 🔧 ADDITIONAL ISSUES TO FIX

### Issue 4: WebSocket Stale Timer Shows 56 Years

**File:** `market_discovery.py` line 190

```python
_last_ws_update_at = multiprocessing.Value('d', 0.0)  # epoch=0 → always stale!
```

**Impact:** Since initial value is 0.0 (Jan 1 1970), `time.time() - 0.0` = ~1.77 billion seconds. Bot permanently thinks WS is "stale" and runs aggressive 15-second polling.

**The Fix:** Initialize to current time:
```python
_last_ws_update_at = multiprocessing.Value('d', time.time())
```

---

### Issue 5: Port 8082 Binding Fails on Restart

**Root cause:** When `systemctl restart blueprints-bot` sends SIGTERM, the main process exits but the child processes (PriceWatcher multiprocessing, WsBroadcaster asyncio) may not die fast enough. The new instance tries to bind 8082 while the old child still holds it.

**The Fix in `market_discovery.py`:**
```python
def handle_exit_signal(signum, frame):
    print(f"\n[SIGN] Received signal {signum}. Shutting down THE BLUEPRINTS...")
    _stop_background_services()
    # Give children time to release sockets
    time.sleep(1)
    sys.exit(0)
```

**And in `blueprints-bot.service`:**
```ini
[Service]
...
KillMode=control-group    # Kill all child processes in the cgroup
KillSignal=SIGTERM
TimeoutStopSec=15         # Wait 15s for graceful shutdown before SIGKILL
```

---

### Issue 6: Circuit Breaker is Wrong

**File:** `cycles.py` line 606

```python
if wallet_after_position_management <= 3.50:  # ← WRONG: checks absolute wallet
```

**Master Plan says:** "Daily Loss Limit $1.50 → lock bot"

**The Fix:**
```python
# Calculate daily loss from baseline
daily_baseline = float(daily_session.get("baseline_wallet", PAPER_BASE_WALLET))
daily_loss = daily_baseline - wallet_after_position_management
if effective_allow_new_entries and daily_loss >= 1.50:
    effective_allow_new_entries = False
    effective_entry_gate_reason = "circuit_breaker_daily_loss"
```

---

### Issue 7: Haiku Monitor is a Stub

**File:** `analysis.py` line 328-335

Even when `HAIKU_MONITOR_ENABLED=true`, the function always returns `{"action": "hold", "confidence": 1.0}` without calling the API. This is a placeholder that was never implemented.

**Impact:** Low (during paper trading). But should be either:
- Disabled in `.env` (`HAIKU_MONITOR_ENABLED=false`) to be honest about status, OR
- Actually implemented

---

### Issue 8: `circuit_breaker_alert_sent` Never Resets

On the server, `circuit_breaker_alert_sent: true` but the daily reset logic at line 296-309 in `cycles.py` doesn't reset this flag.

**The Fix:** Add to the daily reset block:
```python
if last_cycle_dt and last_cycle_dt.date() < now_utc.date():
    daily_session = state_meta.get("daily_session", {})
    if isinstance(daily_session, dict):
        current_total = float(state_meta.get("current_wallet", PAPER_BASE_WALLET))
        daily_session["baseline_wallet"] = current_total
        state_meta["daily_session"] = daily_session
        state_meta.pop("circuit_breaker_alert_sent", None)  # ← ADD THIS
```

---

## 📋 EXECUTION CHECKLIST (Priority Order)

### P0 — Do First (Makes Bot Actually Trade)

- [ ] **Fix `calculate_edge()` in `pricing.py`** — Replace binary 0/1 with sigmoid model
- [ ] **Lower liquidity gates in `.env`** — `MARKET_MIN_VOLUME_24HR=50`, `MARKET_MAX_SPREAD_GATE=0.20`
- [ ] **Restart bot** — `systemctl restart blueprints-bot`
- [ ] **Verify cycle shows opportunities** — `tail -f /opt/the_blueprints/logs/paper_loop.out`

### P1 — Do Second (Correctness & Safety)

- [ ] **Fix NOAA consensus indentation** in `forecasting.py`
- [ ] **Fix WS stale timer init** in `market_discovery.py`
- [ ] **Fix port binding** — Add `KillMode=control-group` to service file
- [ ] **Reset `circuit_breaker_alert_sent`** flag in state

### P2 — Do Third (Risk Management)

- [ ] **Fix circuit breaker logic** — Use daily loss instead of absolute wallet
- [ ] **Disable or implement Haiku monitor** — Set `HAIKU_MONITOR_ENABLED=false` until real implementation
- [ ] **Add `circuit_breaker_alert_sent` daily reset**

---

## 🛡️ PREVENTION: What Got "Kesenggol" (Side Effects to Watch)

### Changes That Could Break Things If Done Wrong

1. **Sigmoid `k` parameter**: If `k` is too low (e.g., 0.5), the bot accepts garbage trades with prob=0.55. If too high (e.g., 5.0), it's almost binary again. **Start with k=1.5** and observe.

2. **Lowering volume gate**: If `MARKET_MIN_VOLUME_24HR` goes too low (e.g., 0), the bot may enter markets where it literally can't exit. **50 is safe** for paper trading.

3. **Lowering spread gate**: If `MARKET_MAX_SPREAD_GATE` is too wide (e.g., 0.50), entry prices are unreliable. **0.20 is reasonable**.

4. **NOAA consensus unindent**: Make sure the `if t_noaa is not None:` block is at the SAME indentation level as `if hist_avg is not None:` — both children of `if base_avg is not None:`.

5. **KillMode=control-group**: This is safe. It ensures all children die with the parent. But test with one restart cycle before leaving unattended.

6. **WS timer init**: By initializing to `time.time()`, the first cycle won't trigger stale fallback. The bot will use normal 300s intervals until WS actually goes stale.

### Files That Will Be Modified

| File | Change | Risk |
|------|--------|------|
| `market_discovery_internal/pricing.py` | `calculate_edge()` sigmoid | **HIGH** — core trading logic. Test manually first |
| `market_discovery_internal/forecasting.py` | NOAA consensus unindent | **MEDIUM** — affects forecast validation |
| `market_discovery.py` | WS timer init + shutdown sleep | **LOW** |
| `blueprints-bot.service` | KillMode + TimeoutStopSec | **LOW** |
| `/opt/the_blueprints/.env` | Volume/spread gates | **LOW** |
| `market_discovery_internal/cycles.py` | Circuit breaker + daily reset | **MEDIUM** |

### What NOT to Touch

- **`state_persistence.py`** — Atomic write logic is correct. Don't change.
- **`ws_price_watcher.py`** — Multiprocessing architecture is sound. The port issue is a cleanup timing issue only.
- **`parsing.py`** — Golden Window logic (8-14h) is correct and working. The `too_early_to_enter` rejections are genuine.
- **`execution.py`** — Dormant mode lock is critical. DO NOT unlock until live trading Phase 3.
- **`run_paper_5usd.sh`** — Not used by systemd. Leave it as manual backup runner.

---

## 🧪 VERIFICATION AFTER FIX

After applying P0 fixes, run this to verify:

```bash
# On server
cd /opt/the_blueprints
/opt/the_blueprints/venv/bin/python3 market_discovery.py --paper

# Expected output:
# - "opportunities: X" where X > 0
# - "Opened this cycle: Y" where Y > 0
# - Telegram notifications received
```

If still zero, run diagnostic:
```bash
/opt/the_blueprints/venv/bin/python3 scripts/diagnose_pipeline.py 2>&1 | tee /tmp/diag.log
```

---

## 📊 Expected Impact After Fix

With the sigmoid model (k=1.5) and lowered gates:
- Markets where forecast is 1-3°C away from threshold should now generate `prob=0.70-0.98`
- With prices at $0.15-$0.40, edge would be `0.30-0.80` — well above `STRATEGY_MIN_EDGE=0.20`
- Estimated new opportunity rate: **5-15 per cycle** (vs current 0)
- Exact bracket markets with relaxed gates: additional **3-8 per cycle**
