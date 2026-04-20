# Baseline Strict Trading Logic (V1)

This document preserves the "maximalist" strict logic and thresholds used before the April 20th optimization sprint. Use these values to revert the bot to its most defensive and precise state.

## Core Gates (config.py)

- **Minimum Model Probability**: `0.60` (60%)
- **Minimum Edge**: `0.10` (10 cents)
- **Golden Window (Max)**: `14.0` hours before resolution
- **Golden Window (Min)**: `8.0` hours before resolution
- **Minimum 24h Volume**: `500` USD
- **Maximum Spread**: `0.12` (12 cents)

## Parsing Logic (parsing.py)

### Ambiguity Guard
The original logic was strictly exclusionary:
```python
if len(allowed_stations) > 1 and not source_explicit:
    return None, "ambiguous_station"
```
If a city like New York or London had multiple airport stations and the market title did not explicitly contain the station code (e.g., JFK, LHR), the market was rejected immediately.

### City Matching
Matches were based on strict regex patterns in `CITY_PATTERNS`.

## Strategy Goal
The goal of this baseline was "Zero-Flaw" precision, prioritizing high accuracy over trade volume.
