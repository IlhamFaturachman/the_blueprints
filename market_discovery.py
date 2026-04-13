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


def _env_bool(name, default=False):
    """Parse boolean environment variables using common truthy values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}

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

# Direction hints
EXACT_KEYWORDS = r"\b(exact(?:ly)?|equal(?:s| to)?|at exactly|precisely|on the dot)\b"
BELOW_KEYWORDS = r"\b(below|under|less than|cooler than|drop below|stay under)\b"
WEATHER_CONTEXT_PATTERN = r"\b(weather|forecast|temperature|temp|degrees?|rain|snow|humidity|hot|cold|heat|chill|high|low)\b"
DIRECTION_CANDIDATE_PATTERN = r"\b(above|below|under|over|exceed|reach|hit|drop|stay|exact(?:ly)?|equal(?:s| to)?)\b"

# Paper-trading strategy defaults (can be overridden via environment variables)
PAPER_STAKE_USD = float(os.getenv("PAPER_STAKE_USD", "100"))
HYBRID_TAKE_PROFIT_MIN_PRICE = float(os.getenv("HYBRID_TAKE_PROFIT_MIN_PRICE", "0.50"))
HYBRID_TAKE_PROFIT_MAX_PRICE = float(os.getenv("HYBRID_TAKE_PROFIT_MAX_PRICE", "0.60"))
HYBRID_STOP_LOSS_MULTIPLIER = float(os.getenv("HYBRID_STOP_LOSS_MULTIPLIER", "0.48"))
HYBRID_LATE_WINDOW_HOURS = float(os.getenv("HYBRID_LATE_WINDOW_HOURS", "2.0"))
HYBRID_MIN_CONFIDENCE_TO_HOLD = float(os.getenv("HYBRID_MIN_CONFIDENCE_TO_HOLD", "0.75"))
HYBRID_CONFIDENCE_EDGE_SCALE = float(os.getenv("HYBRID_CONFIDENCE_EDGE_SCALE", "0.35"))
PAPER_STATE_FILE = os.getenv("PAPER_STATE_FILE", "logs/paper_positions.json")
PAPER_MAX_OPEN_POSITIONS = int(os.getenv("PAPER_MAX_OPEN_POSITIONS", "3"))
PAPER_ENTRY_MIN_PRICE = float(os.getenv("PAPER_ENTRY_MIN_PRICE", "0.20"))
PAPER_ENTRY_MAX_PRICE = float(os.getenv("PAPER_ENTRY_MAX_PRICE", "0.30"))
PAPER_LOOP_INTERVAL_SECONDS = int(os.getenv("PAPER_LOOP_INTERVAL_SECONDS", "300"))

# Discovery + entry strategy (runtime-tunable)
DISCOVERY_MAX_FETCH_PAGES = int(os.getenv("DISCOVERY_MAX_FETCH_PAGES", "2"))
DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN = _env_bool("DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN", True)
DISCOVERY_AGGRESSIVE_SCAN_PAGES = int(os.getenv("DISCOVERY_AGGRESSIVE_SCAN_PAGES", "3"))
DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES = int(
    os.getenv("DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES", "3")
)
STRATEGY_MAX_YES_PRICE = float(os.getenv("STRATEGY_MAX_YES_PRICE", "0.35"))
STRATEGY_MIN_MODEL_PROB = float(os.getenv("STRATEGY_MIN_MODEL_PROB", "0.70"))
STRATEGY_MIN_EDGE = float(os.getenv("STRATEGY_MIN_EDGE", "0.35"))

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

def _extract_market_list(data):
    """Normalize Gamma API responses to a market list."""
    return data if isinstance(data, list) else data.get("markets", [])


def _market_search_text(raw):
    """Build combined searchable text from common market fields."""
    tags = raw.get("tags") or []
    tag_values = []

    for tag in tags if isinstance(tags, list) else []:
        if isinstance(tag, dict):
            tag_values.extend([
                str(tag.get("name") or ""),
                str(tag.get("slug") or ""),
                str(tag.get("label") or ""),
            ])
        else:
            tag_values.append(str(tag))

    fields = [
        raw.get("question"),
        raw.get("title"),
        raw.get("slug"),
        raw.get("description"),
        *tag_values,
    ]
    return " ".join(str(value) for value in fields if value)


def _has_target_city(text_lower):
    """Return True when text mentions one of the configured target cities."""
    return any(
        re.search(pattern, text_lower, re.IGNORECASE)
        for pattern in CITY_PATTERNS.values()
    )


def _is_temperature_market_candidate(raw):
    """Return True when market text looks like a city temperature contract."""
    text = _market_search_text(raw)
    text_lower = text.lower()

    has_city = _has_target_city(text_lower)
    has_threshold = bool(re.search(THRESHOLD_PATTERN, text, re.IGNORECASE))
    has_context = bool(re.search(WEATHER_CONTEXT_PATTERN, text_lower, re.IGNORECASE))
    has_direction = bool(re.search(DIRECTION_CANDIDATE_PATTERN, text_lower, re.IGNORECASE))

    return has_city and has_threshold and (has_context or has_direction)


def _dedupe_markets(markets):
    """Deduplicate markets by stable id-like fields while preserving order."""
    seen = set()
    unique = []

    for market in markets:
        key = (
            market.get("id")
            or market.get("conditionId")
            or f"{market.get('question')}::{market.get('endDate')}"
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(market)

    return unique

def fetch_markets(inspect=False, aggressive_scan=False):
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
    markets = _extract_market_list(data)

    if inspect:
        print("=== INSPECT MODE: First 3 raw market structures ===\n")
        for i, market in enumerate(markets[:3], 1):
            print(f"--- Market {i} ---")
            print(json.dumps(market, indent=2, default=str))
            print()
        sys.exit(0)

    candidates = [m for m in markets if _is_temperature_market_candidate(m)]
    if candidates:
        return _dedupe_markets(candidates)

    # Gamma weather tagging currently returns many unrelated markets;
    # scan a couple of additional pages via offset to improve candidate quality
    # without making normal CLI runs too slow.
    scanned = list(markets)
    page_limit = params["limit"]
    offset = page_limit

    for _ in range(max(0, DISCOVERY_MAX_FETCH_PAGES)):
        page_params = {**params, "offset": offset}
        try:
            page_data = fetch_with_retry(GAMMA_API, params=page_params, max_retries=1)
        except Exception:
            break

        page_markets = _extract_market_list(page_data)
        if not page_markets:
            break

        scanned.extend(page_markets)
        candidates = [m for m in scanned if _is_temperature_market_candidate(m)]
        if candidates:
            break

        offset += page_limit

    candidates = [m for m in scanned if _is_temperature_market_candidate(m)]
    if candidates:
        return _dedupe_markets(candidates)

    if aggressive_scan:
        broad_params = {
            "active": "true",
            "limit": 100,
        }
        broad_scanned = []
        broad_offset = 0

        for _ in range(max(1, DISCOVERY_AGGRESSIVE_SCAN_PAGES)):
            page_params = {**broad_params, "offset": broad_offset}
            try:
                broad_data = fetch_with_retry(GAMMA_API, params=page_params, max_retries=1)
            except Exception:
                break

            broad_page = _extract_market_list(broad_data)
            if not broad_page:
                break

            broad_scanned.extend(broad_page)
            candidates = [m for m in broad_scanned if _is_temperature_market_candidate(m)]
            if candidates:
                return _dedupe_markets(candidates)

            broad_offset += broad_params["limit"]

        if broad_scanned:
            return _dedupe_markets(broad_scanned)

    # Fallback: return everything scanned so parser can still discover
    # opportunities outside candidate heuristics.
    return _dedupe_markets(scanned)


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

    search_text = _market_search_text(raw)
    search_text_lower = search_text.lower()

    # Step 1: Match city
    city = None
    for target_city, pattern in CITY_PATTERNS.items():
        if re.search(pattern, search_text_lower, re.IGNORECASE):
            city = target_city
            break

    if city is None:
        return None  # Not a target city — skip silently

    has_weather_context = bool(re.search(WEATHER_CONTEXT_PATTERN, search_text_lower, re.IGNORECASE))
    has_temperature_hint = bool(re.search(THRESHOLD_PATTERN, search_text, re.IGNORECASE))

    # Skip non-weather city markets early to avoid noisy unmatched logs.
    if not has_weather_context and not has_temperature_hint:
        return None

    # Step 2: Extract temperature threshold and unit
    match = re.search(THRESHOLD_PATTERN, question, re.IGNORECASE)
    if not match:
        match = re.search(THRESHOLD_PATTERN, search_text, re.IGNORECASE)
    if not match:
        _log_unmatched(question, "no temperature threshold found")
        return None

    threshold = float(match.group(1))
    unit = match.group(2).upper()

    # Step 3: Determine direction
    if re.search(EXACT_KEYWORDS, search_text_lower, re.IGNORECASE):
        direction = "exact"
    elif re.search(BELOW_KEYWORDS, search_text_lower, re.IGNORECASE):
        direction = "below"
    else:
        direction = "above"

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
        "end_date": end_dt.isoformat(),
        "market_question": question,
        "threshold": threshold,
        "unit": unit,
        "direction": direction,
        "yes_price": yes_price,
        "token_id": token_id,
        "hours_until_resolve": round(hours_until_resolve, 1),
    }


# ---------------------------------------------------------------------------
# Layer 3: Fetch Weather Forecast from Open-Meteo
# ---------------------------------------------------------------------------

def fetch_forecast(city, date):
    """
    Fetch the daily max temperature forecast for a city on a specific date.

    Uses Open-Meteo free API — no authentication required.
    Fetches 3-day forecast in one request, returns temp for requested date.

    Returns temperature in °C (float), or None if:
    - City not in TARGET_CITIES
    - Requested date not in forecast window
    - Network failure after 3 retries
    """
    coords = TARGET_CITIES.get(city)
    if not coords:
        return None

    params = {
        "latitude": coords["lat"],
        "longitude": coords["lon"],
        "daily": "temperature_2m_max",
        "timezone": "auto",
        "forecast_days": 3,
    }

    try:
        data = fetch_with_retry(OPEN_METEO_API, params=params)
    except Exception:
        return None

    daily = data.get("daily", {})
    times = daily.get("time", [])
    temps = daily.get("temperature_2m_max", [])

    if date in times:
        return temps[times.index(date)]

    return None


# ---------------------------------------------------------------------------
# Layer 4: Calculate Model Probability and Edge
# ---------------------------------------------------------------------------

def calculate_edge(market, forecast_temp):
    """
    Calculate model probability and edge for a market.

    V1 model is intentionally simple:
    - above: 1.0 if forecast >= threshold else 0.0
    - below: 1.0 if forecast < threshold else 0.0
    - exact: unsupported in V1 (skip in main pipeline)
    """
    if forecast_temp is None:
        return None

    threshold = market["threshold"]
    unit = market["unit"]
    direction = market["direction"]

    # Open-Meteo is Celsius; convert only when market threshold is Fahrenheit.
    if unit == "F":
        forecast_converted = (forecast_temp * 9 / 5) + 32
    else:
        forecast_converted = forecast_temp

    if direction == "above":
        model_prob = 1.0 if forecast_converted >= threshold else 0.0
    elif direction == "below":
        model_prob = 1.0 if forecast_converted < threshold else 0.0
    else:
        return None

    edge = round(model_prob - market["yes_price"], 4)

    return {
        **market,
        "model_prob": model_prob,
        "edge": edge,
        "forecast_temp_c": round(float(forecast_temp), 1),
        "forecast_temp_converted": round(float(forecast_converted), 1),
    }


# ---------------------------------------------------------------------------
# Layer 5: Filter to High-Edge Opportunities
# ---------------------------------------------------------------------------

def filter_opportunities(
    markets,
    max_yes_price=STRATEGY_MAX_YES_PRICE,
    min_model_prob=STRATEGY_MIN_MODEL_PROB,
    min_edge=STRATEGY_MIN_EDGE,
):
    """
    Keep markets where YES is cheap and model probability is high.

    Default criteria (runtime-tunable via env):
    yes_price < 0.35, model_prob >= 0.70, edge >= 0.35
    Returns opportunities sorted by edge descending.
    """
    opportunities = [
        market for market in markets
        if market["yes_price"] < max_yes_price
        and market["model_prob"] >= min_model_prob
        and market["edge"] >= min_edge
    ]
    return sorted(opportunities, key=lambda item: item["edge"], reverse=True)


# ---------------------------------------------------------------------------
# Layer 6: Paper Trading Helpers (Hybrid Exit Strategy)
# ---------------------------------------------------------------------------

def _hours_until_resolve_from_end_date(end_date, now_utc=None):
    """Compute hours until resolve from ISO datetime string."""
    if not end_date:
        return None

    try:
        end_dt = datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
    except ValueError:
        return None

    now_dt = now_utc or datetime.now(timezone.utc)
    return (end_dt - now_dt).total_seconds() / 3600


def build_paper_position(opportunity, stake_usd=PAPER_STAKE_USD):
    """Create a paper position from an opportunity candidate."""
    entry_price = float(opportunity["yes_price"])
    if entry_price <= 0:
        raise ValueError("yes_price must be > 0")

    quantity = round(float(stake_usd) / entry_price, 6)
    cost_basis = round(quantity * entry_price, 4)

    return {
        "status": "open",
        "city": opportunity["city"],
        "token_id": opportunity["token_id"],
        "market_question": opportunity.get("market_question", ""),
        "direction": opportunity["direction"],
        "threshold": opportunity["threshold"],
        "unit": opportunity["unit"],
        "date": opportunity["date"],
        "end_date": opportunity.get("end_date"),
        "entry_price": entry_price,
        "quantity": quantity,
        "cost_basis": cost_basis,
        "target_price": HYBRID_TAKE_PROFIT_MIN_PRICE,
        "target_price_low": HYBRID_TAKE_PROFIT_MIN_PRICE,
        "target_price_high": HYBRID_TAKE_PROFIT_MAX_PRICE,
        "stop_loss_price": round(entry_price * HYBRID_STOP_LOSS_MULTIPLIER, 4),
        "entry_model_prob": opportunity.get("model_prob"),
        "entry_edge": opportunity.get("edge"),
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }


def _position_confidence_score(position, current_yes_price, forecast_still_valid):
    """Estimate confidence (0-1) that holding remains favorable."""
    if not forecast_still_valid:
        return 0.0

    base_prob = position.get("entry_model_prob")
    if base_prob is None:
        base_prob = 1.0
    base_prob = max(0.0, min(float(base_prob), 1.0))

    current_price = max(0.0, min(float(current_yes_price), 1.0))
    edge_now = max(base_prob - current_price, 0.0)
    edge_scale = HYBRID_CONFIDENCE_EDGE_SCALE if HYBRID_CONFIDENCE_EDGE_SCALE > 0 else 1.0
    edge_component = min(edge_now / edge_scale, 1.0)
    price_component = 1.0 - min(abs(current_price - 0.50) / 0.50, 1.0)

    score = (0.70 * base_prob) + (0.20 * edge_component) + (0.10 * price_component)
    return round(max(0.0, min(score, 1.0)), 4)


def evaluate_hybrid_exit(
    position,
    current_yes_price,
    forecast_still_valid,
    hours_until_resolve=None,
    now_utc=None,
    confidence_score=None,
):
    """
    Hybrid exit strategy:
    1) Take-profit when price reaches configured TP band (default 0.50+).
    2) Stop-loss when current price <= configured stop-loss price.
    3) If within late window (H-2 by default):
       - forecast valid + confidence >= min threshold -> hold to resolve
       - otherwise -> sell
    4) Otherwise hold and wait.
    """
    price = float(current_yes_price)
    target_price = float(position.get("target_price", HYBRID_TAKE_PROFIT_MIN_PRICE))
    tp_low = float(position.get("target_price_low", HYBRID_TAKE_PROFIT_MIN_PRICE))
    tp_high = float(position.get("target_price_high", HYBRID_TAKE_PROFIT_MAX_PRICE))
    stop_loss_price = float(position["stop_loss_price"])

    if confidence_score is None:
        confidence_score = 1.0 if forecast_still_valid else 0.0
    confidence_score = max(0.0, min(float(confidence_score), 1.0))

    if price <= stop_loss_price:
        return {
            "action": "sell",
            "reason": "stop_loss",
            "target_price": target_price,
            "confidence_score": confidence_score,
        }

    if price >= tp_low:
        reason = "take_profit_band" if price <= tp_high else "take_profit_band_breakout"
        return {
            "action": "sell",
            "reason": reason,
            "target_price": target_price,
            "confidence_score": confidence_score,
        }

    hours = hours_until_resolve
    if hours is None:
        hours = _hours_until_resolve_from_end_date(position.get("end_date"), now_utc=now_utc)

    if hours is not None and hours <= HYBRID_LATE_WINDOW_HOURS:
        if not forecast_still_valid:
            return {
                "action": "sell",
                "reason": "late_window_forecast_invalid",
                "target_price": target_price,
                "confidence_score": confidence_score,
            }

        if confidence_score >= HYBRID_MIN_CONFIDENCE_TO_HOLD:
            return {
                "action": "hold_to_resolve",
                "reason": "late_window_confidence_pass",
                "target_price": target_price,
                "confidence_score": confidence_score,
            }

        return {
            "action": "sell",
            "reason": "late_window_confidence_below_min",
            "target_price": target_price,
            "confidence_score": confidence_score,
        }

    return {
        "action": "hold",
        "reason": "await_target",
        "target_price": target_price,
        "confidence_score": confidence_score,
    }


def close_paper_position(position, exit_price, reason, now_utc=None):
    """Close an open paper position and compute realized PnL metrics."""
    closed = {**position}
    resolved_at = now_utc or datetime.now(timezone.utc)
    price = float(exit_price)
    exit_value = round(price * float(closed["quantity"]), 4)
    pnl_usd = round(exit_value - float(closed["cost_basis"]), 4)
    roi_pct = round((pnl_usd / float(closed["cost_basis"])) * 100, 4) if closed["cost_basis"] else 0.0

    closed.update(
        {
            "status": "closed",
            "last_price": price,
            "exit_price": price,
            "exit_value": exit_value,
            "realized_pnl_usd": pnl_usd,
            "realized_roi_pct": roi_pct,
            "closed_at": resolved_at.isoformat(),
            "close_reason": reason,
        }
    )

    return closed


def update_paper_position(
    position,
    current_yes_price,
    forecast_still_valid,
    hours_until_resolve=None,
    now_utc=None,
    confidence_score=None,
):
    """Apply hybrid exit decision and return updated position plus decision."""
    decision = evaluate_hybrid_exit(
        position=position,
        current_yes_price=current_yes_price,
        forecast_still_valid=forecast_still_valid,
        hours_until_resolve=hours_until_resolve,
        now_utc=now_utc,
        confidence_score=confidence_score,
    )

    updated = {
        **position,
        "last_price": float(current_yes_price),
        "last_confidence_score": decision.get("confidence_score"),
    }

    if decision["action"] == "sell":
        updated = close_paper_position(
            position=updated,
            exit_price=float(current_yes_price),
            reason=decision["reason"],
            now_utc=now_utc,
        )

    return updated, decision


def load_paper_state(path=PAPER_STATE_FILE):
    """Load paper-trading state from disk or return an empty state."""
    if not os.path.exists(path):
        return {"positions": [], "history": [], "updated_at": None}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"positions": [], "history": [], "updated_at": None}

    positions = data.get("positions", []) if isinstance(data, dict) else []
    history = data.get("history", []) if isinstance(data, dict) else []
    updated_at = data.get("updated_at") if isinstance(data, dict) else None
    meta = data.get("meta") if isinstance(data, dict) else None

    if not isinstance(positions, list):
        positions = []
    if not isinstance(history, list):
        history = []

    state = {
        "positions": positions,
        "history": history,
        "updated_at": updated_at,
    }

    if isinstance(meta, dict):
        state["meta"] = meta

    return state


def save_paper_state(state, path=PAPER_STATE_FILE):
    """Persist paper-trading state to disk."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def run_discovery_cycle(inspect=False, aggressive_scan=False):
    """Run one discovery cycle and return structured results."""
    markets_raw = fetch_markets(inspect=inspect, aggressive_scan=aggressive_scan)

    parsed = []
    skipped_markets = 0
    exact_skipped = 0

    for raw in markets_raw:
        parsed_market = parse_market(raw)
        if not parsed_market:
            skipped_markets += 1
            continue

        if parsed_market["direction"] == "exact":
            exact_skipped += 1
            continue

        parsed.append(parsed_market)

    failed_cities = []
    enriched = []

    for market in parsed:
        city = market["city"]
        date = market["date"]
        forecast_temp = fetch_forecast(city, date)

        if forecast_temp is None:
            if city not in failed_cities:
                failed_cities.append(city)
            continue

        edge_result = calculate_edge(market, forecast_temp)
        if edge_result:
            enriched.append(edge_result)

    opportunities = filter_opportunities(enriched)

    return {
        "markets_raw": markets_raw,
        "parsed": parsed,
        "enriched": enriched,
        "opportunities": opportunities,
        "failed_cities": failed_cities,
        "skipped_markets": skipped_markets,
        "exact_skipped": exact_skipped,
        "aggressive_scan": aggressive_scan,
    }


