# 🛡️ Anti-429 Resilience Protocol (Gudang Data)
**Version:** 1.0.0 "The Padded Shield"
**Strategy:** Extreme Rate-Limit Compliance for Open-Meteo

This document explains the "Safe-by-Design" architecture implemented to neutralize 429 errors and prevent IP blacklisting from the Open-Meteo Archive API.

---

## 🏗️ 1. Architecture: Producer-Consumer Model
We have decoupled the **fetching** of historical data from the **consumption** of historical data.

- **The Producer (`Warmer`)**: Only the `GudangDataWarmer` service is authorized to perform live hits to the Archive API. It runs with a strict **12-second stealth interval** between requests.
- **The Consumer (`Cycles/Discovery`)**: The main trading engine is now in **Cache-Only Mode**. It is physically restricted from calling the live Archive API.

## 🧠 2. Implementation Logic
The `_fetch_historical_average` function now includes an `allow_live` switch:

```python
# PRODUCER (Warmer) -> allow_live=True
# CONSUMER (Cycles) -> allow_live=False (SAFE)
def _fetch_historical_average(city, date, allow_live=True):
    # 1. Always check SQLite Warehouse first
    # 2. If missing AND allow_live=False -> Return None immediately
    # 3. If missing AND allow_live=True -> Perform Throttled API Call
```

## ⏳ 3. The "Cold Start" Protocol
When the database is wiped or reset, the following happens:
1. **Empty Warehouse**: Initial trading cycles will find 18+ events, but skip them because historical averages are missing from the cache.
2. **Background Priming**: The `Warmer` begins populating the cache at a rate of ~5 data points per minute.
3. **Saturation**: Within 10-15 minutes, the most common city/date events are cached. 
4. **Resumption**: Trading cycles automatically begin processing the events as soon as the warmer provides the data.

## 🏁 4. Strategic Benefits
- **Zero Ban Risk**: The main loop cannot "burst" the API limits, even if a discovery cycle finds 1,000 markets.
- **Improved Latency**: Consumer cycles never wait for a slow 8-retry API call; it's either an instant SQLite hit or a safe skip.

---

**MAINTENANCE INSTRUCTION:** Never set `allow_live=True` inside `cycles.py` or `discovery.py`. This is the core "Zero-Flaw" safety gate.
