# Design Spec: Observation Edge + ECMWF/EMOS Forecast Upgrade

**Date:** 2026-08-04
**Status:** Brainstorming — Pending User Approval
**Author:** Brainstorming session (user + assistant)

---

## 1. Problem Statement

The Blueprints bot trades Polymarket daily-max-temperature bracket markets. Prior research (Claude Code session 7677785a, Jul 26 2026) found:

1. **Market sigma = 0.90°C** — fit from 598 bracket price curves
2. **Bot forecast error = 1.15°C** vs market consensus — bot consistently picks wrong bracket
3. **Bot sigma was 1.0/1.5/2.0** (constant) — now fixed to ensemble spread
4. **3 ICAO stations wrong** (Taipei, Houston, Hong Kong) — now fixed on VPS
5. **Bot Brier = 0.2898 vs market Brier = 0.0456** — market is 6.4× more accurate
6. **6 routes to profitability tested, all closed** — no forecast edge, no momentum, no arbitrage
7. **station_bias table has only 3 real samples** — bias correction never runs
8. **Shadow scorecard baseline: -119.4% vs market**

User's strategy: buy at 0.10-0.30, TP at 2× entry (0.20-0.60), never hold to resolve. Enter early in golden window (14-18h before resolve).

### Key insight: observation edge (with caveat)

For US cities, the daily high temperature typically occurs at 14:00-16:00 local time (~20:00-23:00 UTC the day before resolve). By the time golden window opens (18:00 UTC), the peak has likely already occurred. METAR reports the **current** temperature at hourly observation time — not the daily high directly. To determine the likely winning bracket, we must track the **running daily max** across all METAR observations since local midnight, and model the probability that the current running max is the final daily max.

**This is NOT certain arbitrage.** After 17:00 local time, the probability that the running max equals the final daily max is high (temperature is declining), but not 100%. A late-afternoon spike or an unseasonably warm evening could still push the high higher. This makes the observation edge a **high-probability bet, not a guaranteed win** — and the design must account for this uncertainty.

### What was NOT tested before

Prior session tested:
- Forecast edge (model vs market) — lost 6.4×
- Momentum (autocorrelation) — zero
- Martingale (TP/SL flipping) — negative EV even at zero fee
- Bracket arbitrage (sum < 1.0) — illusion of mid vs ask
- Market making — adverse selection
- Cross-venue (Kalshi vs Polymarket) — different contracts

Prior session did NOT test:
- **How fast Polymarket prices converge to the winning bracket after the running daily max becomes a high-probability lock** (e.g., after 17:00 local when temp is declining)
- Whether there's a tradeable window where the winning bracket is still 0.10-0.30 despite the outcome being ~90% determined

---

## 2. Architecture: 3-Phase Approach

### Phase 1: Running-Max Lag Study (7 days, zero capital)

**Goal:** Determine if there's a time window between the running daily max becoming a high-probability lock and Polymarket bracket prices converging.

**Key concept: running daily max + lock probability**

```
Local day starts (midnight local time)
  → Begin tracking running_max from METAR observations
  → Each hourly METAR: if temp > running_max, update running_max

After 14:00-16:00 local time (peak window):
  → running_max is LIKELY the final daily max
  → But not certain — temp could still rise

After 17:00 local time:
  → Temperature is declining (diurnal cycle)
  → Probability running_max = final_max increases
  → Model this probability empirically from historical data

The "lock" event = the time when running_max is sufficiently
likely to be the final daily max (e.g., 85%+ confidence).
```

**Lock probability model:**

For each US city, using IEM historical data (2+ years):
1. For each day, find the actual daily max and the time it occurred
2. For each hour after peak, compute: P(running_max = final_max | hour, city)
3. Fit a simple model: `P_lock(h) = f(hour_after_peak, margin_above_running_2nd_highest)`

Example (hypothetical, to be verified from data):
| Hour (local) | P(running_max = final) |
|---|---|
| 14:00 | 30% |
| 15:00 | 55% |
| 16:00 | 75% |
| 17:00 | 85% |
| 18:00 | 92% |
| 19:00 | 96% |
| 20:00 | 98% |

