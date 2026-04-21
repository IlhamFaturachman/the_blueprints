# The Blueprints — Phase A: 5-7 Day Operations Plan

**Created:** 21 April 2026, 23:15 WIB  
**Period:** 22 April – 28 April 2026  
**Starting Wallet:** $5.00  
**Mode:** Paper Trading (Batch A optimizations active)

---

## Current Bot Configuration

| Parameter | Value | Source |
|-----------|-------|--------|
| Wallet | $5.00 | `.env` PAPER_BASE_WALLET=5.0 |
| Stake per trade | $1.00 | `.env` PAPER_STAKE_USD=1.0 |
| Max open positions | 5 | `.env` PAPER_MAX_OPEN_POSITIONS=5 |
| Golden window | 4-18h before resolve | `.env` GOLDEN_WINDOW_HOURS_MIN/MAX |
| Min edge | 10% | `.env` STRATEGY_MIN_EDGE=0.10 |
| Min model prob | 60% | `.env` STRATEGY_MIN_MODEL_PROB=0.60 |
| Confidence to hold | 55% | `.env` HYBRID_MIN_CONFIDENCE_TO_HOLD=0.55 |
| Thesis decay threshold | 35% | Code default (no .env override) |
| Entry price range | $0.15-$0.75 | Code: WATCH_MAX=0.15, MAX_YES=0.75 |
| Seasonal sigma | Active (April = 1.35x for 4-season cities) | Code |
| Forecast blend | 64/36 (Open-Meteo/wttr.in) | Code |
| Time-decay edge | **OFF** (dormant) | Not set in .env |
| AI/Haiku | **OFF** | `.env` HAIKU_*_ENABLED=false |
| VPS commit | `f194918` | Latest on master |

---

## Golden Window Schedule (Daily)

Polymarket weather markets resolve at **12:00 UTC (19:00 WIB)**.
(Verified from live API: `endDate: 2026-04-22T12:00:00Z`)

| Event | UTC | WIB (UTC+7) |
|-------|-----|-------------|
| Golden window opens (18h before) | 18:00 (previous day) | **01:00** |
| Early morning opportunities | 22:00-02:00 | **05:00-09:00** |
| Peak opportunity zone | 02:00-06:00 | **09:00-13:00** |
| Golden window closes (4h before) | 08:00 | **15:00** |
| Markets resolve | 12:00 | **19:00** |

**Best time to check the bot:** Morning **07:00-08:00 WIB** (bot should already have found opportunities) and afternoon **14:00-15:00 WIB** (before window closes).

---

## Daily Monitoring Checklist (5 minutes/day)

### Morning Check (07:00-08:00 WIB)

The golden window opens at 01:00 WIB, so by morning the bot may already have
opened positions. This is the most important check of the day.

1. **Open dashboard:** `http://103.253.244.158:8080/web_ui/`
2. **Verify:**
   - Portfolio value is reasonable ($5.00 + any real PnL)
   - No phantom "NEW YORK" trades with $91+ PnL (test data)
   - Gate status shows `ACTIVE` (not `CIRCUIT_BREAKER_TRIPPED`)
   - Open positions (if any) show real cities and reasonable prices
3. **Check Telegram** for any overnight alerts (entries, exits, errors)

### Afternoon Check (14:00-15:00 WIB — before golden window closes)

4. **Open dashboard again**
5. **Note the day's results:**
   - How many trades opened today?
   - How many closed? Win or loss?
   - What close reasons? (take_profit, sniper, stop_loss, late_window, etc.)
   - Current portfolio value
6. **Log it mentally or in a note** — we need this data for Day 3 and Day 7 decisions

### Evening Check (19:00-20:00 WIB — after markets resolve, optional)

7. Check if all positions for today's markets have settled (resolved to $0 or $1)

### Quick SSH Health Check (only if something looks wrong)

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158

# Is the bot running?
systemctl is-active blueprints.service

# Last 10 log lines
journalctl -u blueprints.service --no-pager -n 10

