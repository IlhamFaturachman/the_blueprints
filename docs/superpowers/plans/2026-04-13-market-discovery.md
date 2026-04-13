# Market Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `market_discovery.py` — a CLI tool that fetches Polymarket weather markets, compares them against Open-Meteo forecasts, and prints high-edge opportunities where YES price < 0.35 and model probability >= 0.70.

**Architecture:** Single file, modular per layer. Seven pure functions (`fetch_with_retry`, `fetch_markets`, `parse_market`, `fetch_forecast`, `calculate_edge`, `filter_opportunities`, print helpers) orchestrated by `main()`. Tests live in `tests/`. No shared state between functions.

**Tech Stack:** Python 3.10+, `requests`, `python-dotenv`, `pytest` (tests only)

---

## Execution Snapshot (2026-04-13)

- Implemented all planned runtime functions in `market_discovery.py` including orchestrator `main()`.
- Added missing tests: `tests/test_calculate_edge.py`, `tests/test_filter.py`, `tests/test_main.py`.
- Direction rule finalized:
    - `above`: `forecast >= threshold`
    - `below`: `forecast < threshold` (strict)
    - `exact`: parsed but skipped in V1 scoring pipeline
- Validation complete:
    - `pytest -q` => `50 passed`
    - CLI smoke tests:
        - `python market_discovery.py --inspect` works
        - `python market_discovery.py` runs end-to-end and prints summary

---

## File Map

| File | Responsibility |
|------|---------------|
| `market_discovery.py` | All discovery logic — constants, 7 functions, `main()` |
| `requirements.txt` | `requests`, `python-dotenv`, `pytest` |
| `.env.example` | Template for future API keys |
| `.env` | Local secrets (gitignored) |
| `logs/unmatched_markets.log` | Auto-created; raw titles that failed parsing |
| `tests/__init__.py` | Empty — makes tests/ a package |
| `tests/test_fetch_with_retry.py` | Tests for retry utility |
| `tests/test_fetch_markets.py` | Tests for Gamma API fetch |
| `tests/test_parse_market.py` | Tests for title parser |
| `tests/test_fetch_forecast.py` | Tests for Open-Meteo fetch |
| `tests/test_calculate_edge.py` | Tests for edge calculator |
| `tests/test_filter.py` | Tests for filter + output |
| `tests/test_main.py` | Tests for orchestrator flow |

---

## Task 1: Project Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.env`
- Create: `tests/__init__.py`
- Create: `market_discovery.py` (skeleton — imports + constants only)

- [ ] **Step 1: Create requirements.txt**

```
requests==2.31.0
python-dotenv==1.0.0
pytest==7.4.0
```

- [ ] **Step 2: Create .env.example**

```
# Polymarket CLOB API key — required for trading execution (not needed for discovery)
POLYMARKET_API_KEY=

# Open-Meteo does not require authentication
```

- [ ] **Step 3: Create .env (gitignored)**

```
POLYMARKET_API_KEY=
```

- [ ] **Step 4: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: Successfully installed requests, python-dotenv, pytest

- [ ] **Step 5: Create tests/__init__.py**

Empty file. Run: `touch tests/__init__.py`

- [ ] **Step 6: Create market_discovery.py skeleton**

```python
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
```

- [ ] **Step 7: Verify import works**

Run: `python -c "import market_discovery; print('OK')"`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git init
echo ".env" >> .gitignore
echo "logs/" >> .gitignore
echo "__pycache__/" >> .gitignore
echo ".pytest_cache/" >> .gitignore
git add requirements.txt .env.example .gitignore tests/__init__.py market_discovery.py
git commit -m "feat: project scaffold for market_discovery"
```

---

## Task 2: fetch_with_retry() — HTTP Utility with Exponential Backoff

**Files:**
- Modify: `market_discovery.py` — add `fetch_with_retry()`
- Create: `tests/test_fetch_with_retry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetch_with_retry.py`:

```python
import pytest
import requests
from unittest.mock import patch, MagicMock
from market_discovery import fetch_with_retry


def _mock_success(data):
    """Helper: create a mock response that returns data as JSON."""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


