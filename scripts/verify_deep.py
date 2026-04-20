"""Deep verification script — traces every critical code path."""
import json, math, sys
from datetime import datetime, timezone, timedelta
from market_discovery_internal.parsing import parse_market
from market_discovery_internal.pricing import calculate_edge, _get_city_sigma
from market_discovery_internal.config import (
    SIGMA_TROPICAL, SIGMA_FOUR_SEASON, SIGMA_DEFAULT,
    STRATEGY_EXACT_MIN_MODEL_PROB, STRATEGY_EXACT_MIN_EDGE,
    FLASH_CRASH_MAX_DROP_PCT, FLASH_CRASH_MIN_TICK_WINDOW_SECONDS,
)
from market_discovery_internal.cycles import compute_kelly_stake

print("=" * 60)
print("DEEP VERIFICATION: Every Critical Code Path")
print("=" * 60)

now = datetime.now(timezone.utc)
end = (now + timedelta(hours=12)).isoformat()

def make_raw(question, prices="[\"0.30\", \"0.70\"]", bid="0.29", ask="0.31", tok="tok"):
    return {
        "question": question,
        "endDate": end,
        "outcomePrices": prices,
        "bestBid": bid, "bestAsk": ask,
        "clobTokenIds": json.dumps([tok, tok + "n"]),
    }

# TEST 1: F->C conversion
print("\n--- TEST 1: F->C Conversion ---")

m1 = parse_market(make_raw("Will the highest temperature in Dallas be 80F or above on April 21?", tok="t1"))
assert m1 and m1["unit"] == "F" and m1["direction"] == "above"
r1 = calculate_edge(m1, 28.0, hours_until_resolve=12)  # 28C=82.4F > 80F
assert r1 and r1["model_prob"] > 0.5, f"Dallas above: prob={r1['model_prob'] if r1 else 'None'}"
print(f"  1a Dallas above 80F: forecast=28C(82.4F), prob={r1['model_prob']:.3f} OK")

m2 = parse_market(make_raw("Will the highest temperature in Chicago be 55F or below on April 21?", prices='["0.60","0.40"]', bid="0.59", ask="0.61", tok="t2"))
assert m2 and m2["direction"] == "below"
r2 = calculate_edge(m2, 10.0, hours_until_resolve=12)  # 10C=50F < 55F
assert r2 and r2["model_prob"] > 0.5, f"Chicago below: prob={r2['model_prob'] if r2 else 'None'}"
print(f"  1b Chicago below 55F: forecast=10C(50F), prob={r2['model_prob']:.3f} OK")

m3 = parse_market(make_raw("Will the highest temperature in London be 14C on April 21?", prices='["0.25","0.75"]', bid="0.24", ask="0.26", tok="t3"))
assert m3 and m3["unit"] == "C" and m3["direction"] == "exact"
r3 = calculate_edge(m3, 14.0, hours_until_resolve=12)
assert r3 and r3["model_prob"] > 0.05, f"London exact: prob={r3['model_prob'] if r3 else 'None'}"
print(f"  1c London exact 14C: forecast=14C, prob={r3['model_prob']:.3f} OK")

m4 = parse_market(make_raw("Will the highest temperature in New York City be between 50-51F on April 21?", prices='["0.40","0.60"]', bid="0.39", ask="0.41", tok="t4"))
assert m4 and m4["threshold"] == 50.0 and m4["threshold_high"] == 51.0 and m4["unit"] == "F"
r4 = calculate_edge(m4, 10.0, hours_until_resolve=12)  # 10C=50F
assert r4 and r4["model_prob"] > 0.05, f"NYC range: prob={r4['model_prob'] if r4 else 'None'}"
print(f"  1d NYC range 50-51F: forecast=10C(50F), prob={r4['model_prob']:.3f} OK")

# TEST 2: Per-Region Sigma
print("\n--- TEST 2: Per-Region Sigma ---")
assert _get_city_sigma("singapore") == SIGMA_TROPICAL
assert _get_city_sigma("new york city") == SIGMA_FOUR_SEASON
assert _get_city_sigma("madrid") == SIGMA_DEFAULT
print(f"  Singapore: s={_get_city_sigma('singapore')} OK")
print(f"  NYC: s={_get_city_sigma('new york city')} OK")
print(f"  Madrid: s={_get_city_sigma('madrid')} OK")

# TEST 3: Kelly Criterion
print("\n--- TEST 3: Kelly Criterion ---")
k1 = compute_kelly_stake(edge=0.10, model_prob=0.70, available_cash=100.0, entry_price=0.30)
assert k1 > 0, f"Kelly good edge: {k1}"
print(f"  3a Good edge: stake=${k1:.2f} OK")