# State check
cd /opt/the_blueprints
python3 -c "import json; s=json.load(open('logs/paper_positions_5usd.json')); m=s['meta']; print('Cash:', m.get('cash')); print('Gate:', m.get('last_entry_gate_reason','open'))"
```

---

## Red Flags & Emergency Actions

### CRITICAL (Act Immediately)

| Red Flag | What It Means | Action |
|----------|---------------|--------|
| Portfolio jumps to $50+ suddenly | Phantom test trades returned | SSH and run: `python3 -c "import sqlite3; c=sqlite3.connect('logs/blueprints_master.db'); print(c.execute(\"SELECT COUNT(*) FROM trade_history WHERE token_id IN ('tok_tp','tok_sl','0xabc')\").fetchone()[0])"` — if > 0, contact developer |
| Dashboard shows `CIRCUIT_BREAKER_TRIPPED` | Daily drawdown exceeded 15% | Check if it's a real loss (legitimate, bot resets next day) or data corruption (bug). If unsure, contact developer |
| Bot service shows `inactive` or `failed` | Bot crashed | Run: `systemctl restart blueprints.service` then check: `journalctl -u blueprints.service -n 30` |

### WARNING (Monitor, No Immediate Action Needed)

| Red Flag | What It Means | Action |
|----------|---------------|--------|
| 0 trades after 2 full days | Filters may be too strict | Note it down. On Day 3, consider relaxing STRATEGY_MIN_EDGE from 0.10 to 0.08 |
| `INSUFFICIENT_CASH` every cycle | All cash deployed in open positions | Normal if 3-5 positions are open. Cash frees up when positions close. |
| Telegram silent for 24h+ | Bot may not be cycling | SSH and check logs |
| All trades are losses | Bad luck or calibration issue | Normal variance for first few trades. Wait for 10+ trades before judging. |

### NORMAL (No Action Needed)

| Observation | Explanation |
|-------------|-------------|
| 0 trades during late evening (after 15:00 WIB) | Normal — golden window closes at 15:00 WIB |
| `empty_temperature_cycles` increasing | Normal — not every cycle finds tradeable markets |
| Small losses on individual trades | Normal — expected win rate is ~70%, meaning ~30% are losses |

---

## Day-by-Day Plan

### Day 1-2 (April 22-23): Observe Batch A

**Goal:** Collect baseline data with Batch A optimizations.

- Do NOT change any settings
- Do NOT enable TIME_DECAY_EDGE
- Just monitor morning + evening
- Note: How many trades? What entry prices? What close reasons?

**Expected behavior:**
- 2-4 trades per day (limited by $5 wallet and 5 position slots)
- Entry prices in $0.15-$0.65 range (wider than before thanks to Batch A)
- Most entries happen between 01:00-15:00 WIB (golden window)
- Mix of take_profit, sniper, stop_loss, late_window exits
- Some cycles show 0 opportunities (normal, especially after 15:00 WIB)

### Day 3 (April 24): First Decision Point

**Review the data so far:**
- Total trades: ideally 5-10 by now
- Win rate: hopefully >60%
- Average entry price: should include some cheap entries ($0.15-$0.35)
- PnL trend: positive or at least break-even

**Decision: Enable TIME_DECAY_EDGE?**

If the bot has been entering 3+ trades/day with reasonable results:
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158
cd /opt/the_blueprints

# Add time-decay to .env
echo "TIME_DECAY_EDGE_ENABLED=true" >> .env

# Restart bot
systemctl restart blueprints.service

# Verify
systemctl is-active blueprints.service
```

If the bot has been entering 0-1 trades/day:
- Do NOT enable time-decay (it would make filters even stricter)
- Instead, consider relaxing STRATEGY_MIN_EDGE:
```bash
# In .env, change:
# STRATEGY_MIN_EDGE=0.10
# To:
# STRATEGY_MIN_EDGE=0.08
```

### Day 4-5 (April 25-26): Steady State

**Goal:** Let the bot run with stable settings. Collect more data.

- Continue morning + evening monitoring
- If TIME_DECAY was enabled on Day 3, check if trade volume dropped
  - If dropped to 0-1/day: add `TIME_DECAY_BASE_HOURS=10.0` to .env and restart (softens the scaling)
  - If still 2-4/day: good, leave it

### Day 6-7 (April 27-28): Review & Plan Next Phase

**Compile a mini-report:**
- Total trades over 7 days
- Win rate (wins / total)
- Total PnL ($)
- Average ROI per trade (%)
- Best and worst trades
- Most active cities
- Most common close reasons

**Decisions for next phase:**
- Is the bot profitable? → Consider Batch B (1d Smart-Skip 2x TP)
- Is the bot breaking even? → Analyze which trades are losing and why
- Is the bot losing money? → Review entry criteria, may need tighter filters

---

## Emergency Procedures

### If Bot Crashes and Won't Restart

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158

# Check what happened
journalctl -u blueprints.service --no-pager -n 50

# Check disk space (if full, bot can't write)
df -h /

# Check memory
free -m