def _mock_failure():
    """Helper: create a mock response that raises RequestException."""
    resp = MagicMock()
    resp.raise_for_status.side_effect = requests.RequestException("timeout")
    return resp


def test_succeeds_on_first_try():
    with patch("market_discovery.requests.get", return_value=_mock_success({"data": "ok"})):
        result = fetch_with_retry("http://example.com")
    assert result == {"data": "ok"}


def test_retries_on_failure_then_succeeds():
    with patch("market_discovery.requests.get", side_effect=[_mock_failure(), _mock_success({"data": "ok"})]):
        with patch("market_discovery.time.sleep"):
            result = fetch_with_retry("http://example.com")
    assert result == {"data": "ok"}


def test_raises_after_max_retries():
    with patch("market_discovery.requests.get", return_value=_mock_failure()):
        with patch("market_discovery.time.sleep"):
            with pytest.raises(requests.RequestException):
                fetch_with_retry("http://example.com", max_retries=3)


def test_exponential_backoff_timing():
    """Verify sleep durations: 1s before retry 2, 2s before retry 3."""
    with patch("market_discovery.requests.get", return_value=_mock_failure()):
        with patch("market_discovery.time.sleep") as mock_sleep:
            with pytest.raises(requests.RequestException):
                fetch_with_retry("http://example.com", max_retries=3)
    sleep_calls = [c[0][0] for c in mock_sleep.call_args_list]
    assert sleep_calls == [1, 2]


def test_passes_params_to_get():
    params = {"tag": "weather", "active": "true"}
    with patch("market_discovery.requests.get", return_value=_mock_success([])) as mock_get:
        fetch_with_retry("http://example.com", params=params)
    mock_get.assert_called_once_with("http://example.com", params=params, timeout=10)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_with_retry.py -v`
Expected: `ImportError` or `AttributeError` — `fetch_with_retry` not defined yet

- [ ] **Step 3: Implement fetch_with_retry() in market_discovery.py**

Add after the constants block:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_with_retry.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add market_discovery.py tests/test_fetch_with_retry.py
git commit -m "feat: add fetch_with_retry with exponential backoff"
```

---

## Task 3: fetch_markets() + --inspect Mode

**Files:**
- Modify: `market_discovery.py` — add `fetch_markets()`
- Create: `tests/test_fetch_markets.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetch_markets.py`:

```python
import pytest
from unittest.mock import patch
from market_discovery import fetch_markets


SAMPLE_MARKETS = [
    {"question": "Will New York hit 80°F?", "active": True},
    {"question": "Will Chicago hit 75°F?", "active": True},
]


def test_returns_list_from_array_response():
    with patch("market_discovery.fetch_with_retry", return_value=SAMPLE_MARKETS):
        result = fetch_markets()
    assert isinstance(result, list)
    assert len(result) == 2


def test_returns_list_from_dict_response():
    """Gamma API sometimes wraps results in {"markets": [...]}."""
    with patch("market_discovery.fetch_with_retry", return_value={"markets": SAMPLE_MARKETS}):
        result = fetch_markets()
    assert len(result) == 2


def test_exits_on_fetch_failure():
    with patch("market_discovery.fetch_with_retry", side_effect=Exception("API down")):
        with pytest.raises(SystemExit) as exc_info:
            fetch_markets()
    assert exc_info.value.code == 1


def test_inspect_mode_exits_after_printing(capsys):
    with patch("market_discovery.fetch_with_retry", return_value=SAMPLE_MARKETS):
        with pytest.raises(SystemExit) as exc_info:
            fetch_markets(inspect=True)
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "INSPECT MODE" in captured.out


def test_fetch_uses_correct_params():
    with patch("market_discovery.fetch_with_retry", return_value=[]) as mock_fetch:
        fetch_markets()
    call_kwargs = mock_fetch.call_args
    params = call_kwargs[1]["params"] if call_kwargs[1] else call_kwargs[0][1]
    assert params.get("tag") == "weather"
    assert str(params.get("active")).lower() == "true"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_markets.py -v`
Expected: `ImportError` — `fetch_markets` not defined

- [ ] **Step 3: Implement fetch_markets() in market_discovery.py**