def build_discovery_diagnostics(discovery):
    """Build drop-off statistics to debug why opportunities are zero."""
    raw_markets = discovery.get("markets_raw", [])
    sample_rejections = []

    counts = {
        "raw_total": len(raw_markets),
        "city_match": 0,
        "temperature_hint": 0,
        "weather_context": 0,
        "direction_hint": 0,
        "temperature_candidates": 0,
        "parsed": len(discovery.get("parsed", [])),
        "enriched": len(discovery.get("enriched", [])),
        "opportunities": len(discovery.get("opportunities", [])),
    }

    for raw in raw_markets:
        question = raw.get("question") or raw.get("title") or ""
        text = _market_search_text(raw)
        lower = text.lower()

        has_city = _has_target_city(lower)
        has_threshold = bool(re.search(THRESHOLD_PATTERN, text, re.IGNORECASE))
        has_weather = bool(re.search(WEATHER_CONTEXT_PATTERN, lower, re.IGNORECASE))
        has_direction = bool(re.search(DIRECTION_CANDIDATE_PATTERN, lower, re.IGNORECASE))

        if has_city:
            counts["city_match"] += 1
        if has_threshold:
            counts["temperature_hint"] += 1
        if has_weather:
            counts["weather_context"] += 1
        if has_direction:
            counts["direction_hint"] += 1

        is_candidate = has_city and has_threshold and (has_weather or has_direction)
        if is_candidate:
            counts["temperature_candidates"] += 1
        elif len(sample_rejections) < 5 and question:
            if not has_city:
                reason = "no_city"
            elif not has_threshold:
                reason = "no_threshold"
            elif not (has_weather or has_direction):
                reason = "no_weather_context"
            else:
                reason = "other"
            sample_rejections.append({"reason": reason, "question": question})

    return {
        **counts,
        "failed_cities": list(discovery.get("failed_cities", [])),
        "skipped_markets": discovery.get("skipped_markets", 0),
        "exact_skipped": discovery.get("exact_skipped", 0),
        "sample_rejections": sample_rejections,
    }