**Components:**

| Component | Function |
|---|---|
| METAR Poller | Every 60s, read METAR for each US city ICAO. Track **running daily max** since local midnight. Record each new high + timestamp. |
| Lock Detector | When running_max meets lock criteria (hour ≥ 17:00 local AND P_lock ≥ 85%), flag as "lock event". Record: city, date, running_max, lock_time, winning_bracket_estimate. |
| Price Tracker | Concurrent, poll CLOB best_bid/ask for each bracket. Record price at lock event, +1m, +5m, +15m, +30m, +1h, +2h, +3h. |
| Outcome Verifier | Next day, fetch IEM `daily.py` actual max_temp. Compare to running_max at lock time. Was the lock correct? |

**Output:** `logs/running_max_lag_study.jsonl`

```json
{
  "city": "dallas",
  "date": "2026-08-05",
  "local_peak_time": "15:30 CDT",
  "running_max_at_17:00_local": 35.0,
  "lock_time_utc": "2026-08-05T22:00Z",
  "lock_probability": 0.88,
  "winning_bracket_estimate": "35-36°C",
  "actual_daily_max": 35.0,
  "lock_correct": true,
  "bracket_prices": {
    "at_lock": {"winning": 0.18, "runner_up": 0.22, "other": 0.08},
    "+5m":  {"winning": 0.19, "runner_up": 0.21, "other": 0.08},
    "+15m": {"winning": 0.28, "runner_up": 0.18, "other": 0.06},
    "+30m": {"winning": 0.41, "runner_up": 0.12, "other": 0.04},
    "+1h":  {"winning": 0.65, "runner_up": 0.05, "other": 0.02},
    "+2h":  {"winning": 0.85, "runner_up": 0.02, "other": 0.01}
  },
  "time_to_050": 1620,
  "time_to_090": 7200,
  "tp_2x_entry_reachable": true,
  "tp_window_seconds": 5400,
  "fill_depth_usd_at_entry": 12.50
}
```

**Key metrics:**
- `lock_accuracy`: % of days where running_max at lock = actual daily max (target: ≥85%)
- `time_to_050`: seconds from lock to winning bracket crossing 0.50
- `time_to_090`: seconds to crossing 0.90
- `tp_window_seconds`: duration price stayed above 2× typical entry
- `fill_feasible`: does orderbook have ≥$5 depth at entry price?

**Decision gate:**
```
IF lock_accuracy ≥ 85% AND median(time_to_050) > 300s:
    → Window exists. Proceed to Phase 3 shadow trading (Mode A).
IF lock_accuracy < 70%:
    → Running max is too unreliable. Observation edge is weak.
    → Pivot to forecast-only strategy (Phase 2 + Phase 3 Mode B).
IF lock_accuracy ≥ 85% BUT median(time_to_050) < 60s:
    → Market prices in too fast. No tradeable window.
    → Observation edge exists but is not captureable.
```

**Implementation:**
- Script: `scripts/running_max_lag_study.py`
- Background process on VPS, does not touch production trading
- Uses existing `fetch_noaa_metar()` from `forecasting.py` (but tracks running max, not just latest)
- Uses existing `fetch_orderbook_quote()` from `pricing.py`
- Lock probability model computed from IEM historical data at startup

**Critical implementation note:** The existing `fetch_noaa_metar()` reads only `data[0]` (latest point reading). The lag study must accumulate ALL METAR observations for the local day and track the running max. This requires either:
- (a) Polling every hour and maintaining state, OR
- (b) Requesting `hours=24` parameter to get all observations for the day and computing max

Option (b) is simpler and more robust.

---

### Phase 2: ECMWF + EMOS Forecast Upgrade (parallel, zero capital)

**Goal:** Upgrade forecast pipeline for Asian/European cities where observation edge doesn't exist. Target: forecast error 1.15°C → ~0.76°C.

**2A. ECMWF Open Data Integration**

