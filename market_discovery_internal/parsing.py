"""Market parsing and extraction logic for market_discovery."""

import os
import json
import re
from datetime import datetime, timezone

from market_discovery_internal.config import (
    LOG_FILE, DAILY_RESOLVE_ONLY, DAILY_MIN_HOURS_TO_RESOLVE,
    DAILY_MIN_RESOLVE_DAYS_AHEAD, DAILY_MAX_RESOLVE_DAYS_AHEAD,
    GOLDEN_WINDOW_HOURS_MAX, GOLDEN_WINDOW_HOURS_MIN,
    THRESHOLD_RE, RANGE_THRESHOLD_RE, EXACT_RE, ABOVE_RE, BELOW_RE,
    WEATHER_CONTEXT_RE, CITY_REGEXES, TARGET_CITIES,
    AIRPORT_IATA_TO_ICAO, STATION_NAME_TO_ICAO, CITY_STATIONS,
    LOWEST_TEMP_RE, LOWEST_TEMP_MARKETS_ENABLED,
)


def _normalize_icao_code(code, city_name=None):
    """Normalize a 3 or 4 letter airport code into a valid ICAO code."""
    if not code:
        return None
    code = code.upper().strip()
    
    # Priority 1: Direct hit in our mapping
    if code in AIRPORT_IATA_TO_ICAO:
        return AIRPORT_IATA_TO_ICAO[code]
    
    # Priority 2: 4-letter codes usually don't need change
    if len(code) == 4:
        return code
        
    # Priority 3: 3-letter codes
    if len(code) == 3:
        # If it's a known city in our TARGET_CITIES and it's US-based, prefix with K
        # We can detect US-based by looking at the hardcoded ICAO in config if it starts with K
        default_icao = TARGET_CITIES.get(city_name, {}).get("icao", "")
        if default_icao.startswith("K"):
            return f"K{code}"
            
    return code # Fallback to original if we can't be sure


def _log_unmatched(title, reason):
    """Append an unparseable market title to the log file."""
    os.makedirs("logs", exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"[{timestamp}] {reason}: {title}\n")

def _market_search_text(raw):
    """Combine title and description/slug for better city matching."""
    parts = [
        str(raw.get("question") or ""),
        str(raw.get("title") or ""),
        str(raw.get("description") or ""),
        str(raw.get("slug") or ""),
        str(raw.get("event_slug") or ""),
    ]
    return " ".join(parts)

def _has_target_city(text_lower):
    """Return True if the text contains any of the target city names."""
    for _, regex in CITY_REGEXES:
        if regex.search(text_lower):
            return True
    return False

def _match_target_city(text_lower):
    """Return the normalized name of the first matching target city."""
    # Priority matches: check for more specific city names first if needed.
    for city, regex in CITY_REGEXES:
        if regex.search(text_lower):
            return city
    return None

def _is_temperature_market_candidate(raw):
    """Heuristic check if a raw market seems like a weather temperature market."""
    search_text = _market_search_text(raw).lower()
    return (
        bool(WEATHER_CONTEXT_RE.search(search_text)) and
        bool(THRESHOLD_RE.search(search_text)) and
        _has_target_city(search_text)
    )

def _extract_yes_token_id(raw_market):
    """Extract the token ID for the 'Yes' outcome from a raw market payload."""
    # Method 1: Legacy tokens array
    tokens = raw_market.get("tokens", [])
    if tokens and isinstance(tokens, list):
        for t in tokens:
            if isinstance(t, dict):
                outcome = str(t.get("outcome") or "").lower()
                if outcome == "yes":
                    return t.get("token_id")
        if len(tokens) > 0 and isinstance(tokens[0], dict):
            return tokens[0].get("token_id")

    # Method 2: Modern clobTokenIds array (Gamma API)
    clob_ids = raw_market.get("clobTokenIds", [])
    if clob_ids and isinstance(clob_ids, str):
        try:
            import json
            clob_ids = json.loads(clob_ids)
        except Exception:
            pass
    if clob_ids and isinstance(clob_ids, list) and len(clob_ids) > 0:
        return clob_ids[0] # YES token is always index 0
        
    return None

def _normalize_city_key(city):
    """Normalize city name for dictionary keys."""
    if not city: return ""
    return str(city).strip().lower()