def print_discovery_diagnostics(discovery, aggressive_scan=False):
    """Print a concise per-stage drop-off report for discovery debugging."""
    diag = build_discovery_diagnostics(discovery)

    print(f"\n{'=' * 49}")
    print("  DISCOVERY DIAGNOSTICS")
    print(f"{'=' * 49}")
    print(f"  Aggressive scan      : {'on' if aggressive_scan else 'off'}")
    print(f"  Raw markets          : {diag['raw_total']}")
    print(f"  City match           : {diag['city_match']}")
    print(f"  Temperature hint     : {diag['temperature_hint']}")
    print(f"  Weather context      : {diag['weather_context']}")
    print(f"  Direction hint       : {diag['direction_hint']}")
    print(f"  Temp candidates      : {diag['temperature_candidates']}")
    print(f"  Parsed               : {diag['parsed']}")
    print(f"  Enriched             : {diag['enriched']}")
    print(f"  Opportunities        : {diag['opportunities']}")
    print(f"  Skipped              : {diag['skipped_markets']}")
    print(f"  Exact skipped        : {diag['exact_skipped']}")

    failed_cities = diag["failed_cities"]
    if failed_cities:
        names = ", ".join(city.title() for city in failed_cities)
        print(f"  Forecast failures    : {names}")

    if diag["sample_rejections"]:
        print("  Rejection samples    :")
        for sample in diag["sample_rejections"]:
            snippet = sample["question"][:120]
            print(f"    - [{sample['reason']}] {snippet}")

    print(f"{'=' * 49}")


