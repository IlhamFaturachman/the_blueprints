import json
import os
import sys
from datetime import datetime, timezone

# Ensure market_discovery_internal is in the path
sys.path.append(os.getcwd())

from market_discovery_internal.analysis import _haiku_entry_analysis
from market_discovery_internal.config import ANTHROPIC_API_KEY

def run_trap_simulation():
    if not ANTHROPIC_API_KEY:
        print("ERROR: Anthropic API Key missing. Cannot run live simulation.")
        return

    TRAPS = [
        {"id": "TRAP_1_IMP_HIGH", "city": "New York City", "forecast": 75.0, "market": "Will NYC be 80F or higher?", "desc": "Impossible High Temperature (75C)"},
        {"id": "TRAP_2_IMP_LOW", "city": "Chicago", "forecast": -80.0, "market": "Will Chicago be below 10F?", "desc": "Impossible Low Temperature (-80C)"},
        {"id": "TRAP_3_CONFLICT", "city": "London", "forecast": 45.0, "market": "London 25C or above?", "desc": "Extreme Disagreement (Mocked as single value for AI parsing)"},
        {"id": "TRAP_4_NONE_DATA", "city": "Paris", "forecast": None, "market": "Paris 20C or higher?", "desc": "Missing Temperature Data"},
        {"id": "TRAP_5_STALE", "city": "Miami", "forecast": 30.0, "market": "Miami 75F or lower?", "desc": "Stale evidence (handled by quality checks before AI)", "age": 48},
        {"id": "TRAP_6_MISMATCH", "city": "Tokyo", "forecast": 15.0, "market": "Will Tokyo reach 35C?", "desc": "Market Question vs Forecast Mismatch"},
        {"id": "TRAP_7_AMBIGUOUS", "city": "Unknown City", "forecast": 25.0, "market": "Random Weather Market?", "desc": "Ambiguous/Unknown City name"},
        {"id": "TRAP_8_SUSPICIOUS", "city": "London", "forecast": 58.0, "market": "Heatwave in London?", "desc": "Highly suspicious temperature (58C)"},
        {"id": "TRAP_9_REVERSE", "city": "Sydney", "forecast": 35.0, "market": "Sydney below 20C?", "desc": "Strong contradictory evidence (Forecast 35, Market Below 20)"},
        {"id": "TRAP_10_NO_UNIT", "city": "Berlin", "forecast": 22.0, "market": "Will it be 22 in Berlin?", "desc": "Missing Unit in Question"}
    ]

    print(f"=== 🦅 LIVE AI TRAP SIMULATION (10 CYCLES) ===")
    print(f"Target: Zero Errors | Budget Limit: $0.75\n")

    pass_count = 0
    for i, trap in enumerate(TRAPS, 1):
        opportunity = {
            "token_id": f"test_token_{trap['id']}",
            "market_question": trap["market"],
            "city": trap["city"],
            "date": "2026-04-18",
            "direction": "above", # dummy
            "threshold": 20.0,
            "unit": "C",
            "yes_price": 0.50,
            "forecast_temp_c": trap["forecast"],
            "forecast_source": "trap-simulator"
        }
        
        print(f"Testing Cycle {i}/10: {trap['desc']}...")
        result = _haiku_entry_analysis(opportunity)
        
        recommendation = result.get("recommendation", "error").lower()
        reason = result.get("reasoning", "No reason provided")
        
        # In all these trap cases, the AI SHOULD recommend 'skip'
        if recommendation == "skip":
            print(f"  ✅ [PASS] AI correctly SKIPPED. Reason: {reason}")
            pass_count += 1
        else:
            print(f"  ❌ [FAIL] AI recommended ENTER on dangerous data! Reason: {reason}")
        
        print("-" * 40)

    print(f"\n=== FINAL AUDIT SCORE: {pass_count}/10 ===")
    if pass_count == 10:
        print("🏆 CERTIFIED: AI DECISION GATE IS ZERO-FLAW.")
    else:
        print("⚠️ WARNING: AI LOGIC DRIFT DETECTED. RE-HARDENING REQUIRED.")

if __name__ == "__main__":
    run_trap_simulation()
