#!/usr/bin/env python3
"""One-shot diagnostic: trace why zero opportunities are found."""
import sys, json, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.config import *
from market_discovery_internal.discovery import fetch_markets
from market_discovery_internal.parsing import parse_market
from market_discovery_internal.forecasting import fetch_forecast
from market_discovery_internal.pricing import calculate_edge

raw = fetch_markets(inspect=False, aggressive_scan=True)

parsed_valid = []
for m in raw:
    result = parse_market(m, return_skip_reason=True)
    if isinstance(result, tuple):
        parsed, reason = result
    else:
        parsed = result
        reason = None
    if parsed:
        parsed_valid.append(parsed)

print(f"Parsed: {len(parsed_valid)} markets")

# Direction counts
dir_counts = {}
for p in parsed_valid:
    d = p.get("direction", "?")
    dir_counts[d] = dir_counts.get(d, 0) + 1
print(f"Direction distribution: {json.dumps(dir_counts)}")

# Examine ALL above/below markets
print("\n=== ABOVE/BELOW MARKETS DETAIL ===")
checked = 0
for p in parsed_valid:
    direction = p.get("direction")
    if direction == "exact":
        continue
    
    city = p.get("city")
    date = p.get("date")
    threshold = p.get("threshold")
    unit = p.get("unit")
    yes_price = p.get("yes_price", 0)
    
    forecast_temp = fetch_forecast(city, date, p.get("icao_code"))
    if forecast_temp is None:
        print(f"  {city} {direction} {threshold}{unit} | FORECAST=None")
        continue
    
    edge_data = calculate_edge(p, forecast_temp)
    if edge_data:
        prob = edge_data["model_prob"]
        edge_val = edge_data["edge"]
        fc = float(forecast_temp)
        
        if direction == "above":
            logic = f"fc={fc:.1f} > thr={threshold}? {'YES' if fc > threshold else 'NO'}"
        elif direction == "below":
            logic = f"fc={fc:.1f} < thr={threshold}? {'YES' if fc < threshold else 'NO'}"
        else:
            logic = direction
        
        marker = "PASS" if (prob >= STRATEGY_MIN_MODEL_PROB and edge_val >= STRATEGY_MIN_EDGE) else "FAIL"
        print(f"  [{marker}] {city} {direction} {threshold}{unit} | yes={yes_price} | {logic} | prob={prob} edge={edge_val:.4f}")
        checked += 1
        if checked >= 40:
            break

# Exact markets summary
print("\n=== EXACT MARKETS THAT PASS PRICE GATE ===")
exact_checked = 0
for p in parsed_valid:
    if p.get("direction") != "exact":
        continue
    yes_price = p.get("yes_price", 0)
    if yes_price > STRATEGY_MAX_YES_PRICE:
        continue
    
    city = p.get("city")
    date = p.get("date")
    threshold = p.get("threshold")
    
    forecast_temp = fetch_forecast(city, date, p.get("icao_code"))
    if forecast_temp is None:
        continue
    
    edge_data = calculate_edge(p, forecast_temp)
    if edge_data:
        fc = float(forecast_temp)
        prob = edge_data["model_prob"]
        edge_val = edge_data["edge"]
        diff = abs(fc - threshold)
        marker = "PASS" if (prob >= STRATEGY_EXACT_MIN_MODEL_PROB and edge_val >= STRATEGY_EXACT_MIN_EDGE) else "FAIL"
        print(f"  [{marker}] {city} exact {threshold}{p.get('unit')} | yes={yes_price} | fc={fc:.1f} | diff={diff:.1f} | prob={prob:.4f} edge={edge_val:.4f}")
        exact_checked += 1
        if exact_checked >= 20:
            break

print(f"\nChecked {exact_checked} exact markets")
print("\nDONE")