def _position_to_market(position, current_yes_price, hours_until_resolve):
    """Build calculate_edge-compatible market dict from an open position."""
    return {
        "city": position["city"],
        "date": position["date"],
        "end_date": position.get("end_date"),
        "market_question": position.get("market_question", ""),
        "threshold": position["threshold"],
        "unit": position["unit"],
        "direction": position["direction"],
        "yes_price": float(current_yes_price),
        "token_id": position["token_id"],
        "hours_until_resolve": hours_until_resolve if hours_until_resolve is not None else position.get("hours_until_resolve"),
    }


def _forecast_still_valid(position, current_yes_price, hours_until_resolve):
    """Evaluate whether forecast still supports the open position thesis."""
    forecast_temp = fetch_forecast(position["city"], position["date"])
    if forecast_temp is None:
        return False

    market_view = _position_to_market(position, current_yes_price, hours_until_resolve)
    edge_result = calculate_edge(market_view, forecast_temp)
    return bool(edge_result and edge_result["model_prob"] >= 0.70)


def run_paper_trading_cycle(
    min_price=None,
    max_price=None,
    stake_usd=PAPER_STAKE_USD,
    state_path=PAPER_STATE_FILE,
    force_aggressive_scan=False,
):
    """Run one paper-trading cycle: discover, manage exits, open new positions."""
    now = datetime.now(timezone.utc)
    state = load_paper_state(path=state_path)

    state_meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    empty_temperature_cycles = int(state_meta.get("empty_temperature_cycles", 0) or 0)

    auto_aggressive = (
        DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN
        and empty_temperature_cycles >= max(0, DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES)
    )
    use_aggressive_scan = force_aggressive_scan or auto_aggressive
    discovery = run_discovery_cycle(inspect=False, aggressive_scan=use_aggressive_scan)

    market_by_token = {m["token_id"]: m for m in discovery["parsed"] if m.get("token_id")}
    next_open_positions = []
    next_history = list(state.get("history", []))
    closed_this_cycle = []

    for position in state.get("positions", []):
        if position.get("status") != "open":
            next_history.append(position)
            continue

        token_id = position.get("token_id")
        live_market = market_by_token.get(token_id)

        if not live_market:
            next_open_positions.append(position)
            continue

        current_yes_price = float(live_market["yes_price"])
        hours_until_resolve = live_market.get("hours_until_resolve")
        forecast_valid = _forecast_still_valid(position, current_yes_price, hours_until_resolve)
        confidence_score = _position_confidence_score(position, current_yes_price, forecast_valid)

        updated_position, decision = update_paper_position(
            position=position,
            current_yes_price=current_yes_price,
            forecast_still_valid=forecast_valid,
            hours_until_resolve=hours_until_resolve,
            now_utc=now,
            confidence_score=confidence_score,
        )

        if decision["action"] == "hold_to_resolve" and hours_until_resolve is not None and hours_until_resolve <= 0:
            settle_price = 1.0 if forecast_valid else 0.0
            updated_position = close_paper_position(
                position=updated_position,
                exit_price=settle_price,
                reason="resolved_after_hold",
                now_utc=now,
            )

        if updated_position.get("status") == "closed":
            closed_this_cycle.append(updated_position)
            next_history.append(updated_position)
        else:
            next_open_positions.append(updated_position)

    min_bound = PAPER_ENTRY_MIN_PRICE if min_price is None else float(min_price)
    max_bound = PAPER_ENTRY_MAX_PRICE if max_price is None else float(max_price)

    open_token_ids = {p.get("token_id") for p in next_open_positions if p.get("status") == "open"}
    available_slots = max(PAPER_MAX_OPEN_POSITIONS - len(open_token_ids), 0)
    opened_this_cycle = []

    for opportunity in discovery["opportunities"]:
        if available_slots <= 0:
            break

        token_id = opportunity.get("token_id")
        price = float(opportunity["yes_price"])

        if token_id in open_token_ids:
            continue
        if price < min_bound or price > max_bound:
            continue

        position = build_paper_position(opportunity, stake_usd=stake_usd)

        next_open_positions.append(position)
        opened_this_cycle.append(position)
        open_token_ids.add(token_id)
        available_slots -= 1

    if discovery["parsed"]:
        empty_temperature_cycles = 0
    else:
        empty_temperature_cycles += 1

    next_state = {
        "positions": next_open_positions,
        "history": next_history,
        "updated_at": now.isoformat(),
        "meta": {
            "empty_temperature_cycles": empty_temperature_cycles,
        },
    }
    save_paper_state(next_state, path=state_path)

    return {
        "opened": opened_this_cycle,
        "closed": closed_this_cycle,
        "open_positions": next_open_positions,
        "state_path": state_path,
        "discovery": discovery,
        "min_bound": min_bound,
        "max_bound": max_bound,
        "used_aggressive_scan": use_aggressive_scan,
        "empty_temperature_cycles": empty_temperature_cycles,
    }


