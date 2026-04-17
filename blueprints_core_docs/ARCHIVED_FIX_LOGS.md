# 🔍 THE BLUEPRINTS — Deep Audit Report: Wave 1-3 Compliance & Server Health

**Date:** 2026-04-17T01:31 WIB  
**Server:** `root@103.253.244.158` (`/opt/the_blueprints`)  
**Code:** `/Users/macairm12020/Documents/Blueprints/the_blueprints`

---

## Executive Summary

I've completed a line-by-line code audit of every module and a live server inspection. The **architecture is solid** — dependency injection, atomic state persistence, PID locking, and modular separation are well-engineered. However, the bot has been running for **185 consecutive empty cycles** with **zero trades** and several critical issues need immediate attention.

> [!CAUTION]
> **The bot is actively running but effectively dead** — 185 empty cycles with 0 opportunities found, 0 positions opened, and 0 PnL. The Telegram bot token is also **exposed in plaintext** in both the local `.env` and the server `.env`.

---

## 📊 Module-by-Module Compliance Matrix

### 🟢 WAVE 1: THE EYES (Akurasi & Filter)

| Module                             | Master Plan Spec                            | Code Status                                                                                      | Verdict                                                                       |
| ---------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| **[A] Precision Semantic Sensing** | Regex + AI Haiku extraction, Learning Cache | ✅ `analysis.py:resolve_station_with_ai()` + `station_knowledge.json` cache                      | **PASS** — But cache file is **empty** on server (no discoveries learned yet) |
| **[B] Multi-API Consensus**        | Open-Meteo + NOAA cross-validation          | ⚠️ `forecasting.py:fetch_forecast()` — Triple source (Open-Meteo + wttr.in + NOAA)               | **FLAW** — See Bug #1 below                                                   |
| **[C] Golden Window (8-14h)**      | Entry only 8-14h before resolve             | ✅ `DAILY_RESOLVE_ONLY=true`, `DAILY_MIN_RESOLVE_DAYS_AHEAD=1`, `DAILY_MAX_RESOLVE_DAYS_AHEAD=2` | **PASS** — Window is 1-2 days ahead (wider than spec, but safer)              |
| **[K] Anomaly Check**              | Block if temp jumps >7°C from daily average | ✅ `forecasting.py:_fetch_historical_average()` + `HISTORICAL_DEVIATION_C=7.0`                   | **PASS**                                                                      |
| **[S] Sempurna Sprint**            | 3-7 day paper trading verification          | ✅ `daily_monitor.py` + cron healthcheck                                                         | **FLAW** — See Bug #2: Sprint is stuck, no trades happening                   |

---

### 🟡 WAVE 2: THE HANDS (Live Execution)

| Module                 | Master Plan Spec                                    | Code Status                                                                          | Verdict                                               |
| ---------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| **[E] Liquidity Gate** | Check orderbook depth before entry                  | ✅ `pricing.py:check_liquidity_depth()` called in `cycles.py:build_paper_position()` | **PASS**                                              |
| **[G] Exit Sniper**    | Auto TP (90%) + pre-emptive SL via METAR every 5min | ✅ `cycles.py:evaluate_hybrid_exit()` + WS price watcher + Sniper at ≥0.90           | **PASS** — Hybrid exit logic is comprehensive         |
| **[H] Live Bridge**    | `py-clob-client` with Signature Type 0 (EOA)        | ✅ `execution.py:BlueprintsClobClient` — Properly locked in DORMANT mode             | **PASS** — Safety lock `is_dormant=True` is hardcoded |

---

### 🔴 WAVE 3: THE FORTRESS (Risk Management)

| Module                               | Master Plan Spec                                           | Code Status                                                       | Verdict                        |
| ------------------------------------ | ---------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------ |
| **[D] Compounding & Slot Expansion** | $5-$20: Stake $1-$2, 5-8 slots. $20+: 15% stake, 15+ slots | ✅ `cycles.py` lines 618-653 — Tier 1/2 logic + Safe Leverage Cap | **PASS**                       |
| **[L] Circuit Breaker**              | Lock bot if daily loss hits $1.50                          | ⚠️ `cycles.py` line 606: checks `wallet <= 3.50` not daily loss   | **FLAW** — See Bug #3          |
| **[M] Backtest Engine**              | Validate strategy on 7-day historical data                 | ❌ `backtest_runner.py` — **STUB ONLY** (just a print simulation) | **CRITICAL MISS** — See Bug #4 |

---

### 🔵 WAVE 4: THE SYSTEM (Stability) — _Not asked but checked for completeness_