- Library: `ecmwf-opendata` (pip install)
- Data: 51-member ensemble, CC BY 4.0 license
- Variable: `mx2t3` (3-hourly max temp, available for all members)
- Frequency: 2x daily (00z + 12z runs)
- Format: GRIB2, decode with `eccodes` + `xarray`/`cfgrib`
- Storage: SQLite warehouse table `ecmwf_ensemble_raw`
- Coordinate interpolation: bilinear from 0.25° grid to station lat/lon
- Cloud backup: S3 `s3://ecmwf-forecasts` (no-sign-request, archive since Jan 2023)

**2B. IEM Training Labels**

- API: `https://mesonet.agron.iastate.edu/api/1/daily.py`
- Parameter: `max_temp_f` (convert to °C)
- Verified: reproduces Polymarket settlement (RCSS 85/85 days exact)
- Backfill: 2 years historical daily max per station
- Stations: all 31 target cities' ICAO codes
- Storage: existing `weather_archive` table in SQLite

**2C. EMOS Post-Processing (per-station)**

```
Model: Gaussian predictive distribution
  μ = a + b × ensemble_mean
  σ² = c + d × ensemble_variance

Training: rolling 60-day window per station
  Features: ensemble_mean, ensemble_variance (from ECMWF 51 members)
  Labels: IEM daily max_temp observation
  Optimization: minimize CRPS via scipy.optimize
  Output: per-station coefficients (a, b, c, d)

  Fallback: if <30 training samples, use raw ensemble mean + spread
  (no EMOS correction, just like current behavior)
```

**Literature context:** EMOS/Kalman filter removes bias but does NOT reduce standard deviation (Homleid 1995). After bias correction, error goes from 1.15°C → ~0.76°C, but sigma stays ~0.95°C. This means:
- For Asian/European cities: we'll be closer to the market but still slightly worse (0.95 vs 0.90)
- Bias correction is free improvement (wrong brackets → less wrong brackets)
- But it does NOT create an edge over the market
- The forecast edge path (Phase 3 Mode B) is unlikely to be profitable on its own
- Its main value is: (1) stopping stupid losses from wrong brackets, (2) providing a calibrated baseline for comparison

**2D. Decision Engine Changes**

| Current | New |
|---|---|
| Open-Meteo + wttr.in blend | ECMWF 51-member direct |
| wttr.in (293 records, hobby API) | Removed entirely |
| Sigma: constant 1.0/1.5/2.0 | Ensemble spread (already fixed) |
| station_bias: 3 samples | EMOS coefficients per station |
| Forecast mean: 1.15°C error | Target: ~0.76°C after EMOS |

**Unchanged:** Entry bucket logic, Kelly sizing, hybrid exit (7 conditions), flash crash shield, paper mode, execution bridge, dashboard, Telegram alerts.

---

### Phase 3: Shadow Trading (7-14 days, zero capital)

**Goal:** Validate the user's strategy (buy 0.10-0.30, TP 2× entry) with real market data, zero risk.

**Mode A: US Cities (Observation Edge — High-Probability Bet)**

```
1. Track running daily max from METAR (all observations since local midnight)
2. After 17:00 local time, compute lock_probability
3. If lock_probability ≥ 85% AND winning_bracket best_ask is 0.10-0.30:
   → Shadow BUY at best_ask (record entry_price, timestamp, lock_prob)
   → This is a HIGH-PROBABILITY bet, NOT a guaranteed win
   → The remaining risk: temp could still rise, changing the winning bracket
4. Track price every 60s
5. Shadow SELL when best_bid ≥ 2 × entry_price → record TP
6. Shadow SL when best_bid ≤ 0.25 × entry_price → record SL
7. If neither triggers by resolve → record as "unresolved" (settle at $1 or $0)
8. Next day: verify actual daily max vs running_max at entry time
   → If running_max was wrong: mark trade as "lock_failed"
   → Track lock_accuracy separately from TP hit rate
```

**Risk model for Mode A:**
- Lock probability is NOT 100% — some days the temp will rise after entry
- On those days, the "winning bracket" we bought will be wrong
- The TP/SL structure must account for this:
  - When temp rises past the next bracket, our bracket's price drops (effectively SL)
  - The question is: does TP (2×) trigger before SL (0.25×) often enough?
  - This is what Phase 3 measures

