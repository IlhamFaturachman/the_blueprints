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
    "new york":  r"\bnew york(?:\s+city)?\b|\bnyc\b",
    "chicago":   r"\bchicago\b|\bchi\b",  # \bchi\b is broad but safe within weather tag filter
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


# ---------------------------------------------------------------------------
# Logging Helper
# ---------------------------------------------------------------------------

def _log_unmatched(title, reason):
    """
    Append an unparseable market title to the log file for later review.
    The log helps improve the regex patterns over time.
    """
    os.makedirs("logs", exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {reason}: {title}\n")


# ---------------------------------------------------------------------------
# Layer 2: Parse Raw Market Dict into Structured Fields
# ---------------------------------------------------------------------------

def parse_market(raw):
    """
    Parse a raw Gamma API market dict into structured fields.

    Strategy:
    1. Match city against target list (regex, handles abbreviations)
    2. Extract temperature threshold + unit from question text
    3. Determine direction (above/below threshold)
    4. Extract YES price from outcomePrices field
    5. Extract token ID from tokens array
    6. Parse resolution date from endDate field

    Returns None if:
    - City not in target list (silent skip)
    - Cannot extract temperature (logged)
    - Missing price/token data (logged)
    - Resolves outside 3-day forecast window (silent skip)
    """
    question = raw.get("question") or raw.get("title") or ""
    question_lower = question.lower()

    # Step 1: Match city
    city = None
    for target_city, pattern in CITY_PATTERNS.items():
        if re.search(pattern, question_lower, re.IGNORECASE):
            city = target_city
            break

    if city is None:
        return None  # Not a target city — skip silently

    # Step 2: Extract temperature threshold and unit
    match = re.search(THRESHOLD_PATTERN, question, re.IGNORECASE)
    if not match:
        _log_unmatched(question, "no temperature threshold found")
        return None

    threshold = float(match.group(1))
    unit = match.group(2).upper()

    # Step 3: Determine direction
    below_keywords = r"\b(below|under|less than|cooler than|drop below|stay under)\b"
    direction = "below" if re.search(below_keywords, question_lower) else "above"

    # Step 4: Extract YES price from outcomePrices (JSON string or list)
    outcome_prices_raw = raw.get("outcomePrices")
    if not outcome_prices_raw:
        _log_unmatched(question, "missing outcomePrices")
        return None

    try:
        prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
        if not isinstance(prices, list) or len(prices) == 0:
            _log_unmatched(question, "outcomePrices is not a non-empty list")
            return None
        yes_price = float(prices[0])
    except (json.JSONDecodeError, ValueError, TypeError):
        _log_unmatched(question, "could not parse outcomePrices")
        return None

    # Step 5: Extract token ID (YES token is first in tokens array)
    tokens = raw.get("tokens", [])
    token_id = None
    if tokens:
        token_id = tokens[0].get("tokenId") or tokens[0].get("token_id")

    if not token_id:
        _log_unmatched(question, "missing token_id")
        return None

    # Step 6: Parse resolution date from endDate
    end_date_raw = raw.get("endDate") or raw.get("end_date") or ""
    try:
        end_dt = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00"))
        date_str = end_dt.strftime("%Y-%m-%d")
        now = datetime.now(timezone.utc)
        hours_until_resolve = (end_dt - now).total_seconds() / 3600
    except (ValueError, AttributeError):
        _log_unmatched(question, f"could not parse endDate: {end_date_raw}")
        return None

    # Only include markets within 3-day forecast window
    if hours_until_resolve <= 0 or hours_until_resolve > 72:
        return None

    return {
        "city": city,
        "date": date_str,
        "market_question": question,
        "threshold": threshold,
        "unit": unit,
        "direction": direction,
        "yes_price": yes_price,
        "token_id": token_id,
        "hours_until_resolve": round(hours_until_resolve, 1),
    }