# Force restart
systemctl restart blueprints.service
```

### If You Need to Stop the Bot Completely

```bash
systemctl stop blueprints.service
```

### If You Need to Reset to Clean $5 State

```bash
systemctl stop blueprints.service
cd /opt/the_blueprints

# Clean DB
./venv/bin/python3 -c "
import sqlite3
c = sqlite3.connect('logs/blueprints_master.db')
c.execute('DELETE FROM trade_history')
c.execute('DELETE FROM active_positions')
c.execute('DELETE FROM cycle_metrics')
c.execute('DELETE FROM calibration_stats')
c.execute('UPDATE portfolio_summary SET base_wallet=5.0, cash=5.0, total_pnl=0.0')
c.commit()
print('DB cleaned')
"

# Clean JSON
cat > logs/paper_positions_5usd.json << 'EOF'
{"positions":[],"history":[],"cycle_journal":[],"meta":{"base_wallet":5.0,"cash":5.0,"current_wallet":5.0,"daily_session":{"baseline_wallet":5.0},"auto_tuner":{"computed_at":null,"adjustments":{},"blacklisted":[]},"acceptance_metrics_rolling":{},"current_tier":1,"circuit_breaker_alert_sent":false,"empty_temperature_cycles":0}}
EOF

# Restart
systemctl start blueprints.service
```

### If Phantom Trades Appear Again (Should NOT Happen)

This was caused by pytest writing test data to the production DB. Fixed in commit `f194918`. If it somehow recurs:

```bash
# Check for test tokens
cd /opt/the_blueprints
./venv/bin/python3 -c "
import sqlite3
c = sqlite3.connect('logs/blueprints_master.db')
n = c.execute(\"SELECT COUNT(*) FROM trade_history WHERE token_id IN ('tok_tp','tok_sl','0xhaiku','0xabc','0x0')\").fetchone()[0]
print(f'Test tokens: {n}')
if n > 0:
    print('BUG: Test data leaked. Clean DB and contact developer.')
else:
    print('Clean. Issue is something else.')
"
```

---

## What NOT to Do During This Period

- **Do NOT run `pytest` on the VPS** unless deploying a new fix (the phantom trades bug is fixed, but there's no reason to run tests on production)
- **Do NOT edit production code on the VPS directly** — always edit locally, test, commit, push, then pull on VPS
- **Do NOT change `.env` values without restarting the bot** — changes only take effect after restart
- **Do NOT enable AI/Haiku** — it's disabled for a reason (caused losses historically)
- **Do NOT increase PAPER_BASE_WALLET** — keep at $5 for Phase A data collection
- **Do NOT implement Batch B yet** — wait until Day 7 review

---

## Key Contacts & Resources

| Resource | Location |
|----------|----------|
| VPS SSH | `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158` |
| Dashboard | `http://103.253.244.158:8080/web_ui/` |
| Bot service | `blueprints.service` |
| State file | `/opt/the_blueprints/logs/paper_positions_5usd.json` |
| Database | `/opt/the_blueprints/logs/blueprints_master.db` |
| .env config | `/opt/the_blueprints/.env` |
| Bot logs | `journalctl -u blueprints.service` |
| GitHub repo | `git@github.com:IlhamFaturachman/the_blueprints.git` |
| Current commit | `f194918` |

---

## Batch A Changes Active in This Period

| # | Change | Effect |
|---|--------|--------|
| 1a | ENTRY_BUCKET_WATCH_MAX_PRICE 0.40→0.15 | Markets $0.15-$0.65 now tradeable (was $0.40-$0.65) |
| 1b | HYBRID_MIN_CONFIDENCE_TO_HOLD 0.75→0.55 | More positions held to resolution instead of sold at H-2 |
| 1c | THESIS_DECAY_THRESHOLD 0.45→0.35 | Fewer premature exits on winning positions |
| 2a | Seasonal sigma (April=1.35x for 4-season cities) | More conservative probabilities during volatile spring weather |
| 2b | forecast_still_valid returns float 0.0-1.0 | Graduated validity instead of binary cliff at 0.70 |
| 2c | Confidence uses entry_edge in late window | Prevents confidence drop as market converges toward correct price |
| 2e | Weighted forecast blend 64/36 | Open-Meteo weighted higher (more reliable API) |

## Dormant Features (Ready to Enable)

| Feature | How to Enable | When |
|---------|---------------|------|
| Time-decay edge scaling | Add `TIME_DECAY_EDGE_ENABLED=true` to .env, restart | Day 3 (if trade volume is good) |
| Smart-skip 2x TP (Batch B 1d) | Requires code implementation | After Day 7 review |