def print_paper_cycle_summary(cycle):
    """Print concise paper-trading cycle summary."""
    discovery = cycle["discovery"]

    print(f"\n{'=' * 43}")
    print("  PAPER TRADING CYCLE")
    print(f"{'=' * 43}")
    print(f"  Opportunities      : {len(discovery['opportunities'])}")
    print(f"  Opened this cycle  : {len(cycle['opened'])}")
    print(f"  Closed this cycle  : {len(cycle['closed'])}")
    print(f"  Open positions now : {len(cycle['open_positions'])}")
    print(f"  Entry bounds       : {cycle['min_bound']:.4f} - {cycle['max_bound']:.4f}")
    print(f"  Aggressive scan    : {'on' if cycle.get('used_aggressive_scan') else 'off'}")
    print(f"  Empty temp cycles  : {cycle.get('empty_temperature_cycles', 0)}")
    print(f"  State file         : {cycle['state_path']}")
    print(f"{'=' * 43}")

    if cycle["opened"]:
        print("  Opened tokens      :")
        for position in cycle["opened"]:
            print(f"    - {position['token_id']} @ {position['entry_price']:.4f}")

    if cycle["closed"]:
        print("  Closed tokens      :")
        for position in cycle["closed"]:
            print(
                f"    - {position['token_id']} @ {position['exit_price']:.4f} "
                f"({position['close_reason']}, PnL {position['realized_pnl_usd']:+.4f})"
            )


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def print_opportunities(opportunities):
    """Print a readable opportunity list."""
    if not opportunities:
        print("\nNo opportunities found matching criteria.")
        return

    print(f"\n{'=' * 57}")
    print(f"  OPPORTUNITIES FOUND: {len(opportunities)}")
    print(f"{'=' * 57}")

    for i, opp in enumerate(opportunities, 1):
        print(f"\n[{i}] {opp['city'].title()} — {opp['market_question']}")
        print(f"    YES price  : {opp['yes_price']:.2f}")
        print(f"    Model prob : {opp['model_prob']:.2f}")
        print(f"    Edge       : {opp['edge']:+.4f}")
        print(
            f"    Forecast   : {opp['forecast_temp_converted']:.1f}°{opp['unit']} "
            f"(threshold: {opp['threshold']}°{opp['unit']}, {opp['direction']})"
        )
        print(f"    Token ID   : {opp['token_id']}")
        print(f"    Resolves   : {opp['hours_until_resolve']}h")