k2 = compute_kelly_stake(edge=0.01, model_prob=0.50, available_cash=100.0, entry_price=0.50)
assert k2 == 0.0, f"Kelly no edge: {k2}"
print(f"  3b No edge: stake=${k2:.2f} OK")

k3 = compute_kelly_stake(edge=0.05, model_prob=0.30, available_cash=100.0, entry_price=0.50)
assert k3 == 0.0, f"Kelly negative: {k3}"
print(f"  3c Negative Kelly: stake=${k3:.2f} OK")

k4 = compute_kelly_stake(edge=0.10, model_prob=0.0, available_cash=100.0, entry_price=0.30)
assert k4 == 0.0, f"Kelly prob=0: {k4}"
print(f"  3d Prob=0: stake=${k4:.2f} OK")

# TEST 4: Flash Crash Config
print("\n--- TEST 4: Flash Crash Shield Config ---")
assert FLASH_CRASH_MAX_DROP_PCT == 0.40
assert FLASH_CRASH_MIN_TICK_WINDOW_SECONDS == 90.0
print(f"  Max drop: {FLASH_CRASH_MAX_DROP_PCT*100}% OK")
print(f"  Min tick window: {FLASH_CRASH_MIN_TICK_WINDOW_SECONDS}s OK")

# TEST 5: Exact bracket filter thresholds
print("\n--- TEST 5: Exact Bracket Filter ---")
assert STRATEGY_EXACT_MIN_MODEL_PROB == 0.10
assert STRATEGY_EXACT_MIN_EDGE == 0.02
print(f"  Exact min prob: {STRATEGY_EXACT_MIN_MODEL_PROB} OK")
print(f"  Exact min edge: {STRATEGY_EXACT_MIN_EDGE} OK")

# TEST 6: NOAA ICAO keys
print("\n--- TEST 6: NOAA ICAO Key ---")
assert m1.get("icao_code") == "KDFW", f"Dallas: {m1.get('icao_code')}"
assert m2.get("icao_code") == "KORD", f"Chicago: {m2.get('icao_code')}"
assert m3.get("icao_code") == "EGLL", f"London: {m3.get('icao_code')}"
assert m4.get("icao_code") == "KJFK", f"NYC: {m4.get('icao_code')}"
print(f"  Dallas: {m1['icao_code']} OK")
print(f"  Chicago: {m2['icao_code']} OK")
print(f"  London: {m3['icao_code']} OK")
print(f"  NYC: {m4['icao_code']} OK")

# TEST 7: Edge cases
print("\n--- TEST 7: Edge Cases ---")

m7a = parse_market(make_raw("Will the highest temperature in Toronto be 0C or below on April 21?", prices='["0.20","0.80"]', bid="0.19", ask="0.21", tok="t7a"))
assert m7a is not None
r7a = calculate_edge(m7a, -2.0, hours_until_resolve=12)
assert r7a and r7a["model_prob"] > 0.5
print(f"  7a Toronto below 0C: forecast=-2C, prob={r7a['model_prob']:.3f} OK")

m7b = parse_market(make_raw("Will the highest temperature in Miami be 100F or above on April 21?", prices='["0.05","0.95"]', bid="0.04", ask="0.06", tok="t7b"))
assert m7b is not None
r7b = calculate_edge(m7b, 32.0, hours_until_resolve=12)  # 32C=89.6F < 100F
assert r7b and r7b["model_prob"] < 0.3
print(f"  7b Miami above 100F: forecast=32C(89.6F), prob={r7b['model_prob']:.3f} OK")

r7c = calculate_edge({"yes_price": 0.30, "threshold": 50, "unit": "F", "direction": "above", "city": "dallas"}, None)
assert r7c is None
print(f"  7c forecast=None: returns None OK")

# TEST 8: Ensemble weight import
print("\n--- TEST 8: Ensemble Weight Import ---")
from market_discovery_internal.pricing import ENSEMBLE_WEIGHT, POINT_FORECAST_WEIGHT, WTRIN_WEIGHT
assert ENSEMBLE_WEIGHT == 0.45
print(f"  ENSEMBLE_WEIGHT={ENSEMBLE_WEIGHT} OK")

# TEST 9: build_paper_position returns None when Kelly says no
print("\n--- TEST 9: Kelly Guard in build_paper_position ---")
from market_discovery_internal.cycles import build_paper_position
result = build_paper_position(
    {"yes_price": 0.50, "token_id": "test", "city": "test", "direction": "above",
     "threshold": 50, "unit": "C", "model_prob": 0.30, "edge": 0.01,
     "hours_until_resolve": 12, "date": "2026-04-21", "end_date": end},
    available_cash=100.0,
)
assert result is None, f"Expected None when Kelly says no bet, got {type(result)}"
print(f"  Kelly guard: build_paper_position returns None OK")

print()
print("=" * 60)
print("ALL 9 VERIFICATION TESTS PASSED")
print("=" * 60)