| Module                        | Master Plan Spec                             | Code Status                                                                    | Verdict  |
| ----------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------ | -------- |
| **[F] Wallet Sentry**         | Alert if POL < 0.2 or USDC < $1              | ❌ **NOT IMPLEMENTED** — No on-chain balance checks exist                      | **MISS** |
| **[I] Self-Healing**          | Sync state vs on-chain order history hourly  | ❌ **NOT IMPLEMENTED** — No L2 order reconciliation                            | **MISS** |
| **[J] Emergency Kill-Switch** | Dashboard button for shutdown                | ⚠️ Web UI exists but **no kill-switch button** found in `web_ui/index.html`    | **MISS** |
| **[N] Mobile Alert Sentry**   | Telegram notifications for entry/exit/profit | ✅ `utils.py:send_telegram_alert()` — Entry/Exit/Circuit notifications working | **PASS** |

---

## 🚨 Critical Bugs & Flaws Found

### Bug #1: NOAA Consensus Logic — Indentation Error (CRITICAL)

**File:** [forecasting.py](file:///Users/macairm12020/Documents/Blueprints/the_blueprints/market_discovery_internal/forecasting.py#L161-L192)

```python
    if base_avg is not None:
        # [MODUL K] Historical Anomaly Check
        hist_avg = _fetch_historical_average(city, date)
        if hist_avg is not None:
            if abs(base_avg - hist_avg) > HISTORICAL_DEVIATION_C:
                _log_anomaly(city, date, base_avg, hist_avg, "historical_anomaly")
                return None

        # [MODUL B] Strict Consensus Check (Ground Truth METAR)
        # ⚠️ THIS ENTIRE BLOCK IS INSIDE `if hist_avg is not None:`
        # BUT IT SHOULD RUN INDEPENDENTLY!
            current_hour = datetime.now().hour  # <-- THIS IS INDENTED UNDER hist_avg check
            ...
```

The NOAA consensus validation (`t_noaa` comparison) is **nested inside** the `if hist_avg is not None:` block due to an indentation error. If historical data is unavailable, the NOAA ground truth validation is **completely skipped**. This defeats [MODUL B]'s purpose.

---

### Bug #2: 185 Empty Cycles — Bot Finds Zero Markets (CRITICAL)

**Server state:**

- `empty_temperature_cycles: 185`
- `Rolling PnL: $0.0000`
- `0 positions opened in entire runtime`

**Root causes identified:**

1. **Aggressive scan already on** (auto-triggered after 3 empty cycles) — not helping
2. Discovery fetches markets, but `parse_discovery_markets` + weather forecasts yield zero candidates
3. The combination of `STRATEGY_MIN_MODEL_PROB=0.74` + `STRATEGY_MIN_EDGE=0.34` + `PAPER_ENTRY_MIN_PRICE=0.15` + `PAPER_ENTRY_MAX_PRICE=0.40` is **extremely restrictive**
4. The above/below model probability in `calculate_edge()` returns **binary 0.0 or 1.0** — so the only markets that pass are ones where the forecast is already clearly above/below the threshold, AND the price is between 15-40 cents

> [!IMPORTANT]
> The binary probability model means edge = `1.0 - price` or `0.0 - price`. For a $0.30 YES token, edge would be 0.70 — passes. But for a $0.45 token, it fails the price gate. The usable window is extremely narrow.

---

### Bug #3: Circuit Breaker Logic is Wrong (HIGH)

**Master Plan:** "Lock bot if _daily loss_ hits $1.50"
**Code** ([cycles.py:606](file:///Users/macairm12020/Documents/Blueprints/the_blueprints/market_discovery_internal/cycles.py#L606)):

```python
if wallet_after_position_management <= 3.50:
    effective_allow_new_entries = False
```

This checks **absolute wallet** ≤ $3.50, not **daily drawdown** ≤ $1.50. The Master Plan spec says: _"Daily Loss Limit $1.50. If reached, `ENTRY_GATE_OPEN` → `False`."_

Additionally, `circuit_breaker_alert_sent` is `True` on server but was never reset (no daily reset logic for this flag).

---

### Bug #4: Backtest Runner is a Dead Stub (HIGH)

[backtest_runner.py](file:///Users/macairm12020/Documents/Blueprints/the_blueprints/market_discovery_internal/backtest_runner.py) contains only a `run_fuzz_simulation()` that prints static numbers. There is **no actual historical data processing**, no replaying of past markets, nowhere close to the spec: _"Validasi strategi pada data historis 7 hari terakhir"_.

---

### Bug #5: Haiku Position Monitor is a Phantom (HIGH)

[analysis.py:328-335](file:///Users/macairm12020/Documents/Blueprints/the_blueprints/market_discovery_internal/analysis.py#L328-L335):

```python
def _haiku_position_monitor(position, current_yes_price=None, hours_until_resolve=None):
    if not HAIKU_MONITOR_ENABLED or not ANTHROPIC_API_KEY:
        return {"action": "hold", "confidence": 1.0}
    # Simplified: Haiku logic here would check news or updated trends.
    # For now, return hold as default placeholder for WAVE 1 logic.
    return {"action": "hold", "confidence": 1.0}
```

Even when `HAIKU_MONITOR_ENABLED=true` (it is on the server!), the function **always returns "hold"**. The actual Haiku API call is never made. The server `.env` sets `HAIKU_MONITOR_ENABLED=true` but the code ignores it.

---

### Bug #6: WebSocket Stale Timer Shows Insane Value (MEDIUM)

**Server log:** `⚠️ WS Stale (1776364197s). Using Hybrid Fallback (15s).`

1,776,364,197 seconds = **~56 years**. This is because `_last_ws_update_at` is initialized to `multiprocessing.Value('d', 0.0)` (epoch zero). If WS never receives a message (0 subscriptions = 0 messages), the stale timer reads as `time.time() - 0.0` = current epoch seconds.

This causes the bot to **permanently** run in aggressive 15-second polling mode, which wastes resources.

---

### Bug #7: Telegram Bot Token Exposed in Git (CRITICAL SECURITY)

The Telegram bot token `8692939768:AAGhnTsDu9-Fjz_qSYTpBXC4vbV2lKxchcI` is:

1. In the local `.env` file
2. In the server `.env` file
3. The `.gitignore` only has 63 bytes — likely not comprehensive enough

> [!CAUTION]
> If this `.env` was ever committed to git, the token needs to be **revoked via @BotFather immediately** and regenerated. Anyone with the token can send messages as your bot.

---

### Bug #8: `run_paper_5usd.sh` Bypass — Server Doesn't Use It (MEDIUM)

The systemd service runs `market_discovery.py --paper-loop` directly:

```
ExecStart=/opt/the_blueprints/venv/bin/python3 /opt/the_blueprints/market_discovery.py --paper-loop
```

But the hardened `run_paper_5usd.sh` script sets crucial env overrides (`PAPER_ENTRY_MIN_PRICE=0.10`, `PAPER_ENTRY_MAX_PRICE=0.65`, etc.) that are **not applied** when running directly. The server `.env` has its own values which don't match the shell script defaults.

---

### Bug #9: Duplicate `POLYMARKET_API_KEY` in Local `.env` (LOW)

```
POLYMARKET_API_KEY=your_api_key_here    # Line 1
POLYMARKET_API_KEY=                      # Line 3
```

The second one overrides the first. Not harmful but messy.

---

### Bug #10: `state.json` Schema Drift from Master Plan (MEDIUM)

**Master Plan specifies:**

```json
{
  "meta": {
    "daily_base_balance": 5.0,
    "daily_loss_limit": 1.5,
    "entry_gate_open": true,
    "bot_mode": "LIVE"
  },
  "risk": {
    "circuit_breaker_active": false,
    "anomalies_detected_today": 0
  }
}
```

**Actual state** has different field names (`current_wallet`, `last_entry_gate_open`, etc.) and no separate `risk` object. The schema should be reconciled.

---

### Bug #11: UI Service Uses `python3 -m http.server` (LOW)

Nginx is already configured to serve `/opt/the_blueprints` on port 8080. The `blueprints-ui.service` running `python3 -m http.server 8080` **conflicts** with nginx on the same port. Only one can bind. It appears nginx wins (as it's running). The UI service is redundant.

---

### Bug #12: Dual PriceWatcher Subprocesses (MEDIUM)

Server shows **4+ PriceWatcher subprocesses**:

```
root 139680  /opt/the_blueprints/venv/bin/python ... --paper-loop
root 139686  /opt/the_blueprints/venv/bin/python ... --paper-loop
root 139695  /opt/the_blueprints/venv/bin/python3 ... --paper-loop
root 139701  /opt/the_blueprints/venv/bin/python3 ... --paper-loop
```

The PID lock should prevent duplicates, but multiprocessing spawns child processes for PriceWatcher + WsBroadcaster + Manager. With frequent restarts (journalctl shows 7+ restarts in 3 hours), orphan processes may accumulate if `_stop_background_services()` doesn't clean up fast enough.

---

### Bug #13: `_save_json_blob` Not Fully Atomic (LOW)

[utils.py:92-102](file:///Users/macairm12020/Documents/Blueprints/the_blueprints/market_discovery_internal/utils.py#L92-L102) — The `_save_json_blob` function doesn't `fsync` before `os.replace`. The `save_paper_state` in `state_persistence.py` does `fsync` properly. This inconsistency means AI ledger/cache files could get corrupted on power loss.

---

### Bug #14: NOAA METAR Check Uses Server Local Time (MEDIUM)

[forecasting.py:174](file:///Users/macairm12020/Documents/Blueprints/the_blueprints/market_discovery_internal/forecasting.py#L174):

```python
current_hour = datetime.now().hour  # VPS time check
is_peak_heat = (12 <= current_hour <= 19)
```

Uses **server local time** (UTC+7 WIB on your VPS). For a New York market, "peak heat" 12-19 server time = 1-8 AM EST — completely wrong timezone. The peak heat window should be calculated **per city's timezone**, not the server's.

---

## 🏥 Server Health Summary

| Metric                 | Value                                     | Status                          |
| ---------------------- | ----------------------------------------- | ------------------------------- |
| **Bot Service**        | `active` (running since 01:25)            | 🟢                              |
| **UI Service**         | `active`                                  | 🟡 Conflicting with nginx       |
| **Open Positions**     | 0                                         | 🔴 Dead                         |
| **Empty Cycle Streak** | 185                                       | 🔴 Critical                     |
| **Wallet**             | $5.00                                     | 🟢 Unchanged                    |
| **PnL**                | $0.00                                     | 🔴 No trades ever               |
| **WS Connection**      | Connected but 0 subscriptions             | 🟡 Expected (no open positions) |
| **Crontab**            | `*/15 min` cycle + `*/10 min` healthcheck | 🟢                              |
| **Nginx**              | Config OK, proxying WS to 8082            | 🟢                              |
| **AI Budget**          | $1.04 / $3.00 spent (34.7%) in April      | 🟢                              |
| **Station Knowledge**  | Empty                                     | 🟡 No stations learned          |
| **Anomaly Log**        | Empty                                     | 🟢 No anomalies (also no data)  |

---

## 🔧 Proposed Fix Plan (Priority Order)

### P0 — Immediate (Bot is Dead)

1. **Fix NOAA consensus indentation** in `forecasting.py` — Unindent the METAR consensus block from the `hist_avg` conditional
2. **Fix NOAA timezone** — Use city-aware timezone for peak heat check instead of server local time
3. **Fix WS stale timer initialization** — Set `_last_ws_update_at` to current time instead of epoch 0
4. **Revoke and regenerate Telegram bot token** — Current one is in plaintext in potentially-committed files

### P1 — High (Make Trades Possible)

5. **Tune strategy thresholds** — The current combination is too restrictive for the live market. Consider:
   - Lower `STRATEGY_MIN_EDGE` from 0.34 → 0.20
   - Widen `PAPER_ENTRY_MAX_PRICE` from 0.40 → 0.55
   - Or implement a **gradient probability model** instead of binary 0/1 for above/below
6. **Fix Circuit Breaker** — Implement actual daily-loss tracking ($1.50 drawdown) instead of absolute wallet check
7. **Reset `circuit_breaker_alert_sent`** on server daily reset
8. **Implement Haiku Monitor** — Actually call the Anthropic API when `HAIKU_MONITOR_ENABLED=true`

### P2 — Medium (Completeness)

9. **Build real Backtest Engine** (Modul M) — Replay historical weather + market data
10. **Fix UI service conflict** — Remove `blueprints-ui.service` since nginx handles serving
11. **Add `fsync` to `_save_json_blob`** for all cache/ledger files
12. **Clean up duplicate PriceWatcher processes** — Add process cleanup on restart

### P3 — Deferred (Wave 4)

13. **Implement Wallet Sentry** (Modul F) — On-chain POL/USDC balance monitoring
14. **Implement Self-Healing** (Modul I) — L2 order history reconciliation
15. **Add Kill-Switch** (Modul J) — Dashboard button for emergency shutdown

---

## Open Questions

> [!IMPORTANT]
>
> 1. **Telegram Token:** Has the `.env` file ever been committed to git? If yes, we need to revoke the token immediately via @BotFather.
> 2. **Strategy Tuning:** Should we lower the edge/probability thresholds to start getting trades, or do you want to first fix the code bugs and see if that alone produces opportunities?
> 3. **Priority:** Should I start fixing P0 bugs immediately, or do you want to review this plan first?