def print_summary(
    total_cities,
    failed_cities,
    total_markets,
    parsed_markets,
    skipped_markets,
    exact_skipped,
    opportunities_count,
):
    """Print run-level coverage and result counts."""
    success_count = total_cities - len(failed_cities)
    failed_str = ""
    if failed_cities:
        names = ", ".join(city.title() for city in failed_cities)
        failed_str = f"  ({names}: timeout/forecast unavailable)"

    print(f"\n{'=' * 37}")
    print("  RUN SUMMARY")
    print(f"{'=' * 37}")
    print(f"  Cities fetched  : {success_count}/{total_cities}{failed_str}")
    print(f"  Markets parsed  : {parsed_markets}/{total_markets}  ({skipped_markets} skipped)")
    print(f"  Exact skipped   : {exact_skipped}")
    print(f"  Opportunities   : {opportunities_count}")
    print(f"{'=' * 37}\n")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def main():
    """Run discovery mode or paper-trading mode based on CLI flags."""
    inspect_mode = "--inspect" in sys.argv
    paper_mode = "--paper" in sys.argv
    paper_loop_mode = "--paper-loop" in sys.argv
    diagnose_mode = "--diagnose" in sys.argv
    aggressive_mode = "--aggressive" in sys.argv

    if paper_loop_mode:
        print(f"Starting paper loop every {PAPER_LOOP_INTERVAL_SECONDS}s. Press Ctrl+C to stop.")
        try:
            while True:
                cycle = run_paper_trading_cycle(force_aggressive_scan=aggressive_mode)
                print_paper_cycle_summary(cycle)
                time.sleep(PAPER_LOOP_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nPaper loop stopped.")
        return

    if paper_mode:
        cycle = run_paper_trading_cycle(force_aggressive_scan=aggressive_mode)
        print_paper_cycle_summary(cycle)
        return

    discovery = run_discovery_cycle(inspect=inspect_mode, aggressive_scan=aggressive_mode)

    if diagnose_mode:
        print_discovery_diagnostics(discovery, aggressive_scan=aggressive_mode)

    print_opportunities(discovery["opportunities"])
    print_summary(
        total_cities=len(TARGET_CITIES),
        failed_cities=discovery["failed_cities"],
        total_markets=len(discovery["markets_raw"]),
        parsed_markets=len(discovery["parsed"]),
        skipped_markets=discovery["skipped_markets"],
        exact_skipped=discovery["exact_skipped"],
        opportunities_count=len(discovery["opportunities"]),
    )


if __name__ == "__main__":
    main()
