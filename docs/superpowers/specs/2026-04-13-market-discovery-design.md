# Market Discovery Component — Design Spec
**Date:** 2026-04-13
**Status:** Implemented (V1)
**Scope:** `market_discovery.py` only — no trading execution

---

## Overview

A Python script that finds mispriced weather markets on Polymarket by comparing
market prices against real forecast data from Open-Meteo. Surfaces opportunities
where the market underprices an event that the forecast considers likely.

**Target edge:** YES price < 0.35 AND model probability >= 0.70 (produces edge >= 0.35)

**Implementation status (2026-04-13):**
- End-to-end CLI orchestrator implemented (`--inspect` + normal mode)
- Parser supports structured fields first (`tags`, `slug`, `description`) with title fallback
- Direction handling finalized as: `above` uses `>=`, `below` uses strict `<`
- `exact` markets are parsed but skipped in V1 scoring pipeline
- Automated validation: `pytest` passing (50 tests)

---

## Cities in Scope

New York, Chicago, London, Tokyo, Hong Kong, Miami, Sydney, Toronto

---

## Architecture: Single File, Modular Per Layer

One file (`market_discovery.py`) divided into clear layers. Each function has
one responsibility and can be tested or debugged independently.

```
main()
  │
  ├─► [--inspect mode]
  │     fetch_markets() → dump 3 raw JSON samples → exit
  │
  └─► [normal mode]
        fetch_markets()              # Gamma API
              ▼
        parse_market(raw)            # extract structured fields from title
              ▼
        fetch_forecast(city, date)   # Open-Meteo daily max temp
              ▼
        calculate_edge(market, forecast)
              ▼
        filter_opportunities()       # yes_price < 0.35 AND model_prob >= 0.70
              ▼
        print_opportunities()
        print_summary()
```

### AI-Ready Design
`parse_market()` and `calculate_edge()` are the two functions designed to be
swapped with an AI reasoning layer in V2. Their interfaces are kept clean and
isolated for this reason. V1 uses rule-based implementations only.

---

## Layer Specs

### 1. `fetch_markets(inspect=False) → list[dict]`
- Endpoint: `https://gamma-api.polymarket.com/markets`
- Params: `tag=weather`, `active=true`
- Retry: 3x with exponential backoff (1s → 2s → 4s)
- If all retries fail: exit with clear error message (no point continuing)
- If `inspect=True`: print first 3 raw results as formatted JSON and exit
- Returns: list of raw market dicts

### 2. `parse_market(raw: dict) → dict | None`
- Input: raw market dict from Gamma API
- First: check API-provided structured fields (tags, slug, description)
- Fallback: regex parse from `question` / `title` field
- Extract: `city`, `date`, `threshold`, `unit` (F or C), `direction` (above/below/exact)
- Also extract: `yes_price` (from `outcomePrices`), `token_id`, `end_date`
- If city not in target list: skip silently
- If parse fails: log raw title to `logs/unmatched_markets.log`, return None
- Returns: structured market dict or None

### 3. `fetch_forecast(city: str, date: str) → float | None`
- API: Open-Meteo (`https://api.open-meteo.com/v1/forecast`)
- Fetch: daily max temperature for next 3 days in one request
- Return only the max temp for the specific `date` requested
- Coordinates: hardcoded lat/lon per city (no geocoding dependency)
- Retry: 3x per city with exponential backoff (1s → 2s → 4s)
- If all retries fail: return None, log city to error summary
- Returns: forecasted max temp as float (in °C by default)

### 4. `calculate_edge(market: dict, forecast_temp: float) → dict`
- Convert units if needed (market in °F → convert forecast °C to °F)
- V1 model:
      - `above`: `model_prob = 1.0 if forecast >= threshold else 0.0`
      - `below`: `model_prob = 1.0 if forecast < threshold else 0.0`
      - `exact`: unsupported in V1 scorer (market is skipped by orchestrator)
- Edge: `edge = model_prob - yes_price`
- Returns: market dict enriched with `model_prob`, `edge`, `hours_until_resolve`,
      `forecast_temp_c`, and `forecast_temp_converted`

### 5. `filter_opportunities(markets: list[dict]) → list[dict]`
- Keep only: `yes_price < 0.35 AND model_prob >= 0.70`
- (This naturally produces edge > 0.35, exceeding the 0.30 minimum)
- Sort by edge descending (highest edge first)

### 6. `print_opportunities(opportunities: list[dict])`
- Clean readable output per opportunity:
  ```
  [1] New York — Will max temp exceed 80°F on Apr 15?
      YES price : 0.28
      Model prob: 0.85
      Edge      : +0.57
      Token ID  : 0xabc123...
      Resolves  : 18h
  ```

### 7. `print_summary()`
- Print at end of every run:
  ```
  === RUN SUMMARY ===
  Cities fetched : 7/8  (Tokyo: timeout after 3 retries)
      Markets parsed : 43/51 (8 skipped)
      Exact skipped  : 2
  Opportunities  : 3
  ==================
  ```

---

## Error Handling

| Failure Point         | Behavior                                              |
|-----------------------|-------------------------------------------------------|
| Gamma API down        | Retry 3x → exit with error (can't proceed without markets) |
| Open-Meteo per city   | Retry 3x → skip city, log to summary                |
| Title parse failure   | Skip market, append raw title to `logs/unmatched_markets.log` |
| Unknown city in title | Skip silently (not in target list)                   |
| Direction `exact`     | Parse successfully, skip from V1 edge scoring        |
| Unit conversion error | Skip market, log warning                             |

---

## File Structure

```
the_blueprints/
├── market_discovery.py
├── .env                        # API keys (future use)
├── .env.example                # committed to git, no real values
├── requirements.txt            # requests, python-dotenv
└── logs/
    └── unmatched_markets.log   # auto-created on first run
```

---

## Configuration (via .env)

```env
# Reserved for future API keys (e.g. CLOB API for trading)
# No keys required for market discovery
POLYMARKET_API_KEY=
```

---

## City Coordinates (hardcoded)

| City      | Lat     | Lon      |
|-----------|---------|----------|
| New York  | 40.7128 | -74.0060 |
| Chicago   | 41.8781 | -87.6298 |
| London    | 51.5074 |  -0.1278 |
| Tokyo     | 35.6762 | 139.6503 |
| Hong Kong | 22.3193 | 114.1694 |
| Miami     | 25.7617 | -80.1918 |
| Sydney    | -33.8688| 151.2093 |
| Toronto   | 43.6532 | -79.3832 |

---

## Opportunity Output Schema

```python
{
    "city": str,
    "date": str,                 # YYYY-MM-DD
      "end_date": str,             # ISO datetime
    "market_question": str,
    "yes_price": float,          # 0.0–1.0
    "model_prob": float,         # 0.0–1.0
    "edge": float,               # model_prob - yes_price
      "forecast_temp_c": float,
      "forecast_temp_converted": float,
    "token_id": str,
    "hours_until_resolve": float
}
```

---

## CLI Usage

```bash
# Inspect raw API response (run once before first real use)
python market_discovery.py --inspect

# Normal discovery run
python market_discovery.py
```

---

## Out of Scope (V1)

- Trading execution
- AI/LLM reasoning layer (designed for V2 swap)
- Exact-market probability modeling (exact markets are currently skipped)
- Historical backtesting
- Scheduling / cron
- Web UI or dashboard
- Database persistence

---

## V2 Upgrade Path

When ready to add AI reasoning:
1. Replace `parse_market()` with Claude API call for title extraction
2. Replace `calculate_edge()` with Claude reasoning over forecast + market context
3. No other files need to change
