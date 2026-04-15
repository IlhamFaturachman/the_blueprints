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
from datetime import datetime, timezone

import requests
from market_discovery_internal.cli import (
    parse_cli_mode_flags,
    run_main_discovery_mode as _run_main_discovery_mode_impl,
    run_main_paper_loop_mode as _run_main_paper_loop_mode_impl,
    run_main_paper_report_mode as _run_main_paper_report_mode_impl,
    run_main_paper_single_mode as _run_main_paper_single_mode_impl,
)
from market_discovery_internal.config import *  # noqa: F401,F403
from market_discovery_internal.cycles import (
    enrich_discovery_markets as _enrich_discovery_markets_impl,
    parse_discovery_markets as _parse_discovery_markets_impl,
    run_discovery_cycle as _run_discovery_cycle_impl,
    run_paper_trading_cycle as _run_paper_trading_cycle_impl,
)
from market_discovery_internal.diagnostics import (
    analyze_discovery_raw_market as _analyze_discovery_raw_market_impl,
    build_discovery_diagnostics as _build_discovery_diagnostics_impl,
    classify_discovery_rejection_reason as _classify_discovery_rejection_reason_impl,
    collect_discovery_bucket_counts as _collect_discovery_bucket_counts_impl,
    collect_discovery_evidence_and_ai as _collect_discovery_evidence_and_ai_impl,
)
from market_discovery_internal.forecasting import (
    fetch_forecast_with_cache as _fetch_forecast_with_cache_impl,
    forecast_still_valid as _forecast_still_valid_impl,
    position_to_market as _position_to_market_impl,
    prefetch_forecasts as _prefetch_forecasts_impl,
)
from market_discovery_internal.output import (
    print_discovery_diagnostics as _print_discovery_diagnostics_impl,
    print_opportunities as _print_opportunities_impl,
    print_paper_cycle_summary as _print_paper_cycle_summary_impl,
    print_paper_state_report as _print_paper_state_report_impl,
    print_summary as _print_summary_impl,
)
from market_discovery_internal.reporting import (
    build_journal_anomaly_counters as _build_journal_anomaly_counters_impl,
    build_journal_age_breakdown as _build_journal_age_breakdown_impl,
    build_journal_retention_payload as _build_journal_retention_payload_impl,
    build_paper_state_report as _build_paper_state_report_impl,
    normalize_last_cycle_performance as _normalize_last_cycle_performance_impl,
    normalize_recent_journal_entries as _normalize_recent_journal_entries_impl,
    normalize_rolling_acceptance_metrics as _normalize_rolling_acceptance_metrics_impl,
    normalize_rolling_city_coverage_metrics as _normalize_rolling_city_coverage_metrics_impl,
    parse_utc_datetime as _parse_utc_datetime_impl,
)
from market_discovery_internal.state_persistence import (
    load_paper_state as _load_paper_state_impl,
    save_paper_state as _save_paper_state_impl,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Constants are imported from market_discovery_internal.config to keep
# market_discovery.py as a backward-compatible public surface.

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
    return any(pattern.search(text_lower) for _, pattern in CITY_REGEXES)


def _match_target_city(text_lower):
    """Return canonical city name when text matches configured city patterns."""
    for city, pattern in CITY_REGEXES:
        if pattern.search(text_lower):
            return city
    return None


def _is_temperature_market_candidate(raw):
    """Return True when market text looks like a city temperature contract."""
    text = _market_search_text(raw)
    text_lower = text.lower()

    if not _has_target_city(text_lower):
        return False
    if not THRESHOLD_RE.search(text):
        return False
    if WEATHER_CONTEXT_RE.search(text_lower):
        return True

    return bool(DIRECTION_CANDIDATE_RE.search(text_lower))


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

def _events_to_markets(events):
    """Flatten a list of event dicts into their constituent market dicts."""
    markets = []
    for event in events:
        for market in event.get("markets", []):
            markets.append(market)
    return markets


def fetch_markets(inspect=False, aggressive_scan=False):
    """
    Fetch active weather markets from the Polymarket Gamma Events API.

    Uses /events/pagination?tag_slug=weather which returns the correct
    weather temperature markets (not the tag=weather markets endpoint
    which returns unrelated results).

    If inspect=True, prints the first 3 raw event + market structures
    and exits.

    Returns a list of raw market dicts on success.
    Exits with code 1 if the API is unreachable after 3 retries.
    """
    base_params = {
        "tag_slug": "weather",
        "active": "true",
        "archived": "false",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
        "limit": 200,
    }

    try:
        data = fetch_with_retry(GAMMA_EVENTS_API, params={**base_params, "offset": 0})
    except Exception as e:
        print(f"\nERROR: Could not fetch markets from Gamma API: {e}")
        print("Check your internet connection and try again.")
        sys.exit(1)

    # Events pagination returns {"data": [...], "count": N} or a plain list
    if isinstance(data, dict):
        events = data.get("data", data.get("events", []))
    else:
        events = data

    if inspect:
        print("=== INSPECT MODE: First event and its first 3 markets ===\n")
        for i, event in enumerate(events[:2], 1):
            print(f"--- Event {i}: {event.get('title', '')} ---")
            for j, market in enumerate(event.get("markets", [])[:3], 1):
                print(f"  Market {j}:")
                print(json.dumps(market, indent=4, default=str))
            print()
        sys.exit(0)

    markets = _events_to_markets(events)
    candidates = [market for market in markets if _is_temperature_market_candidate(market)]
    if candidates:
        return _dedupe_markets(candidates)

    # Fetch additional pages if first page had no candidates
    page_limit = base_params["limit"]
    scanned = list(markets)
    offset = page_limit

    for _ in range(max(0, DISCOVERY_MAX_FETCH_PAGES)):
        try:
            page_data = fetch_with_retry(GAMMA_EVENTS_API, params={**base_params, "offset": offset}, max_retries=1)
        except Exception:
            break

        if isinstance(page_data, dict):
            page_events = page_data.get("data", page_data.get("events", []))
        else:
            page_events = page_data

        if not page_events:
            break

        page_markets = _events_to_markets(page_events)
        scanned.extend(page_markets)
        page_candidates = [market for market in page_markets if _is_temperature_market_candidate(market)]
        if page_candidates:
            candidates.extend(page_candidates)
            return _dedupe_markets(candidates)

        offset += page_limit

    # Fallback: return everything so parser can still discover markets
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

def parse_market(
    raw,
    now_utc=None,
    daily_resolve_only=DAILY_RESOLVE_ONLY,
    daily_min_hours_to_resolve=DAILY_MIN_HOURS_TO_RESOLVE,
    return_skip_reason=False,
):
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
    - Daily mode active and market resolve date/hour constraints are not met
    """
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
        return _with_reason(None)  # Not a target city — skip silently

    has_weather_context = bool(WEATHER_CONTEXT_RE.search(search_text_lower))
    has_temperature_hint = bool(THRESHOLD_RE.search(search_text))

    # Skip non-weather city markets early to avoid noisy unmatched logs.
    if not has_weather_context and not has_temperature_hint:
        return _with_reason(None)

    # Step 2: Extract temperature threshold and unit
    match = THRESHOLD_RE.search(question)
    if not match:
        match = THRESHOLD_RE.search(search_text)
    if not match:
        _log_unmatched(question, "no temperature threshold found")
        return _with_reason(None)

    threshold = float(match.group(1))
    unit = match.group(2).upper()

    # Step 3: Determine direction
    # Check question text (most reliable) then fallback to full search text
    question_lower = question.lower()
    direction_text = f"{question_lower} {search_text_lower}"
    if EXACT_RE.search(direction_text):
        direction = "exact"
    elif ABOVE_RE.search(direction_text):
        direction = "above"
    elif BELOW_RE.search(direction_text):
        direction = "below"
    else:
        direction = "exact"  # Polymarket exact-temp markets: "be 17°C on date"

    # Step 4: Extract YES price from outcomePrices (JSON string or list)
    outcome_prices_raw = raw.get("outcomePrices")
    if not outcome_prices_raw:
        _log_unmatched(question, "missing outcomePrices")
        return _with_reason(None)

    try:
        prices = json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
        if not isinstance(prices, list) or len(prices) == 0:
            _log_unmatched(question, "outcomePrices is not a non-empty list")
            return _with_reason(None)
        yes_price = float(prices[0])
    except (json.JSONDecodeError, ValueError, TypeError):
        _log_unmatched(question, "could not parse outcomePrices")
        return _with_reason(None)

    # Step 5: Extract token ID — Gamma API returns clobTokenIds as a JSON string ["id1","id2"]
    # The first ID is the YES token.
    clob_ids_raw = raw.get("clobTokenIds")
    token_id = None
    if clob_ids_raw:
        try:
            clob_ids = json.loads(clob_ids_raw) if isinstance(clob_ids_raw, str) else clob_ids_raw
            if isinstance(clob_ids, list) and clob_ids:
                token_id = str(clob_ids[0])
        except (json.JSONDecodeError, TypeError):
            pass

    if not token_id:
        _log_unmatched(question, "missing token_id")
        return _with_reason(None)

    # Step 6: Parse resolution date from endDate
    end_date_raw = raw.get("endDate") or raw.get("end_date") or ""
    try:
        end_dt = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00"))
        date_str = end_dt.strftime("%Y-%m-%d")
        now = now_utc if isinstance(now_utc, datetime) else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        hours_until_resolve = (end_dt - now).total_seconds() / 3600
    except (ValueError, AttributeError):
        _log_unmatched(question, f"could not parse endDate: {end_date_raw}")
        return _with_reason(None)

    # Only include markets within 3-day forecast window
    if hours_until_resolve <= 0 or hours_until_resolve > 72:
        return _with_reason(None)

    if daily_resolve_only:
        try:
            min_hours = float(daily_min_hours_to_resolve)
        except (TypeError, ValueError):
            min_hours = DAILY_MIN_HOURS_TO_RESOLVE

        min_hours = max(min_hours, 0.0)
        if end_dt.date() != now.date():
            return _with_reason(None, "daily_date_mismatch")
        if hours_until_resolve < min_hours:
            return _with_reason(None, "daily_min_hours_not_met")

    market_slug = raw.get("slug") or raw.get("event_slug") or ""
    return _with_reason({
        "city": city,
        "date": date_str,
        "end_date": end_dt.isoformat(),
        "market_question": question,
        "market_slug": market_slug,
        "threshold": threshold,
        "unit": unit,
        "direction": direction,
        "yes_price": yes_price,
        "token_id": token_id,
        "hours_until_resolve": round(hours_until_resolve, 1),
    })


# ---------------------------------------------------------------------------
# Layer 3: Fetch Weather Forecast from Open-Meteo
# ---------------------------------------------------------------------------


class ForecastTemp(float):
    def __new__(cls, value, source):
        obj = super().__new__(cls, value)
        obj.source = source
        return obj

def fetch_forecast(city, date):
    """Fetch the daily max temperature forecast from Open-Meteo and wttr.in."""
    coords = TARGET_CITIES.get(city)
    if not coords:
        return None

    def _fetch_open_meteo():
        params = {"latitude": coords["lat"], "longitude": coords["lon"], "daily": "temperature_2m_max", "timezone": "auto", "forecast_days": 3}
        try:
            data = fetch_with_retry(OPEN_METEO_API, params=params)
            daily = data.get("daily", {})
            times = daily.get("time", [])
            temps = daily.get("temperature_2m_max", [])
            if date in times:
                return temps[times.index(date)]
        except Exception:
            pass
        return None

    def _fetch_wttr():
        try:
            import urllib.parse
            url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
            data = fetch_with_retry(url)
            for w in data.get("weather", []):
                if w.get("date") == date:
                    return float(w.get("maxtempC", 0))
        except Exception:
            pass
        return None

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_om = executor.submit(_fetch_open_meteo)
        f_wt = executor.submit(_fetch_wttr)
        t_om = f_om.result()
        t_wt = f_wt.result()

    if t_om is not None and t_wt is not None:
        avg = round((t_om + t_wt) / 2.0, 1)
        return ForecastTemp(avg, "dual-source")
    elif t_om is not None:
        return ForecastTemp(t_om, "open-meteo")
    elif t_wt is not None:
        return ForecastTemp(t_wt, "wttr.in")
    return None


def _hours_until_target_date(date_str, now_utc=None):
    """Compute hours until target date (YYYY-MM-DD)."""
    if not date_str:
        return None

    try:
        target_dt = datetime.fromisoformat(f"{date_str}T12:00:00+00:00")
    except ValueError:
        return None

    now_dt = now_utc or datetime.now(timezone.utc)
    return (target_dt - now_dt).total_seconds() / 3600


def build_weather_evidence(city, date, forecast_temp_c, source="open-meteo", now_utc=None, fetched_at=None):
    source = getattr(forecast_temp_c, 'source', source)
    """Build normalized weather evidence contract for entry/hold decisions."""
    now_dt = now_utc or datetime.now(timezone.utc)
    fetched_dt = fetched_at or now_dt

    if isinstance(fetched_dt, str):
        try:
            fetched_dt = datetime.fromisoformat(fetched_dt.replace("Z", "+00:00"))
        except ValueError:
            fetched_dt = now_dt

    age_hours = max((now_dt - fetched_dt).total_seconds() / 3600, 0.0)
    horizon_hours = _hours_until_target_date(date, now_utc=now_dt)

    if horizon_hours is None:
        horizon_component = 0.6
    elif horizon_hours <= 24:
        horizon_component = 1.0
    elif horizon_hours <= 48:
        horizon_component = 0.9
    elif horizon_hours <= 72:
        horizon_component = 0.8
    else:
        horizon_component = 0.5

    if source == "dual-source":
        source_component = 0.95
    elif source == "open-meteo":
        source_component = 0.85
    else:
        source_component = 0.70
    data_component = 1.0 if forecast_temp_c is not None else 0.0
    quality_score = round((0.50 * data_component) + (0.30 * horizon_component) + (0.20 * source_component), 4)

    return {
        "city": city,
        "date": date,
        "source": source,
        "forecast_temp_c": None if forecast_temp_c is None else round(float(forecast_temp_c), 2),
        "fetched_at": fetched_dt.isoformat(),
        "age_hours": round(age_hours, 4),
        "horizon_hours": None if horizon_hours is None else round(horizon_hours, 2),
        "quality_score": quality_score,
    }


def is_weather_evidence_valid(
    evidence,
    max_age_hours=WEATHER_EVIDENCE_MAX_AGE_HOURS,
    min_quality_score=WEATHER_EVIDENCE_MIN_QUALITY_SCORE,
):
    """Return True when evidence freshness and quality pass configured thresholds."""
    if not isinstance(evidence, dict):
        return False

    age_hours = _safe_float(evidence.get("age_hours"), 9999.0)
    quality_score = _safe_float(evidence.get("quality_score"), 0.0)
    forecast_temp = evidence.get("forecast_temp_c")

    if forecast_temp is None:
        return False

    return age_hours <= float(max_age_hours) and quality_score >= float(min_quality_score)


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


def _safe_float(value, default=0.0):
    """Convert a value to float or return default when conversion fails."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_div(numerator, denominator, default=0.0):
    """Safely divide two numeric values and guard against zero denominator."""
    den = _safe_float(denominator, 0.0)
    if den == 0:
        return default
    return _safe_float(numerator, 0.0) / den


def _elapsed_ms(started_at):
    """Return elapsed milliseconds from a perf_counter timestamp."""
    return round((time.perf_counter() - started_at) * 1000, 3)


def _clamp(value, low=0.0, high=1.0):
    """Clamp a numeric value into a closed interval."""
    return max(low, min(high, float(value)))


def _normalize_city_key(city):
    """Normalize city labels into stable lowercase keys."""
    if city is None:
        return None
    value = str(city).strip().lower()
    return value or None


def _city_candidate_rank(opportunity, bucket):
    """Rank candidates by confidence, then edge, then cheaper YES price."""
    confidence = _safe_float(bucket.get("confidence"), 0.0)
    edge = _safe_float(opportunity.get("edge"), 0.0)
    yes_price = _safe_float(opportunity.get("yes_price"), 1.0)
    return (confidence, edge, -yes_price)


def _evaluate_historical_tuner(history):
    """Build per-city adaptive edge thresholds from closed history."""
    base_min_edge = max(0.0, _safe_float(STRATEGY_MIN_EDGE, 0.35))
    min_trades = max(1, int(_safe_float(AUTO_TUNER_MIN_TRADES, 3)))

    snapshot = {
        "enabled": bool(AUTO_TUNER_ENABLED),
        "base_min_edge": round(base_min_edge, 4),
        "min_trades": min_trades,
        "city_scores": {},
        "summary": {
            "cities_total": 0,
            "aggressive": 0,
            "cautious": 0,
            "blacklist": 0,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if not AUTO_TUNER_ENABLED or not isinstance(history, list):
        return snapshot

    per_city = {}
    for position in history:
        city_key = _normalize_city_key(position.get("city"))
        if not city_key:
            continue

        stats = per_city.setdefault(
            city_key,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "stop_losses": 0,
                "net_realized_pnl": 0.0,
                "roi_total": 0.0,
            },
        )

        pnl = _safe_float(position.get("realized_pnl_usd"), 0.0)
        roi = _safe_float(position.get("realized_roi_pct"), 0.0)
        close_reason = str(position.get("close_reason") or "")

        stats["trades"] += 1
        stats["net_realized_pnl"] += pnl
        stats["roi_total"] += roi

        if pnl > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1

        if close_reason == "stop_loss":
            stats["stop_losses"] += 1

    for city_key, stats in per_city.items():
        trades = int(stats["trades"])
        wins = int(stats["wins"])
        stop_losses = int(stats["stop_losses"])
        win_rate = _safe_div(wins, trades, default=0.0)
        stop_loss_rate = _safe_div(stop_losses, trades, default=0.0)
        avg_roi = _safe_div(stats["roi_total"], trades, default=0.0)
        net_pnl = _safe_float(stats["net_realized_pnl"], 0.0)

        status = "neutral"
        tuned_min_edge = base_min_edge

        if trades >= min_trades:
            if (
                net_pnl <= _safe_float(AUTO_TUNER_BLACKLIST_PNL, -1.5)
                or stop_loss_rate >= _safe_float(AUTO_TUNER_BLACKLIST_STOP_LOSS_RATE, 0.75)
            ):
                status = "blacklist"
                tuned_min_edge = 2.0
            elif (
                win_rate >= _safe_float(AUTO_TUNER_AGGRESSIVE_WIN_RATE, 0.60)
                and net_pnl > 0
            ):
                status = "aggressive"
                tuned_min_edge = max(0.01, base_min_edge - _safe_float(AUTO_TUNER_EDGE_RELAX, 0.05))
            elif (
                win_rate <= _safe_float(AUTO_TUNER_CAUTION_WIN_RATE, 0.45)
                or net_pnl < 0
            ):
                status = "cautious"
                tuned_min_edge = min(0.99, base_min_edge + _safe_float(AUTO_TUNER_EDGE_PENALTY, 0.15))

        city_snapshot = {
            "status": status,
            "trades": trades,
            "win_rate": round(win_rate, 4),
            "stop_loss_rate": round(stop_loss_rate, 4),
            "net_realized_pnl": round(net_pnl, 4),
            "avg_realized_roi": round(avg_roi, 4),
            "min_edge": round(tuned_min_edge, 4),
        }
        snapshot["city_scores"][city_key] = city_snapshot

        if status == "aggressive":
            snapshot["summary"]["aggressive"] += 1
        elif status == "cautious":
            snapshot["summary"]["cautious"] += 1
        elif status == "blacklist":
            snapshot["summary"]["blacklist"] += 1

    snapshot["summary"]["cities_total"] = len(snapshot["city_scores"])
    return snapshot


def _build_daily_session_meta(meta, current_wallet, now_utc=None):
    """Build/update UTC daily session tracking used for entry gating."""
    now_dt = now_utc or datetime.now(timezone.utc)
    date_utc = now_dt.date().isoformat()
    session = meta.get("daily_session") if isinstance(meta.get("daily_session"), dict) else {}

    previous_date = str(session.get("date_utc") or "")
    if previous_date != date_utc:
        baseline_wallet = round(_safe_float(current_wallet, 0.0), 4)
        target_wallet = round(baseline_wallet * _safe_float(DAILY_TARGET_MULTIPLIER, 2.0), 4)
        session = {
            "date_utc": date_utc,
            "started_at_utc": now_dt.isoformat(),
            "baseline_wallet": baseline_wallet,
            "target_wallet": target_wallet,
        }
    else:
        baseline_wallet = round(_safe_float(session.get("baseline_wallet"), current_wallet), 4)
        target_wallet = round(
            _safe_float(session.get("target_wallet"), baseline_wallet * _safe_float(DAILY_TARGET_MULTIPLIER, 2.0)),
            4,
        )
        session["baseline_wallet"] = baseline_wallet
        session["target_wallet"] = target_wallet

    equity_wallet = round(_safe_float(current_wallet, 0.0), 4)
    if baseline_wallet <= 0:
        progress_pct = 0.0
    else:
        progress_pct = round((equity_wallet / baseline_wallet) * 100.0, 2)

    target_reached = target_wallet > 0 and equity_wallet >= target_wallet
    session.update(
        {
            "equity_wallet": equity_wallet,
            "progress_pct": progress_pct,
            "target_reached": bool(target_reached),
            "entry_gate_open": not bool(target_reached),
            "entry_gate_reason": "daily_target_reached" if target_reached else "active",
            "updated_at_utc": now_dt.isoformat(),
        }
    )
    return session


def _build_tuned_filter_opportunities(tuner_snapshot):
    """Build per-cycle opportunity filter with city-level tuned edge floors."""
    city_scores = tuner_snapshot.get("city_scores") if isinstance(tuner_snapshot, dict) else {}
    if not isinstance(city_scores, dict):
        city_scores = {}

    base_min_edge = _safe_float(
        tuner_snapshot.get("base_min_edge") if isinstance(tuner_snapshot, dict) else STRATEGY_MIN_EDGE,
        STRATEGY_MIN_EDGE,
    )

    def _filter(markets):
        opportunities = []
        for market in markets:
            city_key = _normalize_city_key(market.get("city"))
            city_rule = city_scores.get(city_key, {}) if city_key else {}
            tuned_min_edge = _safe_float(city_rule.get("min_edge"), base_min_edge)
            tuned_status = str(city_rule.get("status") or "neutral")

            if tuned_status == "blacklist":
                continue

            if (
                market["yes_price"] < STRATEGY_MAX_YES_PRICE
                and market["model_prob"] >= STRATEGY_MIN_MODEL_PROB
                and market["edge"] >= tuned_min_edge
            ):
                opportunities.append(
                    {
                        **market,
                        "effective_min_edge": round(tuned_min_edge, 4),
                        "tuner_status": tuned_status,
                    }
                )

        return sorted(opportunities, key=lambda item: item["edge"], reverse=True)

    return _filter


def _get_ai_provider_config():
    """Read AI provider configuration from environment."""
    return {
        "provider": os.getenv("AI_AGENT_PROVIDER", "").strip().lower(),
        "model": os.getenv("AI_AGENT_MODEL", "").strip(),
        "api_key": os.getenv("AI_AGENT_API_KEY", "").strip(),
        "endpoint": os.getenv("AI_AGENT_ENDPOINT", "").strip(),
        "timeout_seconds": AI_AGENT_TIMEOUT_SECONDS,
    }


def _is_ai_config_ready(config):
    """Return True when minimum AI configuration is present."""
    return bool(config.get("provider") and config.get("model") and config.get("api_key"))


def _build_ai_decision_payload(opportunity):
    """Build compact payload contract for future AI decision providers."""
    return {
        "city": opportunity.get("city"),
        "date": opportunity.get("date"),
        "market_question": opportunity.get("market_question"),
        "direction": opportunity.get("direction"),
        "threshold": opportunity.get("threshold"),
        "unit": opportunity.get("unit"),
        "yes_price": opportunity.get("yes_price"),
        "model_prob": opportunity.get("model_prob"),
        "edge": opportunity.get("edge"),
        "hours_until_resolve": opportunity.get("hours_until_resolve"),
        "weather_evidence": opportunity.get("weather_evidence"),
    }


def _validate_ai_decision_response(response):
    """Validate AI decision response schema and normalize values."""
    if not isinstance(response, dict):
        raise ValueError("ai response must be a dict")

    bucket = response.get("ai_bucket")
    confidence = _safe_float(response.get("ai_confidence"), -1.0)
    allowed = {"reject", "watchlist", "enter_swing", "enter_hold_candidate"}

    if bucket not in allowed:
        raise ValueError(f"invalid ai_bucket: {bucket}")
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError(f"invalid ai_confidence: {confidence}")

    return {
        "ai_bucket": bucket,
        "ai_confidence": round(confidence, 4),
    }


def _call_ai_provider(payload, config):
    """
    Call AI provider adapter.

    Current implementation intentionally supports only a deterministic "mock"
    adapter for future readiness and tests. Real provider adapters can be
    added later without changing bucket engine flow.
    """
    provider = config.get("provider", "")

    if provider == "mock":
        price = _safe_float(payload.get("yes_price"), 1.0)
        model_prob = _safe_float(payload.get("model_prob"), 0.0)
        edge = _safe_float(payload.get("edge"), 0.0)

        if price < PAPER_ENTRY_MIN_PRICE:
            bucket = "watchlist"
        elif (
            model_prob >= ENTRY_BUCKET_HOLD_MIN_PROB
            and edge >= ENTRY_BUCKET_HOLD_MIN_EDGE
        ):
            bucket = "enter_hold_candidate"
        elif PAPER_ENTRY_MIN_PRICE <= price <= PAPER_ENTRY_MAX_PRICE:
            bucket = "enter_swing"
        else:
            bucket = "reject"

        return {
            "ai_bucket": bucket,
            "ai_confidence": 0.85,
        }

    raise NotImplementedError(f"AI provider adapter not implemented: {provider}")


def _maybe_apply_ai_decision(opportunity):
    """
    Optionally enrich opportunity with AI bucket signals.

    Safe behavior guarantees:
    - AI disabled -> deterministic path only.
    - Missing config / provider error -> deterministic fallback, no crash.
    """
    enriched = {**opportunity}

    if not AI_AGENT_ENABLED:
        enriched["ai_status"] = "off"
        return enriched

    config = _get_ai_provider_config()
    if not _is_ai_config_ready(config):
        enriched["ai_status"] = "missing_config"
        return enriched

    try:
        payload = _build_ai_decision_payload(enriched)
        raw_response = _call_ai_provider(payload, config)
        validated = _validate_ai_decision_response(raw_response)
        enriched.update(validated)
        enriched["ai_status"] = "applied"
    except Exception as exc:
        enriched["ai_status"] = "fallback_error"
        enriched["ai_error"] = str(exc)[:160]

    return enriched


def _entry_confidence_score(opportunity):
    """Compute deterministic confidence score (0-1) for entry bucketing."""
    model_prob = _clamp(_safe_float(opportunity.get("model_prob"), 0.0))
    edge = max(_safe_float(opportunity.get("edge"), 0.0), 0.0)

    tuned_edge_floor = _safe_float(opportunity.get("effective_min_edge"), STRATEGY_MIN_EDGE)
    edge_scale = tuned_edge_floor if tuned_edge_floor > 0 else 0.35
    edge_component = min(edge / edge_scale, 1.0)

    hours = opportunity.get("hours_until_resolve")
    if hours is None:
        freshness_component = 0.5
    else:
        hours = _safe_float(hours, 999.0)
        if hours <= 0:
            freshness_component = 0.3
        elif hours <= 24:
            freshness_component = 1.0
        elif hours <= 48:
            freshness_component = 0.8
        else:
            freshness_component = 0.6

    score = (0.65 * model_prob) + (0.25 * edge_component) + (0.10 * freshness_component)
    return round(_clamp(score), 4)


def decide_entry_bucket(opportunity, min_entry_price, max_entry_price):
    """
    Classify opportunity into one bucket:
    - reject
    - watchlist
    - enter_swing
    - enter_hold_candidate

    AI override is optional and never bypasses hard entry guardrails.
    """
    price = _safe_float(opportunity.get("yes_price"), -1.0)
    model_prob = _safe_float(opportunity.get("model_prob"), 0.0)
    edge = _safe_float(opportunity.get("edge"), -1.0)
    confidence = _entry_confidence_score(opportunity)
    min_edge_threshold = _safe_float(opportunity.get("effective_min_edge"), STRATEGY_MIN_EDGE)

    if price < 0 or price > 1:
        decision = {
            "bucket": "reject",
            "reason": "invalid_price",
            "confidence": confidence,
        }
    elif model_prob < STRATEGY_MIN_MODEL_PROB:
        decision = {
            "bucket": "reject",
            "reason": "low_model_prob",
            "confidence": confidence,
        }
    elif edge < min_edge_threshold:
        decision = {
            "bucket": "reject",
            "reason": "low_edge",
            "confidence": confidence,
        }
    elif min_entry_price <= price <= max_entry_price:
        if (
            model_prob >= ENTRY_BUCKET_HOLD_MIN_PROB
            and edge >= ENTRY_BUCKET_HOLD_MIN_EDGE
            and confidence >= ENTRY_BUCKET_HOLD_MIN_CONFIDENCE
        ):
            decision = {
                "bucket": "enter_hold_candidate",
                "reason": "high_confidence_hold_candidate",
                "confidence": confidence,
            }
        else:
            decision = {
                "bucket": "enter_swing",
                "reason": "entry_band_swing",
                "confidence": confidence,
            }
    elif price <= ENTRY_BUCKET_WATCH_MAX_PRICE:
        decision = {
            "bucket": "watchlist",
            "reason": "out_of_entry_band_watch",
            "confidence": confidence,
        }
    else:
        decision = {
            "bucket": "reject",
            "reason": "out_of_entry_band_reject",
            "confidence": confidence,
        }

    ai_bucket = opportunity.get("ai_bucket")
    ai_confidence_raw = opportunity.get("ai_confidence")
    if AI_AGENT_ENABLED and isinstance(ai_bucket, str):
        allowed_buckets = {"reject", "watchlist", "enter_swing", "enter_hold_candidate"}
        ai_confidence = _clamp(_safe_float(ai_confidence_raw, confidence))

        if ai_bucket in allowed_buckets and ai_confidence >= AI_AGENT_MIN_OVERRIDE_CONFIDENCE:
            # Hard guardrail: AI cannot force entry outside configured entry bounds.
            if ai_bucket.startswith("enter") and not (min_entry_price <= price <= max_entry_price):
                return {
                    **decision,
                    "ai_override_applied": False,
                    "ai_override_reason": "blocked_by_entry_band",
                }

            return {
                **decision,
                "bucket": ai_bucket,
                "reason": f"ai_override_{decision['bucket']}_to_{ai_bucket}",
                "confidence": round(max(confidence, ai_confidence), 4),
                "ai_override_applied": True,
            }

    return {
        **decision,
        "ai_override_applied": False,
    }


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


def _compute_take_profit_price(entry_price):
    """Compute take-profit target price for +100% policy (2x entry by default)."""
    entry = _safe_float(entry_price, 0.0)
    if entry <= 0:
        return HYBRID_TAKE_PROFIT_MIN_PRICE
    return round(min(entry * HYBRID_TAKE_PROFIT_MULTIPLIER, 1.0), 4)


def _ensure_take_profit_target(position):
    """Normalize legacy positions so all open positions follow +100% TP policy."""
    normalized = {**position}
    entry_price = _safe_float(normalized.get("entry_price"), 0.0)
    if entry_price <= 0:
        return normalized

    target_price = _compute_take_profit_price(entry_price)
    normalized["target_price"] = target_price
    normalized["target_price_low"] = target_price
    normalized["target_price_high"] = target_price
    normalized["target_policy"] = "take_profit_100pct"
    return normalized


def build_paper_position(opportunity, stake_usd=PAPER_STAKE_USD):
    """Create a paper position from an opportunity candidate."""
    entry_price = float(opportunity["yes_price"])
    if entry_price <= 0:
        raise ValueError("yes_price must be > 0")

    quantity = round(float(stake_usd) / entry_price, 6)
    cost_basis = round(quantity * entry_price, 4)
    target_price = _compute_take_profit_price(entry_price)

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
        "market_slug": opportunity.get("market_slug", ""),
        "entry_price": entry_price,
        "quantity": quantity,
        "cost_basis": cost_basis,
        "target_price": target_price,
        "target_price_low": target_price,
        "target_price_high": target_price,
        "target_policy": "take_profit_100pct",
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
     1) Take-profit at +100% from entry (2x by default).
    2) Stop-loss when current price <= configured stop-loss price.
    3) If within late window (H-2 by default):
       - forecast valid + confidence >= min threshold -> hold to resolve
       - otherwise -> sell
    4) Otherwise hold and wait.
    """
    price = float(current_yes_price)
    target_price = float(position.get("target_price", _compute_take_profit_price(position.get("entry_price"))))
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

    if price >= target_price:
        return {
            "action": "sell",
            "reason": "take_profit_100pct",
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


def _update_cash_state(state):
    import os
    base_wallet = float(os.getenv("PAPER_MAX_OPEN_POSITIONS", 5)) * 1.0
    history = state.get("history", [])
    positions = state.get("positions", [])
    meta = state.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    
    realized_pnl = sum(float(p.get("realized_pnl_usd", 0.0)) for p in history)
    open_cost = sum(float(p.get("cost_basis", 0.0)) for p in positions)
    
    current_wallet = round(base_wallet + realized_pnl, 4)
    cash = round(current_wallet - open_cost, 4)
    
    meta["base_wallet"] = base_wallet
    meta["current_wallet"] = current_wallet
    meta["cash"] = cash
    meta["realized_pnl_usd"] = round(realized_pnl, 4)
    meta["open_cost_basis"] = round(open_cost, 4)
    meta["daily_session"] = _build_daily_session_meta(meta, current_wallet=current_wallet)
    meta["auto_tuner"] = _evaluate_historical_tuner(history)
    
    state["meta"] = meta
    return state

def load_paper_state(path=PAPER_STATE_FILE):
    """Load paper-trading state from disk or return an empty state."""
    return _update_cash_state(_load_paper_state_impl(path=path))

def save_paper_state(state, path=PAPER_STATE_FILE):
    """Persist paper-trading state to disk."""
    state = _update_cash_state(state)
    _save_paper_state_impl(state=state, path=path)


def _closed_reason_counts(closed_positions):
    """Count close reasons in this cycle for quick post-run diagnostics."""
    counts = {}
    for position in closed_positions:
        reason = str(position.get("close_reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _build_cycle_acceptance_metrics(discovery, bucket_counts, opened_positions, closed_positions):
    """Build per-cycle acceptance metrics from discovery to entry to close."""
    opportunities = len(discovery.get("opportunities", []))
    parsed = len(discovery.get("parsed", []))
    enriched = len(discovery.get("enriched", []))
    opened = len(opened_positions)
    closed = len(closed_positions)

    enter_swing = int(bucket_counts.get("enter_swing", 0))
    enter_hold = int(bucket_counts.get("enter_hold_candidate", 0))
    entry_candidates = enter_swing + enter_hold

    closed_realized_pnl = round(
        sum(_safe_float(position.get("realized_pnl_usd"), 0.0) for position in closed_positions),
        4,
    )
    closed_wins = sum(1 for position in closed_positions if _safe_float(position.get("realized_pnl_usd"), 0.0) > 0)
    avg_closed_roi = round(
        _safe_div(
            sum(_safe_float(position.get("realized_roi_pct"), 0.0) for position in closed_positions),
            closed,
            default=0.0,
        ),
        4,
    )

    return {
        "raw_total": len(discovery.get("markets_raw", [])),
        "parsed_total": parsed,
        "enriched_total": enriched,
        "opportunities_total": opportunities,
        "entry_candidates_total": entry_candidates,
        "opened_total": opened,
        "closed_total": closed,
        "closed_wins_total": closed_wins,
        "closed_realized_pnl_usd": closed_realized_pnl,
        "closed_avg_roi_pct": avg_closed_roi,
        "parsed_to_opportunity_rate": round(_safe_div(opportunities, parsed, default=0.0), 4),
        "opportunity_to_candidate_rate": round(_safe_div(entry_candidates, opportunities, default=0.0), 4),
        "opportunity_to_open_rate": round(_safe_div(opened, opportunities, default=0.0), 4),
        "candidate_to_open_rate": round(_safe_div(opened, entry_candidates, default=0.0), 4),
        "close_win_rate": round(_safe_div(closed_wins, closed, default=0.0), 4),
    }


def _build_rolling_acceptance_metrics(previous, cycle_metrics, bucket_counts):
    """Update cumulative acceptance metrics stored in paper state metadata."""
    previous = previous if isinstance(previous, dict) else {}

    bucket_totals_prev = previous.get("entry_bucket_totals", {})
    bucket_totals = {
        "reject": int(bucket_totals_prev.get("reject", 0)) + int(bucket_counts.get("reject", 0)),
        "watchlist": int(bucket_totals_prev.get("watchlist", 0)) + int(bucket_counts.get("watchlist", 0)),
        "enter_swing": int(bucket_totals_prev.get("enter_swing", 0)) + int(bucket_counts.get("enter_swing", 0)),
        "enter_hold_candidate": int(bucket_totals_prev.get("enter_hold_candidate", 0))
        + int(bucket_counts.get("enter_hold_candidate", 0)),
    }

    cycles_total = int(previous.get("cycles_total", 0)) + 1
    opportunities_total = int(previous.get("opportunities_total", 0)) + int(cycle_metrics.get("opportunities_total", 0))
    entry_candidates_total = int(previous.get("entry_candidates_total", 0)) + int(cycle_metrics.get("entry_candidates_total", 0))
    opened_total = int(previous.get("opened_total", 0)) + int(cycle_metrics.get("opened_total", 0))
    closed_total = int(previous.get("closed_total", 0)) + int(cycle_metrics.get("closed_total", 0))
    closed_wins_total = int(previous.get("closed_wins_total", 0)) + int(cycle_metrics.get("closed_wins_total", 0))
    closed_realized_pnl_total = round(
        _safe_float(previous.get("closed_realized_pnl_total_usd"), 0.0)
        + _safe_float(cycle_metrics.get("closed_realized_pnl_usd"), 0.0),
        4,
    )

    return {
        "cycles_total": cycles_total,
        "opportunities_total": opportunities_total,
        "entry_candidates_total": entry_candidates_total,
        "opened_total": opened_total,
        "closed_total": closed_total,
        "closed_wins_total": closed_wins_total,
        "closed_realized_pnl_total_usd": closed_realized_pnl_total,
        "entry_bucket_totals": bucket_totals,
        "opportunity_to_open_rate": round(_safe_div(opened_total, opportunities_total, default=0.0), 4),
        "candidate_to_open_rate": round(_safe_div(opened_total, entry_candidates_total, default=0.0), 4),
        "close_win_rate": round(_safe_div(closed_wins_total, closed_total, default=0.0), 4),
    }


def _build_city_coverage_metrics(
    opportunity_city_keys,
    candidate_city_keys,
    opened_city_keys,
    min_city_target=PAPER_MIN_CITY_DIVERSITY,
):
    """Build per-cycle city diversification metrics for soft target tracking."""
    target = max(1, int(_safe_float(min_city_target, PAPER_MIN_CITY_DIVERSITY)))
    opportunity_total = len(opportunity_city_keys)
    shortfall = max(target - opportunity_total, 0)

    return {
        "min_city_target": target,
        "unique_opportunity_cities": opportunity_total,
        "unique_candidate_cities": len(candidate_city_keys),
        "unique_opened_cities": len(opened_city_keys),
        "target_met": shortfall == 0,
        "shortfall": shortfall,
        "warning": shortfall > 0,
        "opportunity_cities": sorted(opportunity_city_keys),
        "candidate_cities": sorted(candidate_city_keys),
        "opened_cities": sorted(opened_city_keys),
    }


def _build_rolling_city_coverage_metrics(previous, cycle_city_coverage):
    """Update rolling city coverage counters for diversification monitoring."""
    previous = previous if isinstance(previous, dict) else {}

    cycles_total = int(previous.get("cycles_total", 0)) + 1
    opportunities_city_total = int(previous.get("opportunity_cities_total", 0)) + int(
        cycle_city_coverage.get("unique_opportunity_cities", 0)
    )
    candidates_city_total = int(previous.get("candidate_cities_total", 0)) + int(
        cycle_city_coverage.get("unique_candidate_cities", 0)
    )
    opened_city_total = int(previous.get("opened_cities_total", 0)) + int(
        cycle_city_coverage.get("unique_opened_cities", 0)
    )
    cycles_below_target = int(previous.get("cycles_below_target", 0)) + (
        1 if cycle_city_coverage.get("warning") else 0
    )

    return {
        "cycles_total": cycles_total,
        "opportunity_cities_total": opportunities_city_total,
        "candidate_cities_total": candidates_city_total,
        "opened_cities_total": opened_city_total,
        "cycles_below_target": cycles_below_target,
        "avg_opportunity_cities": round(_safe_div(opportunities_city_total, cycles_total, default=0.0), 4),
        "avg_candidate_cities": round(_safe_div(candidates_city_total, cycles_total, default=0.0), 4),
        "avg_opened_cities": round(_safe_div(opened_city_total, cycles_total, default=0.0), 4),
        "min_city_target": int(cycle_city_coverage.get("min_city_target", PAPER_MIN_CITY_DIVERSITY)),
    }


def _build_cycle_journal_entry(
    now_utc,
    cycle_metrics,
    bucket_counts,
    cycle,
    city_coverage_metrics=None,
    performance_metrics=None,
):
    """Create one compact journal record for this cycle."""
    entry = {
        "timestamp": now_utc.isoformat(),
        "aggressive_scan": bool(cycle.get("used_aggressive_scan", False)),
        "entry_gate_open": bool(cycle.get("entry_gate_open", True)),
        "entry_gate_reason": str(cycle.get("entry_gate_reason") or "active"),
        "empty_temperature_cycles": int(cycle.get("empty_temperature_cycles", 0) or 0),
        "counts": {
            "opportunities": int(cycle_metrics.get("opportunities_total", 0)),
            "entry_candidates": int(cycle_metrics.get("entry_candidates_total", 0)),
            "opened": int(cycle_metrics.get("opened_total", 0)),
            "closed": int(cycle_metrics.get("closed_total", 0)),
            "open_positions_after": len(cycle.get("open_positions", [])),
        },
        "bucket_counts": dict(bucket_counts),
        "closed_reason_counts": _closed_reason_counts(cycle.get("closed", [])),
        "acceptance_rates": {
            "opportunity_to_open_rate": cycle_metrics.get("opportunity_to_open_rate", 0.0),
            "candidate_to_open_rate": cycle_metrics.get("candidate_to_open_rate", 0.0),
            "close_win_rate": cycle_metrics.get("close_win_rate", 0.0),
        },
        "closed_realized_pnl_usd": cycle_metrics.get("closed_realized_pnl_usd", 0.0),
    }

    if isinstance(city_coverage_metrics, dict):
        entry["city_coverage"] = {
            "min_city_target": int(city_coverage_metrics.get("min_city_target", PAPER_MIN_CITY_DIVERSITY)),
            "unique_opportunity_cities": int(city_coverage_metrics.get("unique_opportunity_cities", 0)),
            "unique_candidate_cities": int(city_coverage_metrics.get("unique_candidate_cities", 0)),
            "unique_opened_cities": int(city_coverage_metrics.get("unique_opened_cities", 0)),
            "target_met": bool(city_coverage_metrics.get("target_met", False)),
            "shortfall": int(city_coverage_metrics.get("shortfall", 0)),
        }

    if isinstance(performance_metrics, dict):
        entry["performance"] = {
            "total_ms": _safe_float(performance_metrics.get("total_ms"), 0.0),
            "discovery_ms": _safe_float(performance_metrics.get("discovery_ms"), 0.0),
            "position_management_ms": _safe_float(performance_metrics.get("position_management_ms"), 0.0),
            "entry_selection_ms": _safe_float(performance_metrics.get("entry_selection_ms"), 0.0),
            "metrics_persist_ms": _safe_float(performance_metrics.get("metrics_persist_ms"), 0.0),
        }

    return entry


def _parse_discovery_markets(markets_raw):
    """Parse raw markets and collect skip metrics used by discovery diagnostics."""
    return _parse_discovery_markets_impl(
        markets_raw=markets_raw,
        parse_market_fn=parse_market,
    )


def _enrich_discovery_markets(parsed):
    """Enrich parsed markets with forecast/edge and keep forecast cache diagnostics."""
    return _enrich_discovery_markets_impl(
        parsed=parsed,
        perf_counter_fn=time.perf_counter,
        elapsed_ms_fn=_elapsed_ms,
        prefetch_forecasts_fn=_prefetch_forecasts,
        fetch_forecast_with_cache_fn=_fetch_forecast_with_cache,
        build_weather_evidence_fn=build_weather_evidence,
        is_weather_evidence_valid_fn=is_weather_evidence_valid,
        calculate_edge_fn=calculate_edge,
        maybe_apply_ai_decision_fn=_maybe_apply_ai_decision,
        discovery_forecast_prefetch_min_keys=DISCOVERY_FORECAST_PREFETCH_MIN_KEYS,
        discovery_forecast_prefetch_max_workers=DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS,
    )


def run_discovery_cycle(inspect=False, aggressive_scan=False, filter_opportunities_fn=None):
    """Run one discovery cycle and return structured results."""
    filter_fn = filter_opportunities if filter_opportunities_fn is None else filter_opportunities_fn
    return _run_discovery_cycle_impl(
        inspect=inspect,
        aggressive_scan=aggressive_scan,
        perf_counter_fn=time.perf_counter,
        elapsed_ms_fn=_elapsed_ms,
        fetch_markets_fn=fetch_markets,
        parse_discovery_markets_fn=_parse_discovery_markets,
        enrich_discovery_markets_fn=_enrich_discovery_markets,
        filter_opportunities_fn=filter_fn,
        daily_resolve_only=DAILY_RESOLVE_ONLY,
        daily_min_hours_to_resolve=DAILY_MIN_HOURS_TO_RESOLVE,
    )


def _collect_discovery_evidence_and_ai(enriched_markets):
    """Aggregate evidence validity and AI status counters for diagnostics."""
    return _collect_discovery_evidence_and_ai_impl(enriched_markets=enriched_markets)


def _collect_discovery_bucket_counts(
    opportunities,
    decide_entry_bucket_fn=None,
    paper_entry_min_price=None,
    paper_entry_max_price=None,
):
    """Aggregate entry bucket and reason counters for diagnostics."""
    decide_fn = decide_entry_bucket if decide_entry_bucket_fn is None else decide_entry_bucket_fn
    min_price = PAPER_ENTRY_MIN_PRICE if paper_entry_min_price is None else paper_entry_min_price
    max_price = PAPER_ENTRY_MAX_PRICE if paper_entry_max_price is None else paper_entry_max_price

    return _collect_discovery_bucket_counts_impl(
        opportunities=opportunities,
        decide_entry_bucket_fn=decide_fn,
        paper_entry_min_price=min_price,
        paper_entry_max_price=max_price,
    )


def _classify_discovery_rejection_reason(has_city, has_threshold, has_weather, has_direction):
    """Classify why a raw market is rejected from temperature-candidate stage."""
    return _classify_discovery_rejection_reason_impl(
        has_city=has_city,
        has_threshold=has_threshold,
        has_weather=has_weather,
        has_direction=has_direction,
    )


def _analyze_discovery_raw_market(raw):
    """Return candidate-stage signals for one raw market item."""
    return _analyze_discovery_raw_market_impl(
        raw=raw,
        market_search_text_fn=_market_search_text,
        has_target_city_fn=_has_target_city,
        threshold_re=THRESHOLD_RE,
        weather_context_re=WEATHER_CONTEXT_RE,
        direction_candidate_re=DIRECTION_CANDIDATE_RE,
    )


def _format_discovery_timing_line(perf):
    """Build discovery timing line for diagnostics output."""
    return (
        "  Timing (ms)        : "
        f"fetch={_safe_float(perf.get('fetch_markets_ms'), 0.0):.2f}, "
        f"parse={_safe_float(perf.get('parse_ms'), 0.0):.2f}, "
        f"prefetch={_safe_float(perf.get('forecast_prefetch_ms'), 0.0):.2f}, "
        f"enrich={_safe_float(perf.get('enrich_ms'), 0.0):.2f}, "
        f"filter={_safe_float(perf.get('filter_ms'), 0.0):.2f}, "
        f"total={_safe_float(perf.get('total_ms'), 0.0):.2f}"
    )


def _format_discovery_cache_line(forecast_cache):
    """Build forecast cache line for diagnostics output."""
    return (
        "  Forecast cache     : "
        f"size={int(_safe_float(forecast_cache.get('size'), 0.0))}, "
        f"hits={int(_safe_float(forecast_cache.get('hits'), 0.0))}, "
        f"misses={int(_safe_float(forecast_cache.get('misses'), 0.0))}"
    )


def _format_discovery_prefetch_line(forecast_prefetch):
    """Build forecast prefetch line for diagnostics output."""
    return (
        "  Forecast prefetch  : "
        f"eligible={int(_safe_float(forecast_prefetch.get('eligible'), 0.0))}, "
        f"attempted={int(_safe_float(forecast_prefetch.get('attempted'), 0.0))}, "
        f"success={int(_safe_float(forecast_prefetch.get('successful'), 0.0))}, "
        f"failed={int(_safe_float(forecast_prefetch.get('failed'), 0.0))}, "
        f"workers={int(_safe_float(forecast_prefetch.get('workers'), 0.0))}, "
        f"skipped={'yes' if forecast_prefetch.get('skipped') else 'no'}"
    )


def _format_discovery_daily_skip_line(diag):
    """Build daily skip line for diagnostics output."""
    return (
        "  Daily skips         : "
        f"date-mismatch={int(diag.get('daily_date_mismatch', 0))}, "
        f"min-hours={int(diag.get('daily_min_hours_not_met', 0))} "
        f"(min={float(diag.get('daily_min_hours_to_resolve', DAILY_MIN_HOURS_TO_RESOLVE)):.1f}h)"
    )


def build_discovery_diagnostics(discovery):
    """Build drop-off statistics to debug why opportunities are zero."""
    return _build_discovery_diagnostics_impl(
        discovery=discovery,
        collect_discovery_evidence_and_ai_fn=_collect_discovery_evidence_and_ai,
        collect_discovery_bucket_counts_fn=_collect_discovery_bucket_counts,
        analyze_discovery_raw_market_fn=_analyze_discovery_raw_market,
        classify_discovery_rejection_reason_fn=_classify_discovery_rejection_reason,
        decide_entry_bucket_fn=decide_entry_bucket,
        paper_entry_min_price=PAPER_ENTRY_MIN_PRICE,
        paper_entry_max_price=PAPER_ENTRY_MAX_PRICE,
        daily_resolve_only=DAILY_RESOLVE_ONLY,
        daily_min_hours_to_resolve=DAILY_MIN_HOURS_TO_RESOLVE,
    )


def print_discovery_diagnostics(discovery, aggressive_scan=False):
    """Print a concise per-stage drop-off report for discovery debugging."""
    _print_discovery_diagnostics_impl(
        discovery=discovery,
        aggressive_scan=aggressive_scan,
        build_discovery_diagnostics_fn=build_discovery_diagnostics,
        format_discovery_timing_line_fn=_format_discovery_timing_line,
        format_discovery_cache_line_fn=_format_discovery_cache_line,
        format_discovery_prefetch_line_fn=_format_discovery_prefetch_line,
        format_discovery_daily_skip_line_fn=_format_discovery_daily_skip_line,
    )


def _position_to_market(position, current_yes_price, hours_until_resolve):
    """Build calculate_edge-compatible market dict from an open position."""
    return _position_to_market_impl(
        position=position,
        current_yes_price=current_yes_price,
        hours_until_resolve=hours_until_resolve,
    )


def _fetch_forecast_with_cache(city, date, cache, stats=None):
    """Fetch forecast with per-cycle cache for successful city/date lookups."""
    return _fetch_forecast_with_cache_impl(
        city=city,
        date=date,
        cache=cache,
        stats=stats,
        fetch_forecast_fn=fetch_forecast,
    )


def _prefetch_forecasts(cache_keys, cache, min_keys=0, max_workers=1):
    """Warm forecast cache in parallel for large unique city/date batches."""
    return _prefetch_forecasts_impl(
        cache_keys=cache_keys,
        cache=cache,
        min_keys=min_keys,
        max_workers=max_workers,
        fetch_forecast_fn=fetch_forecast,
    )


def _forecast_still_valid(
    position,
    current_yes_price,
    hours_until_resolve,
    forecast_cache=None,
    forecast_cache_stats=None,
):
    """Evaluate whether forecast still supports the open position thesis."""
    return _forecast_still_valid_impl(
        position=position,
        current_yes_price=current_yes_price,
        hours_until_resolve=hours_until_resolve,
        forecast_cache=forecast_cache,
        forecast_cache_stats=forecast_cache_stats,
        fetch_forecast_with_cache_fn=_fetch_forecast_with_cache,
        build_weather_evidence_fn=build_weather_evidence,
        is_weather_evidence_valid_fn=is_weather_evidence_valid,
        position_to_market_fn=_position_to_market,
        calculate_edge_fn=calculate_edge,
    )


def _build_open_position_inventory(open_positions):
    """Build token and per-city occupancy maps for currently open positions."""
    open_token_ids = {position.get("token_id") for position in open_positions if position.get("status") == "open"}
    open_city_counts = {}

    for position in open_positions:
        if position.get("status") != "open":
            continue

        city_key = _normalize_city_key(position.get("city"))
        if city_key:
            open_city_counts[city_key] = open_city_counts.get(city_key, 0) + 1

    return open_token_ids, open_city_counts


def _build_entry_candidates(opportunities, open_token_ids, open_city_counts, min_bound, max_bound):
    """Build ranked entry candidates while enforcing one-best-candidate per city."""
    bucket_counts = {
        "reject": 0,
        "watchlist": 0,
        "enter_swing": 0,
        "enter_hold_candidate": 0,
    }

    opportunity_city_keys = {
        city_key
        for city_key in (
            _normalize_city_key(opportunity.get("city"))
            for opportunity in opportunities
        )
        if city_key
    }
    candidate_city_keys = set()
    best_candidate_by_city = {}
    cityless_candidates = []

    for index, opportunity in enumerate(opportunities):
        bucket = decide_entry_bucket(opportunity, min_bound, max_bound)
        bucket_name = bucket.get("bucket", "reject")
        bucket_counts[bucket_name] = bucket_counts.get(bucket_name, 0) + 1

        if bucket_name not in {"enter_swing", "enter_hold_candidate"}:
            continue

        token_id = opportunity.get("token_id")
        price = float(opportunity["yes_price"])

        if token_id in open_token_ids:
            continue
        if price < min_bound or price > max_bound:
            continue

        city_key = _normalize_city_key(opportunity.get("city"))
        if city_key:
            if open_city_counts.get(city_key, 0) >= PAPER_MAX_OPEN_PER_CITY:
                continue
            candidate_city_keys.add(city_key)

        candidate = {
            "index": index,
            "city_key": city_key,
            "opportunity": opportunity,
            "bucket": bucket,
            "bucket_name": bucket_name,
            "rank": _city_candidate_rank(opportunity, bucket),
        }

        if city_key is None:
            cityless_candidates.append(candidate)
            continue

        current_best = best_candidate_by_city.get(city_key)
        if (
            current_best is None
            or candidate["rank"] > current_best["rank"]
            or (candidate["rank"] == current_best["rank"] and candidate["index"] < current_best["index"])
        ):
            best_candidate_by_city[city_key] = candidate

    entry_candidates = list(best_candidate_by_city.values()) + cityless_candidates
    entry_candidates.sort(
        key=lambda item: (
            item["rank"][0],
            item["rank"][1],
            item["rank"][2],
            -item["index"],
        ),
        reverse=True,
    )

    return entry_candidates, bucket_counts, opportunity_city_keys, candidate_city_keys


def _append_opened_positions_from_candidates(
    entry_candidates,
    next_open_positions,
    open_token_ids,
    open_city_counts,
    available_slots,
    stake_usd,
):
    """Open positions from ranked candidates while respecting city and slot limits."""
    opened_this_cycle = []
    opened_city_keys = set()

    for candidate in entry_candidates:
        if available_slots <= 0:
            break

        opportunity = candidate["opportunity"]
        bucket = candidate["bucket"]
        bucket_name = candidate["bucket_name"]
        city_key = candidate["city_key"]
        token_id = opportunity.get("token_id")

        if token_id in open_token_ids:
            continue
        if city_key and open_city_counts.get(city_key, 0) >= PAPER_MAX_OPEN_PER_CITY:
            continue

        position = build_paper_position(opportunity, stake_usd=stake_usd)
        position["entry_bucket"] = bucket_name
        position["entry_bucket_reason"] = bucket.get("reason")
        position["entry_confidence_score"] = bucket.get("confidence")
        position["entry_ai_status"] = opportunity.get("ai_status", "off")
        if opportunity.get("ai_bucket") is not None:
            position["entry_ai_bucket"] = opportunity.get("ai_bucket")
        if opportunity.get("ai_confidence") is not None:
            position["entry_ai_confidence"] = opportunity.get("ai_confidence")
        if bucket.get("ai_override_applied"):
            position["entry_ai_override_applied"] = True

        next_open_positions.append(position)
        opened_this_cycle.append(position)
        open_token_ids.add(token_id)
        if city_key:
            open_city_counts[city_key] = open_city_counts.get(city_key, 0) + 1
            opened_city_keys.add(city_key)
        available_slots -= 1

    return opened_this_cycle, opened_city_keys, available_slots


def run_paper_trading_cycle(
    min_price=None,
    max_price=None,
    stake_usd=PAPER_STAKE_USD,
    state_path=PAPER_STATE_FILE,
    force_aggressive_scan=False,
):
    """Run one paper-trading cycle: discover, manage exits, open new positions."""
    import os

    state = load_paper_state(state_path)
    meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    current_wallet = _safe_float(
        meta.get("current_wallet"),
        float(os.getenv("PAPER_MAX_OPEN_POSITIONS", 5)) * 1.0,
    )

    if current_wallet >= 10.0:
        stake_usd = float(int(current_wallet / 5.0))

    stake_usd = max(0.01, _safe_float(stake_usd, PAPER_STAKE_USD))
    paper_max_open_positions = max(1, int(current_wallet // stake_usd)) if current_wallet > 0 else 1

    daily_session = meta.get("daily_session") if isinstance(meta.get("daily_session"), dict) else {}
    allow_new_entries = bool(daily_session.get("entry_gate_open", True))
    entry_gate_reason = str(daily_session.get("entry_gate_reason") or "active")

    tuner_snapshot = meta.get("auto_tuner") if isinstance(meta.get("auto_tuner"), dict) else {}
    tuned_filter_fn = _build_tuned_filter_opportunities(tuner_snapshot)

    def _run_discovery_cycle_with_tuner(inspect=False, aggressive_scan=False):
        return run_discovery_cycle(
            inspect=inspect,
            aggressive_scan=aggressive_scan,
            filter_opportunities_fn=tuned_filter_fn,
        )

    return _run_paper_trading_cycle_impl(

        min_price=min_price,
        max_price=max_price,
        stake_usd=stake_usd,
        state_path=state_path,
        force_aggressive_scan=force_aggressive_scan,
        perf_counter_fn=time.perf_counter,
        now_utc_fn=lambda: datetime.now(timezone.utc),
        elapsed_ms_fn=_elapsed_ms,
        load_paper_state_fn=load_paper_state,
        run_discovery_cycle_fn=_run_discovery_cycle_with_tuner,
        prefetch_forecasts_fn=_prefetch_forecasts,
        ensure_take_profit_target_fn=_ensure_take_profit_target,
        forecast_still_valid_fn=_forecast_still_valid,
        position_confidence_score_fn=_position_confidence_score,
        update_paper_position_fn=update_paper_position,
        close_paper_position_fn=close_paper_position,
        build_open_position_inventory_fn=_build_open_position_inventory,
        build_entry_candidates_fn=_build_entry_candidates,
        append_opened_positions_from_candidates_fn=_append_opened_positions_from_candidates,
        build_city_coverage_metrics_fn=_build_city_coverage_metrics,
        build_cycle_acceptance_metrics_fn=_build_cycle_acceptance_metrics,
        build_rolling_acceptance_metrics_fn=_build_rolling_acceptance_metrics,
        build_rolling_city_coverage_metrics_fn=_build_rolling_city_coverage_metrics,
        build_cycle_journal_entry_fn=_build_cycle_journal_entry,
        save_paper_state_fn=save_paper_state,
        discovery_enable_auto_aggressive_scan=DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN,
        discovery_auto_aggressive_after_empty_cycles=DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES,
        paper_position_forecast_prefetch_min_keys=PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS,
        paper_position_forecast_prefetch_max_workers=PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS,
        paper_entry_min_price=PAPER_ENTRY_MIN_PRICE,
        paper_entry_max_price=PAPER_ENTRY_MAX_PRICE,
        paper_max_open_positions=paper_max_open_positions,
        paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
        paper_journal_max_entries=PAPER_JOURNAL_MAX_ENTRIES,
        allow_new_entries=allow_new_entries,
        entry_gate_reason=entry_gate_reason,
    )


def print_paper_cycle_summary(cycle):
    """Print concise paper-trading cycle summary."""
    _print_paper_cycle_summary_impl(
        cycle=cycle,
        safe_float_fn=_safe_float,
        paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
    )


def _parse_utc_datetime(value):
    """Parse datetime inputs into timezone-aware UTC datetime objects."""
    return _parse_utc_datetime_impl(value=value)


def _build_journal_age_breakdown(journal_entries, now_utc=None):
    """Return age-bucket counts and timestamp coverage information for journal entries."""
    return _build_journal_age_breakdown_impl(
        journal_entries=journal_entries,
        now_utc=now_utc,
        parse_utc_datetime_fn=_parse_utc_datetime,
    )


def _normalize_rolling_acceptance_metrics(rolling):
    """Normalize rolling acceptance metrics into report payload schema."""
    return _normalize_rolling_acceptance_metrics_impl(
        rolling=rolling,
        safe_float_fn=_safe_float,
    )


def _normalize_rolling_city_coverage_metrics(city_rolling):
    """Normalize rolling city coverage metrics into report payload schema."""
    return _normalize_rolling_city_coverage_metrics_impl(
        city_rolling=city_rolling,
        safe_float_fn=_safe_float,
        paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
    )


def _normalize_last_cycle_performance(last_cycle_performance):
    """Normalize last-cycle timing and prefetch details for report payload."""
    return _normalize_last_cycle_performance_impl(
        last_cycle_performance=last_cycle_performance,
        safe_float_fn=_safe_float,
    )


def _normalize_recent_journal_entries(journal, recent_entries):
    """Normalize recent journal entries and return shown raw count."""
    return _normalize_recent_journal_entries_impl(
        journal=journal,
        recent_entries=recent_entries,
        safe_float_fn=_safe_float,
    )


def _build_journal_retention_payload(journal, shown_count, rolling, now_utc=None):
    """Build journal retention and rotation summary payload."""
    return _build_journal_retention_payload_impl(
        journal=journal,
        shown_count=shown_count,
        rolling=rolling,
        now_utc=now_utc,
        safe_float_fn=_safe_float,
        safe_div_fn=_safe_div,
        build_journal_age_breakdown_fn=_build_journal_age_breakdown,
        paper_journal_max_entries=PAPER_JOURNAL_MAX_ENTRIES,
        paper_report_retention_warn_threshold=PAPER_REPORT_RETENTION_WARN_THRESHOLD,
    )


def _build_journal_anomaly_counters(
    journal,
    safe_float_fn=None,
    paper_report_anomaly_streak_alert=None,
    paper_report_reject_dominant_ratio=None,
):
    """Build anomaly streak counters for paper state report payload."""
    safe_float = _safe_float if safe_float_fn is None else safe_float_fn
    streak_alert = (
        PAPER_REPORT_ANOMALY_STREAK_ALERT
        if paper_report_anomaly_streak_alert is None
        else paper_report_anomaly_streak_alert
    )
    reject_ratio = (
        PAPER_REPORT_REJECT_DOMINANT_RATIO
        if paper_report_reject_dominant_ratio is None
        else paper_report_reject_dominant_ratio
    )
    return _build_journal_anomaly_counters_impl(
        journal=journal,
        safe_float_fn=safe_float,
        paper_report_anomaly_streak_alert=streak_alert,
        paper_report_reject_dominant_ratio=reject_ratio,
    )


def build_paper_state_report(state, state_path=PAPER_STATE_FILE, recent_entries=5, now_utc=None):
    """Build a normalized paper-state report payload for text or JSON output."""
    return _build_paper_state_report_impl(
        state=state,
        state_path=state_path,
        recent_entries=recent_entries,
        now_utc=now_utc,
        safe_float_fn=_safe_float,
        safe_div_fn=_safe_div,
        paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
        paper_journal_max_entries=PAPER_JOURNAL_MAX_ENTRIES,
        paper_report_retention_warn_threshold=PAPER_REPORT_RETENTION_WARN_THRESHOLD,
        normalize_rolling_acceptance_metrics_fn=_normalize_rolling_acceptance_metrics_impl,
        normalize_rolling_city_coverage_metrics_fn=_normalize_rolling_city_coverage_metrics_impl,
        normalize_last_cycle_performance_fn=_normalize_last_cycle_performance_impl,
        normalize_recent_journal_entries_fn=_normalize_recent_journal_entries_impl,
        build_journal_retention_payload_fn=_build_journal_retention_payload_impl,
        build_journal_anomaly_counters_fn=_build_journal_anomaly_counters,
        paper_report_anomaly_streak_alert=PAPER_REPORT_ANOMALY_STREAK_ALERT,
        paper_report_reject_dominant_ratio=PAPER_REPORT_REJECT_DOMINANT_RATIO,
    )


def print_paper_state_report(
    state,
    state_path=PAPER_STATE_FILE,
    recent_entries=5,
    output_format="text",
    now_utc=None,
):
    """Print persisted paper state summary without running a new cycle."""
    return _print_paper_state_report_impl(
        state=state,
        state_path=state_path,
        recent_entries=recent_entries,
        output_format=output_format,
        now_utc=now_utc,
        build_paper_state_report_fn=build_paper_state_report,
        safe_float_fn=_safe_float,
        paper_min_city_diversity=PAPER_MIN_CITY_DIVERSITY,
    )


# ---------------------------------------------------------------------------
# Output Helpers
# ---------------------------------------------------------------------------

def print_opportunities(opportunities):
    """Print a readable opportunity list."""
    _print_opportunities_impl(opportunities=opportunities)


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
    _print_summary_impl(
        total_cities=total_cities,
        failed_cities=failed_cities,
        total_markets=total_markets,
        parsed_markets=parsed_markets,
        skipped_markets=skipped_markets,
        exact_skipped=exact_skipped,
        opportunities_count=opportunities_count,
    )


def _parse_cli_mode_flags(argv):
    """Parse CLI mode flags used by main()."""
    return parse_cli_mode_flags(argv)


def _run_main_paper_report_mode(paper_report_json_mode):
    """Handle paper report mode in main()."""
    _run_main_paper_report_mode_impl(
        paper_report_json_mode=paper_report_json_mode,
        load_paper_state_fn=load_paper_state,
        print_paper_state_report_fn=print_paper_state_report,
        paper_state_file=PAPER_STATE_FILE,
    )


def _run_main_paper_loop_mode(aggressive_mode):
    """Handle paper loop mode in main()."""
    _run_main_paper_loop_mode_impl(
        aggressive_mode=aggressive_mode,
        paper_loop_interval_seconds=PAPER_LOOP_INTERVAL_SECONDS,
        run_paper_trading_cycle_fn=run_paper_trading_cycle,
        print_paper_cycle_summary_fn=print_paper_cycle_summary,
        sleep_fn=time.sleep,
    )


def _run_main_paper_single_mode(aggressive_mode):
    """Handle one-shot paper mode in main()."""
    _run_main_paper_single_mode_impl(
        aggressive_mode=aggressive_mode,
        run_paper_trading_cycle_fn=run_paper_trading_cycle,
        print_paper_cycle_summary_fn=print_paper_cycle_summary,
    )


def _run_main_discovery_mode(inspect_mode, aggressive_mode, diagnose_mode):
    """Handle discovery/default mode in main()."""
    _run_main_discovery_mode_impl(
        inspect_mode=inspect_mode,
        aggressive_mode=aggressive_mode,
        diagnose_mode=diagnose_mode,
        run_discovery_cycle_fn=run_discovery_cycle,
        print_discovery_diagnostics_fn=print_discovery_diagnostics,
        print_opportunities_fn=print_opportunities,
        print_summary_fn=print_summary,
        target_cities_len=len(TARGET_CITIES),
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def main():
    """Run discovery mode or paper-trading mode based on CLI flags."""
    modes = _parse_cli_mode_flags(sys.argv)

    if modes["paper_report_mode"]:
        _run_main_paper_report_mode(modes["paper_report_json_mode"])
        return

    if modes["paper_loop_mode"]:
        _run_main_paper_loop_mode(modes["aggressive_mode"])
        return

    if modes["paper_mode"]:
        _run_main_paper_single_mode(modes["aggressive_mode"])
        return

    _run_main_discovery_mode(
        inspect_mode=modes["inspect_mode"],
        aggressive_mode=modes["aggressive_mode"],
        diagnose_mode=modes["diagnose_mode"],
    )


if __name__ == "__main__":
    main()