def check_golden_window(hours_until_resolve, hours_into_weather_day, city=None, market_date=None):
    """
    Dynamic timezone-aware golden window check.
    
    Uses hours_into_weather_day (time since local midnight at station) as primary
    criterion. When hours_into_weather_day is None (missing gameStartTime),
    computes it from city timezone + market_date (local midnight at station).
    Falls back to legacy flat hours_until_resolve check only when both
    weather-day data and timezone fallback are unavailable.
    
    Returns: None if market is in golden window (pass), or a rejection reason string.
    """
    from market_discovery_internal.config import (
        GOLDEN_WINDOW_HOURS_MIN,
        GOLDEN_WINDOW_HOURS_MAX,
        GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS,
        GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS,
        GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS,
        TARGET_CITIES,
    )
    
    # Safety floor: never enter if market is about to resolve
    if hours_until_resolve is not None and hours_until_resolve < GOLDEN_WINDOW_RESOLVE_SAFETY_HOURS:
        return "too_close_to_resolve"
    
    # Primary check: weather-day-based (timezone-aware)
    if hours_into_weather_day is None and city and market_date:
        # Fallback: compute from city timezone + market date
        try:
            from zoneinfo import ZoneInfo
            _tz_name = (TARGET_CITIES.get(city) or {}).get("tz") or "UTC"
            _local_tz = ZoneInfo(_tz_name)
            _local_midnight = datetime.fromisoformat(market_date).replace(tzinfo=_local_tz)
            _now = datetime.now(timezone.utc)
            hours_into_weather_day = (_now - _local_midnight).total_seconds() / 3600.0
        except Exception:
            pass
    
    if hours_into_weather_day is not None:
        if hours_into_weather_day < GOLDEN_WINDOW_WEATHER_DAY_MIN_HOURS:
            return "too_early_weather_day"
        if hours_into_weather_day > GOLDEN_WINDOW_WEATHER_DAY_MAX_HOURS:
            return "too_late_weather_day"
        return None  # in golden window
    
    # Last resort: legacy flat check when no weather day data at all
    if hours_until_resolve is not None:
        if hours_until_resolve > GOLDEN_WINDOW_HOURS_MAX:
            return "too_early_to_enter"
        if hours_until_resolve < GOLDEN_WINDOW_HOURS_MIN:
            return "too_close_to_resolve"
    
    return None

