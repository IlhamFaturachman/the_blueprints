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
