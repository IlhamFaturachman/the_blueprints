"""
market_discovery.py — Polymarket weather market discovery tool.

Usage:
    python market_discovery.py --inspect   # dump raw API samples and exit
    python market_discovery.py             # run full discovery

Strategy: surface weather markets where YES price < 0.35 and
forecast implies >= 70% probability (edge >= 0.35).
"""

import os
import sys
import time
import json
import re
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAMMA_API = "https://gamma-api.polymarket.com/markets"
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
LOG_FILE = "logs/unmatched_markets.log"

# Target cities with hardcoded coordinates (lat, lon)
TARGET_CITIES = {
    "new york":  {"lat": 40.7128,  "lon": -74.0060},
    "chicago":   {"lat": 41.8781,  "lon": -87.6298},
    "london":    {"lat": 51.5074,  "lon":  -0.1278},
    "tokyo":     {"lat": 35.6762,  "lon": 139.6503},
    "hong kong": {"lat": 22.3193,  "lon": 114.1694},
    "miami":     {"lat": 25.7617,  "lon": -80.1918},
    "sydney":    {"lat": -33.8688, "lon": 151.2093},
    "toronto":   {"lat": 43.6532,  "lon": -79.3832},
}

# Regex patterns for matching city names (handles abbreviations/variants)
CITY_PATTERNS = {
    "new york":  r"\bnew york(?:\s+city)?\b|\bnyc\b|\bny\b",
    "chicago":   r"\bchicago\b|\bchi\b",
    "london":    r"\blondon\b",
    "tokyo":     r"\btokyo\b",
    "hong kong": r"\bhong kong\b|\bhk\b|\bhkg\b",
    "miami":     r"\bmiami\b",
    "sydney":    r"\bsydney\b",
    "toronto":   r"\btoronto\b",
}

# Matches: "75°F", "80F", "25°C", "30 C", "75 degrees F"
THRESHOLD_PATTERN = r"(\d+(?:\.\d+)?)\s*(?:degrees?\s*)?(?:°\s*)?([FC])\b"

# ---------------------------------------------------------------------------
# HTTP Utility
# ---------------------------------------------------------------------------

def fetch_with_retry(url, params=None, max_retries=3):
    """
    GET a URL and return parsed JSON. Retries up to max_retries times
    with exponential backoff (1s, 2s, 4s) on any request error.
    Raises the last exception if all retries are exhausted.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise last_error


# ---------------------------------------------------------------------------
# Layer 1: Fetch Markets from Polymarket Gamma API
# ---------------------------------------------------------------------------

def fetch_markets(inspect=False):
    """
    Fetch active weather markets from the Polymarket Gamma API.

    If inspect=True, prints the first 3 raw market dicts as formatted JSON
    and exits. Use this once to understand the API response structure
    before building the parser.

    Returns a list of raw market dicts on success.
    Exits with code 1 if the API is unreachable after 3 retries.
    """
    params = {
        "tag": "weather",
        "active": "true",
        "limit": 100,
    }

    try:
        data = fetch_with_retry(GAMMA_API, params=params)
    except Exception as e:
        print(f"\nERROR: Could not fetch markets from Gamma API: {e}")
        print("Check your internet connection and try again.")
        sys.exit(1)

    # API returns either a list directly or {"markets": [...]}
    markets = data if isinstance(data, list) else data.get("markets", [])

    if inspect:
        print("=== INSPECT MODE: First 3 raw market structures ===\n")
        for i, market in enumerate(markets[:3], 1):
            print(f"--- Market {i} ---")
            print(json.dumps(market, indent=2, default=str))
            print()
        sys.exit(0)

    return markets