**Mode B: Asian/European Cities (Forecast Edge)**

```
1. ECMWF + EMOS produces calibrated forecast + sigma
2. Compute model_prob for each bracket
3. If model_prob >> market_price (edge > 15%):
   → Shadow BUY bracket at best_ask
4. Same TP/SL tracking as Mode A
5. Compare shadow Brier vs market Brier (ongoing scorecard)
```

**Output:** `logs/shadow_trades.jsonl`

**Metrics tracked:**
- `lock_accuracy`: % of trades where running_max at entry = actual daily max
- `tp_hit_rate`: % of trades that hit 2× before 0.25× (target: >50%)
- `sl_hit_rate`: % of trades that hit 0.25× first
- `avg_time_to_tp`: seconds from entry to TP
- `net_pnl`: including 7% overround + 5% taker fee
- `fill_probability`: orderbook depth ≥ $5 at entry
- `brier_model_vs_market`: ongoing scorecard

**Decision gate:**
```
IF lock_accuracy ≥ 85% AND tp_hit_rate > 50% AND net_pnl > 0:
    → Green light: implement real trading with observation edge
    → Size positions with Kelly, using lock_probability as the "model_prob"
ELSE IF lock_accuracy ≥ 85% BUT tp_hit_rate < 40%:
    → Lock is reliable but TP timing is wrong
    → Market converges too fast or too slow
    → Analyze: is it a spread problem (can't fill at TP) or a timing problem?
ELSE IF lock_accuracy < 70%:
    → Running max is too unreliable as a daily-high proxy
    → Abandon Mode A, rely on Mode B (forecast edge) only
ELSE:
    → Extend shadow period 7 more days
```

---

## 3. Data Flow

```
Phase 1 (Running-Max Lag Study):
  NOAA METAR API (hours=24) → running_max tracker → lock_detector
  Polymarket CLOB API → price_tracker → record bracket prices
  IEM historical data → lock_probability_model (computed at startup)
  → logs/running_max_lag_study.jsonl

Phase 2 (ECMWF + EMOS):
  ECMWF Open Data (GRIB2) → ecmwf-opendata → SQLite ecmwf_ensemble_raw
  IEM API → daily max_temp_f → SQLite weather_archive
  EMOS training (rolling 60d) → per-station coefficients → station_bias table
  ECMWF ensemble + EMOS → calibrated forecast → pricing.py calculate_edge()

Phase 3 (Shadow Trading):
  Mode A: METAR running_max + lock_prob → shadow buy → track → TP/SL/lock_verified
  Mode B: ECMWF+EMOS → model_prob → shadow buy → track → TP/SL
  → logs/shadow_trades.jsonl
  → Decision gate output
```

---

## 4. Error Handling

| Scenario | Handling |
|---|---|
| ECMWF API unavailable | Fallback to Open-Meteo ensemble (current behavior) |
| IEM API unavailable | Skip EMOS training for that station, use raw ensemble |
| METAR API unavailable | Skip observation for that city/day, log gap |
| Polymarket CLOB API unavailable | Skip price tracking, log gap |
| EMOS training <30 samples | Use raw ensemble mean + spread (no EMOS) |
| GRIB2 decode failure | Log error, skip that run, use cached forecast |
| Lock probability model has <30 historical days for a city | Use a global default curve (pooled across all cities) |
| Running max at 17:00 local is wrong (temp rises later) | This is expected ~15% of the time. Not a bug — it's the risk of the strategy. Track as `lock_failed`. |

---

## 5. Testing