Add after `fetch_with_retry`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_markets.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add market_discovery.py tests/test_fetch_markets.py
git commit -m "feat: add fetch_markets with inspect mode"
```

---

## Task 4: parse_market() — Title Parser

**Files:**
- Modify: `market_discovery.py` — add `_log_unmatched()` and `parse_market()`
- Create: `tests/test_parse_market.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parse_market.py`:

```python
import json
import pytest
from unittest.mock import patch
from market_discovery import parse_market


def make_raw(question, prices=None, tokens=None, end_date=None):
    """Helper: build a minimal raw market dict."""
    return {
        "question": question,
        "outcomePrices": json.dumps(prices or ["0.28", "0.72"]),
        "tokens": tokens or [{"tokenId": "0xabc123"}],
        "endDate": end_date or "2026-04-15T12:00:00Z",
    }


# --- City matching ---

def test_parses_new_york_full_name():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F on April 15?"))
    assert result["city"] == "new york"


def test_parses_nyc_abbreviation():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will NYC reach 75°F on April 15?"))
    assert result["city"] == "new york"


def test_parses_hong_kong():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will Hong Kong exceed 30°C on April 15?"))
    assert result["city"] == "hong kong"


def test_non_target_city_returns_none_silently():
    # Paris is not in target list — should return None without logging
    with patch("market_discovery._log_unmatched") as mock_log:
        result = parse_market(make_raw("Will Paris reach 25°C on April 15?"))
    assert result is None
    mock_log.assert_not_called()


# --- Threshold and unit extraction ---

def test_extracts_fahrenheit_threshold():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York exceed 75°F on April 15?"))
    assert result["threshold"] == 75.0
    assert result["unit"] == "F"


def test_extracts_celsius_threshold():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will Tokyo reach 30°C on April 15?"))
    assert result["threshold"] == 30.0
    assert result["unit"] == "C"


def test_no_temperature_logs_unmatched_and_returns_none():
    with patch("market_discovery._log_unmatched") as mock_log:
        result = parse_market(make_raw("Will it rain in New York on April 15?"))
    assert result is None
    mock_log.assert_called_once()


# --- Direction extraction ---

def test_default_direction_is_above():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York exceed 75°F on April 15?"))
    assert result["direction"] == "above"


def test_below_direction_detected():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York drop below 60°F on April 15?"))
    assert result["direction"] == "below"


def test_under_direction_detected():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York stay under 65°F on April 15?"))
    assert result["direction"] == "below"


# --- Price and token extraction ---

def test_extracts_yes_price():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", prices=["0.32", "0.68"]))
    assert result["yes_price"] == pytest.approx(0.32)


def test_extracts_token_id():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", tokens=[{"tokenId": "0xdeadbeef"}]))
    assert result["token_id"] == "0xdeadbeef"


def test_missing_outcome_prices_logs_and_returns_none():
    raw = make_raw("Will New York hit 80°F?")
    raw["outcomePrices"] = None
    with patch("market_discovery._log_unmatched") as mock_log:
        result = parse_market(raw)
    assert result is None
    mock_log.assert_called_once()


# --- Date and resolution ---

def test_extracts_date_from_end_date():
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", end_date="2026-04-15T18:00:00Z"))
    assert result["date"] == "2026-04-15"


def test_market_outside_72h_returns_none():
    """Markets resolving more than 3 days out are outside forecast window."""
    with patch("market_discovery._log_unmatched"):
        result = parse_market(make_raw("Will New York hit 80°F?", end_date="2030-01-01T12:00:00Z"))
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parse_market.py -v`
Expected: `ImportError` — `parse_market` and `_log_unmatched` not defined

- [ ] **Step 3: Implement _log_unmatched() and parse_market() in market_discovery.py**

Add after `fetch_markets`:

```python
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
        yes_price = float(prices[0])
    except (json.JSONDecodeError, IndexError, ValueError, TypeError):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parse_market.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add market_discovery.py tests/test_parse_market.py