def parse_market(
    raw,
    now_utc=None,
    daily_resolve_only=DAILY_RESOLVE_ONLY,
    daily_min_hours_to_resolve=DAILY_MIN_HOURS_TO_RESOLVE,
    daily_min_resolve_days_ahead=DAILY_MIN_RESOLVE_DAYS_AHEAD,   # deprecated: unused, kept for backward compat
    daily_max_resolve_days_ahead=DAILY_MAX_RESOLVE_DAYS_AHEAD,   # deprecated: unused, kept for backward compat
    return_skip_reason=False,
):
    """Parse a raw Gamma API market dict into structured fields."""
    def _with_reason(parsed_market, skip_reason=None):
        if return_skip_reason:
            return parsed_market, skip_reason
        return parsed_market

    question = raw.get("question") or raw.get("title") or ""
    search_text = _market_search_text(raw)
    search_text_lower = search_text.lower()

    # Step 1: Match city
    city = _match_target_city(search_text_lower)
    if city is None:
        return _with_reason(None)

    # Step 1.5: [MODUL A] Precision Station Extraction
    icao_code = None
    source_explicit = False
    
    # Priority 1: Explicit code in parentheses like "(LGA)" or "(KNYC)"
    # We look for 3 uppercase letters (IATA) or 4 uppercase letters (ICAO)
    icao_match = re.search(r"\(([A-Z]{3,4})\)", search_text)
    if icao_match:
        extracted = icao_match.group(1)
        icao_code = _normalize_icao_code(extracted, city)
        if icao_code:
            source_explicit = True
    
    # Priority 2: Semantic station name matching (e.g. "Central Park", "O'Hare")
    if not icao_code:
        for name, code in STATION_NAME_TO_ICAO.items():
            if name in search_text_lower:
                icao_code = code
                source_explicit = True
                break

    # Priority 3: [MODUL A] Haiku AI Sensing — for ambiguous cities that passed regex and
    # name-dict but still lack an explicit station. Only triggered when the city has multiple
    # known stations (i.e. would otherwise hit the ambiguity guard).
    allowed_stations = CITY_STATIONS.get(city, [])
    if not source_explicit and len(allowed_stations) > 1:
        try:
            from market_discovery_internal.analysis import resolve_station_with_ai
            ai_icao = resolve_station_with_ai(city, search_text)
            if ai_icao:
                # Validate: AI-returned ICAO must belong to the city's known station list
                if ai_icao in allowed_stations:
                    icao_code = ai_icao
                    source_explicit = True
        except Exception as _e:
            _log_unmatched(question, f"haiku_sensing_error: {_e}")

    # Ambiguity Guard: Previously rejected if no explicit station. 
    # Now we allow fallback to the primary station in CITY_STATIONS or TARGET_CITIES.
    if len(allowed_stations) > 1 and not source_explicit:
        # Fallback to the primary station (first in list)
        icao_code = allowed_stations[0]
        # logic continues to fallback check below
    
    # Fallback for single-station cities or explicit matches
    if not icao_code:
        icao_code = TARGET_CITIES.get(city, {}).get("icao")

    # Final Source Literacy: Check for official oracles
    official_sources = ["noaa", "wunderground", "national weather service", "environment canada"]
    has_official_source = any(s in search_text_lower for s in official_sources)
    if not has_official_source:
        # We allow it if it's a standard temperature market, but log it as lower confidence?
        # User requested 'baca rules', so let's be strict if they mention a weird source.
        pass

    has_weather_context = bool(WEATHER_CONTEXT_RE.search(search_text_lower))
    has_temperature_hint = bool(THRESHOLD_RE.search(search_text))

    if not has_weather_context and not has_temperature_hint:
        return _with_reason(None)

    # Step 2: Extract threshold (try range bracket first, then single value)
    # CRITICAL: Only search `question` for range patterns, NEVER search_text/slug.
    # Slugs contain dates like "april-21-2026" which match the range regex as
    # "21-2026" (interpreted as 21°F to 2026°F). Range brackets like "50-51°F"
    # always appear in the question text itself.
    threshold_high = None  # Upper bound for range brackets like "50-51°F"
    range_match = RANGE_THRESHOLD_RE.search(question)
    if range_match:
        threshold = float(range_match.group(1))
        threshold_high = float(range_match.group(2))
        unit_match = range_match.group(3)
        # Sanity check: reject if either value is physically impossible for temperature
        if threshold > 200 or threshold_high > 200:
            range_match = None  # Fall through to single-value match
    if range_match and threshold_high is not None:
        pass  # range_match already set threshold, threshold_high, unit_match
    else:
        match = THRESHOLD_RE.search(question)
        if not match:
            match = THRESHOLD_RE.search(search_text)
        if not match:
            _log_unmatched(question, "no temperature threshold found")
            return _with_reason(None)
        threshold = float(match.group(1))
        unit_match = match.group(2)
    
    if unit_match:
        unit = unit_match.upper()
    else:
        # Smart Inference if unit is missing (Common in current Polymarket titles)
        if icao_code and icao_code.startswith("K"):
            unit = "F"
        elif threshold >= 60: # Heuristic: no city reaches 60°C, but 60°F is common
            unit = "F"
        else:
            unit = "C"

    # Step 3: Direction
    question_lower = question.lower()
    # Priority order for direction detection:
    # 1. Range brackets ("between 60-61°F") → always exact
    # 2. Explicit above/below keywords ("or higher", "or below") → above/below
    # 3. Explicit exact keywords ("exactly", "precisely") → exact
    # 4. Plain "be X°C/F" without qualifier → exact (350 of 666 markets on Polymarket)
    #    This is the MOST COMMON pattern for exact bracket markets.
    # 5. Default fallback → above (rare — only if no pattern matches at all)
    if threshold_high is not None:
        direction = "exact"
    elif ABOVE_RE.search(question_lower):
        direction = "above"
    elif BELOW_RE.search(question_lower):
        direction = "below"
    elif EXACT_RE.search(question_lower):
        direction = "exact"
    elif re.search(r'\bbe\s+(?:-?\d+)', question_lower):
        # [FIX] "be 6°C", "be 21°C", "be -1°C" without any above/below qualifier
        # = exact bracket. This covers 350+ markets (53% of all temperature markets).
        # ABOVE_RE and BELOW_RE are checked FIRST, so "be 6°C or higher" correctly
        # matches above, and only bare "be 6°C" falls through to here.
        direction = "exact"
    else:
        direction = "above"

    # Step 3b: Temperature type — "highest" (max) vs "lowest" (min)
    search_text_lower = _market_search_text(raw).lower()
    temp_type = "min" if LOWEST_TEMP_RE.search(search_text_lower) else "max"
    # Feature gate: skip lowest-temperature markets if disabled
    if temp_type == "min" and not LOWEST_TEMP_MARKETS_ENABLED:
        return _with_reason(None, "lowest_temp_disabled")

    # Step 4: Price
    # Primary: outcomePrices array (legacy Gamma field)
    # Fallback: bestAsk (newer bracket-style markets may omit outcomePrices)
    outcome_prices_raw = raw.get("outcomePrices")
    yes_price = None
    if outcome_prices_raw:
        try:
            prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
            if isinstance(prices, list) and len(prices) > 0:
                yes_price = float(prices[0])
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    if yes_price is None:
        # Fallback: use bestAsk as YES price for bracket markets
        best_ask_raw = raw.get("bestAsk")
        if best_ask_raw is not None:
            try:
                yes_price = float(best_ask_raw)
            except (ValueError, TypeError):
                pass

    if yes_price is None:
        _log_unmatched(question, "missing outcomePrices")
        return _with_reason(None)

    # Step 5: Token ID
    token_id = _extract_yes_token_id(raw)
    if not token_id:
        _log_unmatched(question, "missing token_id")
        return _with_reason(None)

    # Step 6: End date
    end_date_raw = raw.get("endDate") or raw.get("end_date") or ""
    try:
        end_dt = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00"))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        else:
            end_dt = end_dt.astimezone(timezone.utc)
        date_str = end_dt.strftime("%Y-%m-%d")
        now = now_utc if isinstance(now_utc, datetime) else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        hours_until_resolve = (end_dt - now).total_seconds() / 3600
    except (ValueError, AttributeError):
        _log_unmatched(question, f"could not parse endDate: {end_date_raw}")
        return _with_reason(None)

    if hours_until_resolve <= 0 or hours_until_resolve > 72:
        return _with_reason(None)

    if daily_resolve_only:
        # [MODUL C] Golden Window: only enter markets within the configured
        # hours-until-resolve range (default 4-18h). This balances forecast
        # accuracy (better closer to resolve) vs market inefficiency (better
        # further from resolve). Tunable via GOLDEN_WINDOW_HOURS_MIN/MAX.
        if hours_until_resolve > GOLDEN_WINDOW_HOURS_MAX:
            return _with_reason(None, "too_early_to_enter")
        if hours_until_resolve < GOLDEN_WINDOW_HOURS_MIN:
            return _with_reason(None, "too_close_to_resolve")

    market_slug = raw.get("slug") or raw.get("event_slug") or ""

    # Extract Gamma-provided pricing fields for downstream gates
    raw_best_ask = raw.get("bestAsk")
    raw_best_bid = raw.get("bestBid")
    try:
        best_ask = float(raw_best_ask) if raw_best_ask is not None else None
    except (ValueError, TypeError):
        best_ask = None
    try:
        best_bid = float(raw_best_bid) if raw_best_bid is not None else None
    except (ValueError, TypeError):
        best_bid = None
    gamma_spread = round(best_ask - best_bid, 4) if (best_ask is not None and best_bid is not None) else None
    try:
        volume_24hr = float(raw.get("volume24hr") or raw.get("volume_24hr") or 0.0)
    except (ValueError, TypeError):
        volume_24hr = 0.0
    gamma_accepting_orders = bool(raw.get("acceptingOrders", True))

    return _with_reason({
        "city": city,
        "icao_code": icao_code,
        "icao_explicit": source_explicit,
        "date": date_str,
        "end_date": end_dt.isoformat(),
        "market_question": question,
        "description": raw.get("description", ""),
        "threshold": threshold,
        "threshold_high": threshold_high,
        "unit": unit,
        "direction": direction,
        "temp_type": temp_type,
        "yes_price": yes_price,
        "best_ask": best_ask,
        "best_bid": best_bid,
        "gamma_spread": gamma_spread,
        "volume_24hr": volume_24hr,
        "gamma_accepting_orders": gamma_accepting_orders,
        "token_id": token_id,
        "hours_until_resolve": round(hours_until_resolve, 1),
        "market_slug": market_slug,
    })