| Test | Method |
|---|---|
| ECMWF fetch | Unit test: mock GRIB2, verify decode + bilinear interpolation to station |
| EMOS coefficients | Unit test: synthetic (mean, variance, obs) triples, verify CRPS minimization converges |
| IEM label match | Integration test: fetch real IEM data for RCSS, compare to known Polymarket settlement |
| Lock probability model | Unit test: given synthetic daily curves, verify P(running_max=final_max) computation |
| Running max tracker | Unit test: feed sequence of hourly temps, verify running max + lock detection |
| Observation lag study | Integration test: mock METAR + CLOB, verify recording pipeline |
| Shadow trade execution | Unit test: simulate price path (winning bracket rises, losing bracket falls), verify TP/SL logic |
| Lock failure handling | Unit test: temp rises after lock → verify trade marked as `lock_failed` |
| Phase 1 → Phase 3 handoff | Integration test: verify lag study data feeds into shadow trading Mode A |

---

## 6. Dependencies

| Package | Purpose | Cost |
|---|---|---|
| `ecmwf-opendata` | ECMWF data access | Free (CC BY 4.0) |
| `eccodes` | GRIB2 decoder | Free (open source) |
| `xarray` + `cfgrib` | GRIB2 → xarray | Free |
| `scipy` | EMOS optimization | Already installed |
| Existing: `requests`, `sqlite3` | HTTP, DB | Already installed |

**New requirements.txt additions:**
```
ecmwf-opendata>=1.0.0
eccodes>=2.30.0
xarray>=2024.0.0
cfgrib>=0.9.10.0
```

---

## 7. Scope Boundaries

**In scope:**
- Phase 1: running-max lag study script + METAR/CLOB data collection + lock probability model
- Phase 2: ECMWF integration, IEM labels, EMOS post-processing, decision engine upgrade
- Phase 3: shadow trading engine (Mode A + B), metrics, decision gate

**Out of scope:**
- Live trading activation (gated by Phase 3 results)
- Strategy changes beyond data source + post-processing upgrade
- Refactoring existing trading logic (entry/exit/Kelly/flash crash)
- UI/dashboard changes
- AI/Haiku re-enablement

**Non-goal:** Do NOT change the entry bucket logic, Kelly sizing, hybrid exit conditions, or flash crash shield. The only changes to decision-making are: (1) better forecast input data, (2) per-station bias correction, (3) observation-based entry for US cities (with lock probability as the confidence model). Everything else stays.

---

## 8. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| No observation edge (market prices in instantly) | Medium | High — strategy dead | Phase 1 tests this first, 7 days zero cost |
| Lock accuracy too low (<70%) | Medium | High — Mode A dead | Fall back to Mode B (forecast only) |
| Temp rises after lock (15% expected) | Expected | Medium — some trades lose | This is the risk of the strategy. TP/SL + Kelly sizing handle it. Track lock_accuracy separately. |
| ECMWF GRIB2 parsing complex | High | Medium — delays Phase 2 | Open-Meteo wraps ECMWF; use as fallback |
| EMOS doesn't improve below 0.90°C | High (literature confirms) | Medium — Phase 2 less useful | Bias correction still helps (1.15→0.76°C). Stops stupid losses. |
| Shadow trading shows negative EV | Medium | Terminal — no edge exists | Zero capital cost; answer without losing money |
| VPS resource constraints (1 CPU, 1GB RAM) | Medium | Medium — ECMWF processing heavy | Run ECMWF fetch in background, cache aggressively, use Open-Meteo fallback |

---

## 9. Success Criteria

| Phase | Success metric | Target |
|---|---|---|
| Phase 1 | lock_accuracy (running_max at 17:00 local = actual daily max) | ≥85% |
| Phase 1 | median(time_to_050) (lock → winning bracket crosses 0.50) | >300s (5 min) |
| Phase 1 | fill_depth_usd at entry price | ≥$5 |
| Phase 2 | Forecast error after EMOS (Asian/European cities) | <0.80°C (from 1.15°C) |
| Phase 2 | Shadow scorecard skill vs market | >-50% (improvement from -119%) |
| Phase 3 | Shadow TP hit rate (Mode A + B combined) | >50% |
| Phase 3 | Shadow net PnL (with fees) | >0% |
| Phase 3 | Lock accuracy in shadow trades | ≥85% |
| Overall | Decision to trade live or stop | Data-driven, zero capital risk |