git commit -m "feat: add parse_market with city/threshold/direction extraction"
```

---

## Task 5: fetch_forecast() — Open-Meteo Integration

**Files:**
- Modify: `market_discovery.py` — add `fetch_forecast()`
- Create: `tests/test_fetch_forecast.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetch_forecast.py`:

```python
import pytest
from unittest.mock import patch
from market_discovery import fetch_forecast


MOCK_FORECAST = {
    "daily": {
        "time": ["2026-04-13", "2026-04-14", "2026-04-15"],
        "temperature_2m_max": [20.5, 22.1, 18.3],
    }
}


def test_returns_temp_for_requested_date():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("new york", "2026-04-14")
    assert result == 22.1


def test_returns_first_day_temp():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("chicago", "2026-04-13")
    assert result == 20.5


def test_returns_none_for_unknown_city():
    """City not in TARGET_CITIES — no API call should be made."""
    with patch("market_discovery.fetch_with_retry") as mock_fetch:
        result = fetch_forecast("paris", "2026-04-14")
    assert result is None
    mock_fetch.assert_not_called()


def test_returns_none_when_date_not_in_forecast():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST):
        result = fetch_forecast("new york", "2026-04-20")
    assert result is None


def test_returns_none_on_network_failure():
    with patch("market_discovery.fetch_with_retry", side_effect=Exception("timeout")):
        result = fetch_forecast("new york", "2026-04-14")
    assert result is None


def test_sends_correct_coordinates_for_tokyo():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST) as mock_fetch:
        fetch_forecast("tokyo", "2026-04-13")
    call_params = mock_fetch.call_args[1]["params"]
    assert call_params["latitude"] == 35.6762
    assert call_params["longitude"] == 139.6503


def test_requests_3_forecast_days():
    with patch("market_discovery.fetch_with_retry", return_value=MOCK_FORECAST) as mock_fetch:
        fetch_forecast("london", "2026-04-13")
    call_params = mock_fetch.call_args[1]["params"]
    assert call_params["forecast_days"] == 3
    assert call_params["daily"] == "temperature_2m_max"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch_forecast.py -v`
Expected: `ImportError` — `fetch_forecast` not defined

- [ ] **Step 3: Implement fetch_forecast() in market_discovery.py**

Add after `parse_market`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch_forecast.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add market_discovery.py tests/test_fetch_forecast.py
git commit -m "feat: add fetch_forecast via Open-Meteo API"
```

---

## Task 6: calculate_edge() — Unit Conversion + Probability Model

**Files:**
- Modify: `market_discovery.py` — add `calculate_edge()`
- Create: `tests/test_calculate_edge.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_calculate_edge.py`:

```python
import pytest
from market_discovery import calculate_edge


def make_market(threshold, unit, direction, yes_price):
    """Helper: build a minimal parsed market dict."""
    return {
        "city": "new york",
        "date": "2026-04-15",
        "market_question": "test question",
        "threshold": threshold,
        "unit": unit,
        "direction": direction,
        "yes_price": yes_price,
        "token_id": "0xabc",
        "hours_until_resolve": 18.0,
    }


# --- Unit conversion ---

def test_converts_celsius_forecast_to_fahrenheit():
    # 25°C = 77°F — market threshold 75°F, direction above → prob 1.0
    market = make_market(75.0, "F", "above", 0.28)
    result = calculate_edge(market, 25.0)
    assert result["forecast_temp_converted"] == pytest.approx(77.0, abs=0.1)


def test_no_conversion_for_celsius_market():
    market = make_market(25.0, "C", "above", 0.25)
    result = calculate_edge(market, 27.0)
    assert result["forecast_temp_converted"] == 27.0


# --- Probability model (above direction) ---

def test_prob_1_when_forecast_above_threshold_fahrenheit():
    # 25°C = 77°F > 75°F threshold
    market = make_market(75.0, "F", "above", 0.28)
    result = calculate_edge(market, 25.0)
    assert result["model_prob"] == 1.0


def test_prob_0_when_forecast_below_threshold_fahrenheit():
    # 20°C = 68°F < 75°F threshold
    market = make_market(75.0, "F", "above", 0.28)
    result = calculate_edge(market, 20.0)
    assert result["model_prob"] == 0.0


def test_prob_1_when_forecast_exactly_at_threshold():
    # 25°C = 77°F, threshold = 77°F — at threshold counts as "above"
    market = make_market(77.0, "F", "above", 0.28)
    result = calculate_edge(market, 25.0)
    assert result["model_prob"] == 1.0


# --- Probability model (below direction) ---

def test_prob_1_when_forecast_below_threshold_for_below_market():
    # 15°C = 59°F < 60°F threshold, direction below → prob 1.0
    market = make_market(60.0, "F", "below", 0.30)
    result = calculate_edge(market, 15.0)
    assert result["model_prob"] == 1.0


def test_prob_0_when_forecast_above_threshold_for_below_market():
    # 20°C = 68°F > 60°F threshold, direction below → prob 0.0
    market = make_market(60.0, "F", "below", 0.30)
    result = calculate_edge(market, 20.0)
    assert result["model_prob"] == 0.0


# --- Edge calculation ---

def test_edge_is_model_prob_minus_yes_price():
    market = make_market(75.0, "F", "above", 0.28)
    result = calculate_edge(market, 25.0)  # model_prob = 1.0
    assert result["edge"] == pytest.approx(1.0 - 0.28, abs=0.0001)


def test_negative_edge_when_forecast_misses():
    market = make_market(75.0, "F", "above", 0.28)
    result = calculate_edge(market, 20.0)  # model_prob = 0.0
    assert result["edge"] == pytest.approx(0.0 - 0.28, abs=0.0001)


# --- Output fields ---

def test_returns_enriched_market_dict():
    market = make_market(75.0, "F", "above", 0.28)
    result = calculate_edge(market, 25.0)
    assert "model_prob" in result
    assert "edge" in result
    assert "forecast_temp_c" in result
    assert "forecast_temp_converted" in result
    # Original fields preserved
    assert result["city"] == "new york"
    assert result["yes_price"] == 0.28
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_calculate_edge.py -v`
Expected: `ImportError` — `calculate_edge` not defined

- [ ] **Step 3: Implement calculate_edge() in market_discovery.py**

Add after `fetch_forecast`:

```python
# ---------------------------------------------------------------------------
# Layer 4: Calculate Model Probability and Edge
# ---------------------------------------------------------------------------

def calculate_edge(market, forecast_temp):
    """
    Calculate model probability and edge for a market.

    V1 model: binary — if forecast crosses the threshold in the right
    direction, model_prob = 1.0, otherwise 0.0.

    AI-READY HOOK: This function is intentionally simple for V1.
    In V2, replace with a Claude API call that reasons over the
    forecast distribution, historical variance, and market context.

    Args:
        market: parsed market dict from parse_market()
        forecast_temp: forecasted max temp in °C from fetch_forecast()

    Returns: market dict enriched with model_prob, edge, and forecast fields.
    """
    threshold = market["threshold"]
    unit = market["unit"]
    direction = market["direction"]

    # Convert forecast (always °C from Open-Meteo) to market's unit
    if unit == "F":
        forecast_converted = (forecast_temp * 9 / 5) + 32
    else:
        forecast_converted = forecast_temp

    # Binary probability model (V1)
    if direction == "above":
        model_prob = 1.0 if forecast_converted >= threshold else 0.0
    else:  # below
        model_prob = 1.0 if forecast_converted < threshold else 0.0

    edge = round(model_prob - market["yes_price"], 4)

    return {
        **market,
        "model_prob": model_prob,
        "edge": edge,
        "forecast_temp_c": round(forecast_temp, 1),
        "forecast_temp_converted": round(forecast_converted, 1),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_calculate_edge.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add market_discovery.py tests/test_calculate_edge.py
git commit -m "feat: add calculate_edge with unit conversion and binary probability model"
```

---

## Task 7: filter_opportunities() + Print Functions

**Files:**
- Modify: `market_discovery.py` — add `filter_opportunities()`, `print_opportunities()`, `print_summary()`
- Create: `tests/test_filter.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_filter.py`:

```python
import pytest
from market_discovery import filter_opportunities


def make_opp(yes_price, model_prob, edge=None, city="new york"):
    return {
        "city": city,
        "date": "2026-04-15",
        "market_question": "Will test hit threshold?",
        "yes_price": yes_price,
        "model_prob": model_prob,
        "edge": edge if edge is not None else round(model_prob - yes_price, 4),
        "token_id": "0xabc",
        "hours_until_resolve": 18.0,
        "forecast_temp_c": 25.0,
        "forecast_temp_converted": 77.0,
        "unit": "F",
        "threshold": 75.0,
    }


def test_passes_high_edge_opportunity():
    markets = [make_opp(0.28, 1.0)]
    result = filter_opportunities(markets)
    assert len(result) == 1


def test_rejects_yes_price_at_or_above_035():
    markets = [make_opp(0.35, 1.0), make_opp(0.40, 1.0)]
    result = filter_opportunities(markets)
    assert len(result) == 0


def test_rejects_model_prob_below_070():
    markets = [make_opp(0.28, 0.65)]
    result = filter_opportunities(markets)
    assert len(result) == 0


def test_accepts_boundary_values():
    # yes_price = 0.34 (< 0.35) and model_prob = 0.70 (>= 0.70)
    markets = [make_opp(0.34, 0.70)]
    result = filter_opportunities(markets)
    assert len(result) == 1


def test_sorts_by_edge_descending():
    markets = [
        make_opp(0.28, 1.0, edge=0.72),
        make_opp(0.20, 1.0, edge=0.80),
        make_opp(0.30, 1.0, edge=0.70),
    ]
    result = filter_opportunities(markets)
    assert result[0]["edge"] == 0.80
    assert result[1]["edge"] == 0.72
    assert result[2]["edge"] == 0.70


def test_returns_empty_list_when_no_opportunities():
    markets = [make_opp(0.50, 0.50)]
    result = filter_opportunities(markets)
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_filter.py -v`
Expected: `ImportError` — `filter_opportunities` not defined

- [ ] **Step 3: Implement filter_opportunities() and print functions in market_discovery.py**

Add after `calculate_edge`:

```python
# ---------------------------------------------------------------------------
# Layer 5: Filter to High-Edge Opportunities
# ---------------------------------------------------------------------------

def filter_opportunities(markets):
    """
    Filter enriched market dicts to only high-edge opportunities.

    Criteria: YES price < 0.35 AND model probability >= 0.70
    This means the market is pricing the event at under 35¢ while
    our forecast implies it's at least 70% likely — an edge of >= 0.35.

    Returns list sorted by edge descending (best opportunities first).
    """
    opportunities = [
        m for m in markets
        if m["yes_price"] < 0.35 and m["model_prob"] >= 0.70
    ]
    return sorted(opportunities, key=lambda x: x["edge"], reverse=True)


# ---------------------------------------------------------------------------
# Output Functions
# ---------------------------------------------------------------------------

def print_opportunities(opportunities):
    """Print each opportunity in a clean, human-readable format."""
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
        print(f"    Edge       : +{opp['edge']:.4f}")
        print(f"    Forecast   : {opp['forecast_temp_converted']:.1f}°{opp['unit']}"
              f" (threshold: {opp['threshold']}°{opp['unit']}, {opp['direction']})")
        print(f"    Token ID   : {opp['token_id']}")
        print(f"    Resolves   : {opp['hours_until_resolve']}h")


def print_summary(total_cities, failed_cities, total_markets, parsed_markets,
                  unmatched_count, opportunities_count):
    """Print a run summary showing coverage and result counts."""
    success_count = total_cities - len(failed_cities)
    failed_str = ""
    if failed_cities:
        names = ", ".join(c.title() for c in failed_cities)
        failed_str = f"  ({names}: timeout after 3 retries)"

    print(f"\n{'=' * 37}")
    print(f"  RUN SUMMARY")
    print(f"{'=' * 37}")
    print(f"  Cities fetched  : {success_count}/{total_cities}{failed_str}")
    print(f"  Markets parsed  : {parsed_markets}/{total_markets}"
          f"  ({unmatched_count} unmatched → see logs/)")
    print(f"  Opportunities   : {opportunities_count}")
    print(f"{'=' * 37}\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_filter.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add market_discovery.py tests/test_filter.py
git commit -m "feat: add filter_opportunities and print helpers"
```

---

## Task 8: main() Orchestrator + Full Smoke Test

**Files:**
- Modify: `market_discovery.py` — add `main()` and `if __name__ == "__main__"` block

- [ ] **Step 1: Implement main() in market_discovery.py**

Add at the bottom of the file, before any existing `if __name__` block:

```python
# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def main():
    """
    Main entry point. Orchestrates the full discovery pipeline:

    1. Fetch all active weather markets from Polymarket (--inspect to debug)
    2. Parse each market — extract city, threshold, price, token
    3. Fetch Open-Meteo forecast for each city
    4. Calculate model probability and edge
    5. Filter to high-edge opportunities
    6. Print results and run summary
    """
    inspect_mode = "--inspect" in sys.argv

    # --- Step 1: Fetch markets ---
    markets_raw = fetch_markets(inspect=inspect_mode)  # exits if inspect=True

    # --- Step 2: Parse markets ---
    parsed = []
    unmatched_count = 0
    for raw in markets_raw:
        result = parse_market(raw)
        if result:
            parsed.append(result)
        else:
            # Count non-target-city skips too — some will be logged, some silent
            unmatched_count += 1

    # --- Step 3 + 4: Fetch forecasts and calculate edges ---
    failed_cities = []
    enriched = []

    for market in parsed:
        city = market["city"]
        date = market["date"]

        forecast_temp = fetch_forecast(city, date)

        if forecast_temp is None:
            # Track unique cities that failed to fetch
            if city not in failed_cities:
                failed_cities.append(city)
            continue

        enriched.append(calculate_edge(market, forecast_temp))

    # --- Step 5: Filter ---
    opportunities = filter_opportunities(enriched)

    # --- Step 6: Output ---
    print_opportunities(opportunities)
    print_summary(
        total_cities=len(TARGET_CITIES),
        failed_cities=failed_cities,
        total_markets=len(markets_raw),
        parsed_markets=len(parsed),
        unmatched_count=unmatched_count,
        opportunities_count=len(opportunities),
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run full test suite to verify nothing is broken**

Run: `pytest -v`
Expected: All tests pass (aim: 0 failures)

- [ ] **Step 3: Run --inspect mode to verify API connectivity and response structure**

Run: `python market_discovery.py --inspect`
Expected: Prints 3 raw market JSON objects from Polymarket and exits.

If this reveals unexpected field names (e.g., `outcomePrices` is named differently), update `parse_market()` to handle both names and re-run the full test suite.

- [ ] **Step 4: Run normal discovery mode**

Run: `python market_discovery.py`
Expected: Prints opportunities (or "No opportunities found") followed by RUN SUMMARY.

- [ ] **Step 5: Commit final working state**

```bash
git add market_discovery.py
git commit -m "feat: add main() orchestrator — market_discovery.py complete"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] `--inspect` mode → Task 3
- [x] Gamma API fetch with tag=weather, active=true → Task 3
- [x] Parse city, date, threshold, unit, direction → Task 4
- [x] Open-Meteo forecast for each city → Task 5
- [x] Unit conversion (F/C) → Task 6
- [x] Edge calculation (model_prob - yes_price) → Task 6
- [x] Filter: yes_price < 0.35 AND model_prob >= 0.70 → Task 7
- [x] Retry 3x exponential backoff → Task 2
- [x] Skip city on Open-Meteo failure → Task 8 (main)
- [x] Log unmatched titles → Task 4 (_log_unmatched)
- [x] Run summary with counts → Task 7 (print_summary)
- [x] .env support → Task 1 (python-dotenv)
- [x] AI-ready hooks in parse_market + calculate_edge → Task 4, 6

**No placeholders:** All steps contain actual code.

**Type consistency:** `parse_market()` returns dict with keys `city`, `date`, `market_question`, `threshold`, `unit`, `direction`, `yes_price`, `token_id`, `hours_until_resolve`. `calculate_edge()` receives this dict and adds `model_prob`, `edge`, `forecast_temp_c`, `forecast_temp_converted`. `filter_opportunities()` and `print_opportunities()` consume the enriched dict. All field names are consistent across all tasks.
