"""
Research features for thesis-grade data collection + prediction hygiene.

- Station extraction from Polymarket rules/description
- Local day phase (timezone-aware)
- Market-vs-forecast gap
- Nearest-bracket ranking helpers
"""

from __future__ import annotations

import json
import os
import logging
logger = logging.getLogger(__name__)
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from market_discovery_internal.config import (
    STATION_NAME_TO_ICAO,
    TARGET_CITIES,
    RESEARCH_DATA_COLLECT,
    RESEARCH_CYCLES_LOG,
    RESEARCH_SKIPS_LOG,
    MARKET_FORECAST_GAP_MAX_C,
    EXACT_PROXIMITY_SIGMA,
    PREFER_NEAREST_BRACKET,
    SHADOW_OPPORTUNITIES_LOG,
    SHADOW_TRADES_LOG,
)

# Longer phrases first for matching
_STATION_NAMES_SORTED = sorted(STATION_NAME_TO_ICAO.keys(), key=len, reverse=True)

_RE_RECORDED_AT = re.compile(
    r"recorded at the\s+([^\.\n]{3,90}?)(?:\s+Station|\s+station)",
    re.IGNORECASE,
)
_RE_RECORDED_BY = re.compile(
    r"recorded by the\s+([^\.\n]{3,90}?)(?:\s+in degrees|\s+on\s|\.)",
    re.IGNORECASE,
)
_RE_WUNDER_PATH = re.compile(
    r"wunderground\.com/history/daily/([a-z]{2})/([^/\s\"']+)/([^/\s\"']+)",
    re.IGNORECASE,
)


def extract_station_from_rules(text: str) -> dict[str, Any]:
    """
    Parse resolve station / source from market description+title.
    Returns dict with station_name, icao (if mapped), source_kind, confidence.
    """
    out = {
        "station_name": None,
        "icao": None,
        "source_kind": None,
        "confidence": 0.0,
        "raw_match": None,
    }
    if not text:
        return out
    t = str(text)
    tl = t.lower()

    if "hong kong observatory" in tl or "weather.gov.hk" in tl:
        out.update({
            "station_name": "Hong Kong Observatory",
            "icao": "VHHH",
            "source_kind": "hko",
            "confidence": 0.95,
            "raw_match": "hong kong observatory",
        })
        return out
    if "wunderground" in tl:
        out["source_kind"] = "wunderground"
    if "noaa" in tl or "national weather service" in tl:
        out["source_kind"] = out["source_kind"] or "noaa"

    m = _RE_RECORDED_AT.search(t) or _RE_RECORDED_BY.search(t)
    if m:
        name = m.group(1).strip()
        out["station_name"] = name
        out["raw_match"] = name
        out["confidence"] = 0.85
        nl = name.lower()
        for key in _STATION_NAMES_SORTED:
            if key in nl or key in tl:
                out["icao"] = STATION_NAME_TO_ICAO[key]
                out["confidence"] = 0.95
                break
        return out

    for key in _STATION_NAMES_SORTED:
        if key in tl:
            out["station_name"] = key
            out["icao"] = STATION_NAME_TO_ICAO[key]
            out["confidence"] = 0.8
            out["raw_match"] = key
            return out

    wm = _RE_WUNDER_PATH.search(t)
    if wm:
        out["raw_match"] = wm.group(0)
        out["confidence"] = 0.5
        out["source_kind"] = out["source_kind"] or "wunderground"
    return out


def local_day_phase(city: str, now_utc: Optional[datetime] = None, game_start_iso: Optional[str] = None) -> dict[str, Any]:
    """
    Weather-day phase.

    Prefer gameStartTime (local midnight of observation day at station) when present.
    Else fall back to city timezone wall-clock.
    """
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tz_name = (TARGET_CITIES.get(city) or {}).get("tz") or "UTC"

    hours_into = None
    game_start_used = None
    if game_start_iso:
        try:
            gs = str(game_start_iso).replace("Z", "+00:00")
            if " " in gs and "T" not in gs:
                gs = gs.replace(" ", "T", 1)
            gs_dt = datetime.fromisoformat(gs)
            if gs_dt.tzinfo is None:
                gs_dt = gs_dt.replace(tzinfo=timezone.utc)
            else:
                gs_dt = gs_dt.astimezone(timezone.utc)
            hours_into = (now - gs_dt).total_seconds() / 3600.0
            game_start_used = gs_dt.isoformat()
        except Exception:
            hours_into = None

    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = now.astimezone(timezone.utc)
        tz_name = "UTC"

    if hours_into is None:
        hours = local.hour + local.minute / 60.0 + local.second / 3600.0
        clock_source = "wall_clock"
    else:
        # Map hours into weather day [0,24) for phase labels
        hours = hours_into % 24.0 if hours_into >= 0 else max(0.0, hours_into)
        clock_source = "game_start"

    if hours < 6:
        phase = "night_early"
    elif hours < 10:
        phase = "morning"
    elif hours < 14:
        phase = "midday"
    elif hours < 18:
        phase = "afternoon"
    elif hours < 21:
        phase = "evening"
    else:
        phase = "night_late"

    # Info quality depends on temp_type; caller may reweight. Default = highest-temp bias.
    info_quality_high = {
        "night_early": 0.15,
        "morning": 0.30,
        "midday": 0.55,
        "afternoon": 0.85,
        "evening": 0.95,
        "night_late": 0.92,
    }.get(phase, 0.5)
    # Lowest temps often set overnight / early morning
    info_quality_low = {
        "night_early": 0.90,
        "morning": 0.85,
        "midday": 0.55,
        "afternoon": 0.40,
        "evening": 0.35,
        "night_late": 0.70,
    }.get(phase, 0.5)

    return {
        "tz": tz_name,
        "local_hour": round(hours, 3),
        "local_date": local.strftime("%Y-%m-%d"),
        "phase": phase,
        "info_quality": info_quality_high,
        "info_quality_high": info_quality_high,
        "info_quality_low": info_quality_low,
        "hours_into_weather_day": round(hours_into, 3) if hours_into is not None else None,
        "hours_until_local_day_end": round(24.0 - hours_into, 3) if hours_into is not None else None,
        "game_start_time": game_start_used,
        "clock_source": clock_source,
    }


def threshold_to_c(threshold: float, unit: str) -> float:
    u = (unit or "C").upper()
    t = float(threshold)
    if u == "F":
        return (t - 32.0) * 5.0 / 9.0
    return t


def forecast_bracket_distance_c(opportunity: dict) -> Optional[float]:
    """Absolute °C distance between forecast and bracket threshold (mid for ranges)."""
    evidence = opportunity.get("weather_evidence") or {}
    fc = evidence.get("forecast_temp_c")
    if fc is None:
        fc = opportunity.get("forecast_temp_c")
    if fc is None:
        return None
    thr = opportunity.get("threshold")
    if thr is None:
        return None
    unit = opportunity.get("unit") or "C"
    lo = threshold_to_c(thr, unit)
    hi_raw = opportunity.get("threshold_high")
    if hi_raw is not None:
        hi = threshold_to_c(hi_raw, unit)
        mid = (lo + hi) / 2.0
    else:
        mid = lo
    return abs(float(fc) - float(mid))


def market_forecast_gap_c(opportunity: dict) -> Optional[float]:
    """|forecast − market_implied_expected_temp_c| if both exist."""
    evidence = opportunity.get("weather_evidence") or {}
    fc = evidence.get("forecast_temp_c")
    if fc is None:
        fc = opportunity.get("forecast_temp_c")
    mkt = opportunity.get("market_implied_expected_temp_c")
    if fc is None or mkt is None:
        return None
    try:
        return abs(float(fc) - float(mkt))
    except (TypeError, ValueError):
        return None


def hold_confidence(opportunity: dict) -> float:
    """
    0-1 score: should we prefer hold-to-resolve over mid TP/SL.
    Uses model prob, edge, weather-day info quality, structure.
    """
    try:
        prob = float(opportunity.get("model_prob") or opportunity.get("entry_model_prob") or 0)
        edge = float(opportunity.get("edge") or opportunity.get("entry_edge") or 0)
    except (TypeError, ValueError):
        prob, edge = 0.0, 0.0
    phase = opportunity.get("local_phase") or {}
    temp_type = opportunity.get("temp_type") or "max"
    iq = float(
        phase.get("info_quality_low" if temp_type == "min" else "info_quality_high")
        or phase.get("info_quality")
        or 0.5
    )
    structure = opportunity.get("structure") or opportunity.get("direction") or ""
    # exact needs higher bar
    struct_w = 0.85 if str(structure).startswith("exact") else 1.0
    # gap penalty
    gap = opportunity.get("market_forecast_gap_c")
    gap_pen = 0.0
    if gap is not None:
        try:
            gap_pen = min(0.35, float(gap) / 10.0)
        except (TypeError, ValueError):
            gap_pen = 0.0
    score = (0.45 * prob + 0.35 * min(1.0, max(0.0, edge) / 0.25) + 0.20 * iq) * struct_w
    score = max(0.0, min(1.0, score - gap_pen))
    return round(score, 4)


def attach_research_fields(opportunity: dict, now_utc: Optional[datetime] = None) -> dict:
    """Mutate/return opportunity with research fields for logging + gates."""
    city = opportunity.get("city") or ""
    desc = " ".join([
        str(opportunity.get("description") or ""),
        str(opportunity.get("market_question") or ""),
        str(opportunity.get("question") or ""),
        str(opportunity.get("resolution_source") or ""),
    ])
    station = extract_station_from_rules(desc)
    phase = local_day_phase(
        city,
        now_utc,
        game_start_iso=opportunity.get("game_start_time"),
    )
    # Prefer hours_into from parse_market if already set
    if opportunity.get("hours_into_weather_day") is not None and phase.get("hours_into_weather_day") is None:
        try:
            hi = float(opportunity["hours_into_weather_day"])
            phase["hours_into_weather_day"] = hi
            phase["hours_until_local_day_end"] = round(24.0 - hi, 3)
            phase["clock_source"] = phase.get("clock_source") or "parsed"
        except (TypeError, ValueError):
            pass
    dist = forecast_bracket_distance_c(opportunity)
    gap = market_forecast_gap_c(opportunity)
    opportunity["research_station"] = station
    opportunity["local_phase"] = phase
    # Propagate hours_into_weather_day from phase for downstream gates
    if opportunity.get("hours_into_weather_day") is None and phase.get("hours_into_weather_day") is not None:
        opportunity["hours_into_weather_day"] = phase["hours_into_weather_day"]
    opportunity["forecast_bracket_distance_c"] = dist
    opportunity["market_forecast_gap_c"] = gap
    opportunity["research_ts"] = time.time()
    opportunity["hold_confidence"] = hold_confidence(opportunity)
    # Prefer rules ICAO when high confidence
    if station.get("icao") and float(station.get("confidence") or 0) >= 0.8:
        opportunity["icao_from_rules"] = station["icao"]
        if opportunity.get("icao_code") and opportunity["icao_code"] != station["icao"]:
            opportunity["icao_mismatch"] = True
            opportunity["icao_code_default"] = opportunity.get("icao_code")
            opportunity["icao_code"] = station["icao"]
        elif not opportunity.get("icao_explicit"):
            opportunity["icao_code"] = station["icao"]
            opportunity["icao_explicit"] = True
            opportunity["icao_source"] = "rules"
    return opportunity


def passes_market_gap_gate(opportunity: dict, max_gap: Optional[float] = None) -> tuple[bool, str]:
    gap_limit = MARKET_FORECAST_GAP_MAX_C if max_gap is None else float(max_gap)
    gap = opportunity.get("market_forecast_gap_c")
    if gap is None:
        return True, "no_market_implied"
    if float(gap) > gap_limit:
        return False, f"market_forecast_gap {float(gap):.2f}C > {gap_limit}"
    return True, "ok"


def passes_market_consensus_gate(opportunity: dict) -> tuple[bool, str]:
    """Hard entry gate for exact brackets — kill false-edge lottery tickets.

    Calibration-driven (clean collection n≈29):
      - Only |forecast − threshold| < ~0.5°C had positive PnL
      - model_prob >= 0.6 (NOAA override) had 0% WR
      - entry < 0.10 bled hard
      - sibling YES ~0.99 while this YES ~0 → market already decided
    """
    # [STATION-ML] Attach + enforce forecast quality for paper-grade exact
    try:
        from market_discovery_internal.station_postprocess import attach_forecast_quality, QUALITY_MIN_FOR_PAPER
        attach_forecast_quality(opportunity)
        _dir0 = str(opportunity.get("direction") or "").lower()
        if _dir0 == "exact" and not opportunity.get("forecast_quality_ok", True):
            return False, f"exact_low_forecast_quality {opportunity.get('forecast_quality')}<{QUALITY_MIN_FOR_PAPER} ({opportunity.get('forecast_quality_why')})"
    except Exception:
        pass

    direction = str(opportunity.get("direction") or "")
    if direction != "exact":
        return True, "non_exact"

    if str(os.environ.get("DISABLE_EXACT_ENTRY", "0")).lower() in ("1", "true", "yes"):
        return False, "exact_entry_disabled"

    try:
        yes = float(opportunity.get("yes_price") or opportunity.get("entry_price") or 1.0)
    except (TypeError, ValueError):
        yes = 1.0
    try:
        prob = float(opportunity.get("model_prob") or opportunity.get("entry_model_prob") or 0.0)
    except (TypeError, ValueError):
        prob = 0.0
    try:
        edge = float(opportunity.get("edge") or opportunity.get("entry_edge") or 0.0)
    except (TypeError, ValueError):
        edge = 0.0

    min_entry = float(os.environ.get("STRATEGY_EXACT_MIN_ENTRY_PRICE", "0.12"))
    max_entry = float(os.environ.get("STRATEGY_EXACT_MAX_ENTRY_PRICE", "0.28"))
    max_prob = float(os.environ.get("STRATEGY_EXACT_MAX_MODEL_PROB", "0.55"))
    min_prob = float(os.environ.get("STRATEGY_EXACT_MIN_MODEL_PROB", "0.28"))
    min_edge = float(os.environ.get("STRATEGY_EXACT_MIN_EDGE", "0.08"))
    dead_yes = float(os.environ.get("EXACT_DEAD_YES_MAX", "0.04"))
    dead_mip = float(os.environ.get("EXACT_DEAD_MIP_MAX", "0.06"))
    max_fc_gap = float(os.environ.get("EXACT_FORECAST_MAX_GAP_C", "0.5"))
    max_mip_gap = float(os.environ.get("EXACT_MARKET_IMPLIED_MAX_GAP_C", "0.75"))

    if yes <= dead_yes:
        return False, f"exact_dead_yes {yes:.4f}<={dead_yes}"
    if yes < min_entry:
        return False, f"exact_entry_too_cheap {yes:.3f}<{min_entry}"
    if yes > max_entry:
        return False, f"exact_entry_too_rich {yes:.3f}>{max_entry}"
    if prob > 0 and prob < min_prob:
        return False, f"exact_prob_low {prob:.3f}<{min_prob}"
    if prob >= max_prob:
        # Overconfident model/NOAA override was 0% WR in sample
        return False, f"exact_prob_overconfident {prob:.3f}>={max_prob}"
    if edge < min_edge:
        return False, f"exact_edge_low {edge:.3f}<{min_edge}"

    # Forecast must sit on this bracket (primary calib signal)
    dist = opportunity.get("forecast_bracket_distance_c")
    if dist is None:
        try:
            fc = opportunity.get("forecast_temp_c")
            thr = opportunity.get("threshold")
            if fc is not None and thr is not None:
                dist = abs(float(fc) - float(thr))
        except (TypeError, ValueError):
            dist = None
    if dist is not None and float(dist) > max_fc_gap:
        return False, f"exact_forecast_gap_hard {float(dist):.2f}C>{max_fc_gap}"

    # Market-implied peak alignment when available
    mip = opportunity.get("market_implied_prob")
    mip_exp = opportunity.get("market_implied_expected_temp_c")
    thr = opportunity.get("threshold")
    if mip is not None:
        try:
            if float(mip) <= dead_mip and yes <= 0.15:
                return False, f"exact_dead_mip {float(mip):.3f}"
        except (TypeError, ValueError):
            pass
    if mip_exp is not None and thr is not None:
        try:
            gap_m = abs(float(thr) - float(mip_exp))
            if gap_m > max_mip_gap:
                return False, f"exact_far_from_market_peak {gap_m:.2f}C>{max_mip_gap}"
        except (TypeError, ValueError):
            pass

    # [DYNAMIC GOLDEN WINDOW] Use weather-day-based check (timezone-aware)
    from market_discovery_internal.parsing import check_golden_window
    hrs = opportunity.get("hours_until_resolve")
    hwd = opportunity.get("hours_into_weather_day")
    _city = opportunity.get("city")
    _mdate = opportunity.get("date") or opportunity.get("market_date")
    try:
        _hrs = float(hrs) if hrs is not None else None
        _hwd = float(hwd) if hwd is not None else None
    except (TypeError, ValueError):
        _hrs, _hwd = None, None
    _gw = check_golden_window(_hrs, _hwd, city=_city, market_date=_mdate)
    if _gw is not None:
        return False, f"exact_outside_golden {_gw}"

    return True, "ok"



def append_jsonl(path: str, row: dict) -> None:
    if not RESEARCH_DATA_COLLECT:
        return
    try:
        os.makedirs(os.path.dirname(path) or "logs", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        try:
            logger.warning("[JSONL] append failed path=%s err=%s", path, exc)
        except Exception:
            pass


def log_research_cycle(row: dict) -> None:
    append_jsonl(RESEARCH_CYCLES_LOG, row)


def log_research_skip(row: dict) -> None:
    append_jsonl(RESEARCH_SKIPS_LOG, row)

# ---------------------------------------------------------------------------
# Resolve clock helpers (Polymarket weather rules)
# ---------------------------------------------------------------------------

def effective_resolve_hours(
    end_date_iso,
    now_utc=None,
    resolve_after_next_day_first_point=False,
    game_start_iso=None,
    grace_default=None,
    grace_next_day=None,
):
    """
    Polymarket weather markets:
      - endDate = Gamma clock (often ~12:00 UTC on the listed day, NOT local midnight)
      - gameStartTime ≈ local midnight of the observation day at the station
      - Many rules: resolve after first datapoint of the *following* local day
        (revisions allowed until then). Official settle is AFTER weather day ends.

    Returns hours_until_end_date, hours_until_effective_resolve, grace_hours, past_end, past_effective.
    """
    from datetime import timedelta
    from market_discovery_internal.config import (
        RESOLVE_GRACE_HOURS_DEFAULT,
        RESOLVE_GRACE_HOURS_NEXT_DAY_FIRST_POINT,
    )

    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    if resolve_after_next_day_first_point:
        grace = float(
            grace_next_day
            if grace_next_day is not None
            else RESOLVE_GRACE_HOURS_NEXT_DAY_FIRST_POINT
        )
    else:
        grace = float(
            grace_default if grace_default is not None else RESOLVE_GRACE_HOURS_DEFAULT
        )

    end_dt = None
    if end_date_iso:
        try:
            end_dt = datetime.fromisoformat(str(end_date_iso).replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            else:
                end_dt = end_dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            end_dt = None

    # Prefer weather-day end = gameStart + 24h when available (true local day boundary)
    weather_day_end = None
    if game_start_iso:
        try:
            gs = str(game_start_iso).replace("Z", "+00:00")
            if " " in gs and "T" not in gs:
                gs = gs.replace(" ", "T", 1)
            gs_dt = datetime.fromisoformat(gs)
            if gs_dt.tzinfo is None:
                gs_dt = gs_dt.replace(tzinfo=timezone.utc)
            else:
                gs_dt = gs_dt.astimezone(timezone.utc)
            weather_day_end = gs_dt + timedelta(hours=24)
        except (ValueError, TypeError):
            weather_day_end = None

    # Effective resolve clock for paper force-settle:
    # max(endDate, weather_day_end) + grace
    base = end_dt
    if weather_day_end is not None:
        if base is None or weather_day_end > base:
            base = weather_day_end
    if base is None:
        return {
            "hours_until_end_date": None,
            "hours_until_effective_resolve": None,
            "grace_hours": grace,
            "past_end": False,
            "past_effective": False,
            "effective_resolve_at": None,
            "weather_day_end": weather_day_end.isoformat() if weather_day_end else None,
        }

    effective = base + timedelta(hours=grace)
    h_end = (end_dt - now).total_seconds() / 3600 if end_dt else None
    h_eff = (effective - now).total_seconds() / 3600
    return {
        "hours_until_end_date": round(h_end, 3) if h_end is not None else None,
        "hours_until_effective_resolve": round(h_eff, 3),
        "grace_hours": grace,
        "past_end": bool(h_end is not None and h_end <= 0),
        "past_effective": h_eff <= 0,
        "effective_resolve_at": effective.isoformat(),
        "weather_day_end": weather_day_end.isoformat() if weather_day_end else None,
        "resolve_base": base.isoformat(),
    }


def log_shadow_opportunity(row):
    """Append one shadow opportunity row (all directions + optional gate labels).

    Never blocks data collection — gate annotation failures are ignored.
    """
    if not row:
        return
    out = dict(row)
    try:
        if out.get("gate_ok") is None and str(out.get("direction") or "") == "exact":
            try:
                ok, why = passes_market_consensus_gate({
                    "direction": out.get("direction"),
                    "yes_price": out.get("yes_price"),
                    "model_prob": out.get("model_prob"),
                    "edge": out.get("edge"),
                    "forecast_temp_c": out.get("forecast_temp_c"),
                    "threshold": out.get("threshold"),
                    "forecast_bracket_distance_c": out.get("forecast_bracket_distance_c"),
                    "market_implied_prob": out.get("market_implied_prob"),
                    "market_implied_expected_temp_c": out.get("market_implied_expected_temp_c"),
                    "hours_until_resolve": out.get("hours_until_resolve"),
                })
                out["gate_ok"] = bool(ok)
                out["gate_reason"] = why
            except Exception as exc:
                out["gate_error"] = str(exc)[:160]
        if out.get("gap_ok") is None:
            try:
                ok_g, why_g = passes_market_gap_gate(out)
                out["gap_ok"] = bool(ok_g)
                out["gap_reason"] = why_g
            except Exception as exc:
                out["gap_error"] = str(exc)[:160]
    except Exception:
        pass
    path = globals().get("SHADOW_OPPORTUNITIES_LOG") or os.environ.get("SHADOW_OPPORTUNITIES_LOG", "logs/shadow_opportunities.jsonl")
    try:
        if path and not str(path).startswith("/"):
            path = os.path.abspath(path)
    except Exception:
        pass
    append_jsonl(path, out)



def multi_path_counterfactuals(
    entry_price,
    quantity,
    cost_basis,
    target_price,
    stop_loss_price,
    exit_price,
    settle_price=None,
):
    """Counterfactual PnL for hold / TP / SL paths (research)."""
    try:
        ep = float(entry_price or 0)
        qty = float(quantity or 0)
        cost = float(cost_basis or 0)
        tp = float(target_price or 0)
        sl = float(stop_loss_price or 0)
        xp = float(exit_price or 0)
    except (TypeError, ValueError):
        return {}

    def pnl(px):
        if qty <= 0 or cost <= 0:
            return None
        return round(px * qty - cost, 4)

    out = {
        "path_actual_exit_pnl": pnl(xp),
        "path_if_tp_pnl": pnl(tp) if tp > 0 else None,
        "path_if_sl_pnl": pnl(sl) if sl > 0 else None,
    }
    if settle_price is not None:
        try:
            sp = float(settle_price)
            out["path_if_hold_resolve_pnl"] = pnl(sp)
            out["would_win_if_hold_resolve"] = bool(sp >= 0.9)
        except (TypeError, ValueError):
            out["would_win_if_hold_resolve"] = None
            out["path_if_hold_resolve_pnl"] = None
    else:
        out["would_win_if_hold_resolve"] = None
        out["path_if_hold_resolve_pnl"] = None
    return out


# ---------------------------------------------------------------------------
# Live Polymarket resolution (Gamma) — official YES/NO after market resolves
# ---------------------------------------------------------------------------

def fetch_polymarket_resolution(
    market_slug=None,
    token_id=None,
    market_question=None,
    timeout=15,
):
    """
    Live-fetch official YES/NO resolution from Polymarket Gamma.

    Prefer exact market_slug match inside public-search event.markets.
    Never fuzzy-pick a random sibling bracket.
    """
    from market_discovery_internal.utils import fetch_with_retry

    def _parse_prices(raw):
        if raw is None:
            return None
        try:
            prices = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(prices, list) and len(prices) >= 1:
                y = float(prices[0])
                n = float(prices[1]) if len(prices) > 1 else None
                return [y, n]
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return None

    def _from_market(m, source):
        if not isinstance(m, dict):
            return None
        prices = _parse_prices(m.get("outcomePrices"))
        closed = bool(m.get("closed"))
        uma = str(m.get("umaResolutionStatus") or "").lower()
        yes = None
        if prices is not None:
            y = prices[0]
            if y >= 0.95:
                yes = True
            elif y <= 0.05:
                yes = False
        resolved = bool((closed or uma == "resolved") and yes is not None)
        if not resolved:
            return {
                "resolved": False,
                "yes_wins": None,
                "settle_price": None,
                "closed": closed,
                "uma_status": uma,
                "outcome_prices": prices,
                "source": source,
                "raw_question": m.get("question"),
                "matched_slug": m.get("slug"),
                "token_ids": m.get("clobTokenIds"),
            }
        return {
            "resolved": True,
            "yes_wins": bool(yes),
            "settle_price": 1.0 if yes else 0.0,
            "closed": closed,
            "uma_status": uma,
            "outcome_prices": prices,
            "source": source,
            "raw_question": m.get("question"),
            "matched_slug": m.get("slug"),
            "token_ids": m.get("clobTokenIds"),
        }

    def _collect_markets_from_payload(data):
        out = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                if item.get("question") is not None and item.get("outcomePrices") is not None:
                    out.append(item)
                for m in item.get("markets") or []:
                    if isinstance(m, dict):
                        out.append(m)
        elif isinstance(data, dict):
            if data.get("question") is not None and data.get("outcomePrices") is not None:
                out.append(data)
            for m in data.get("markets") or []:
                if isinstance(m, dict):
                    out.append(m)
            for ev in data.get("events") or data.get("data") or []:
                if not isinstance(ev, dict):
                    continue
                for m in ev.get("markets") or []:
                    if isinstance(m, dict):
                        out.append(m)
        return out

    def _pick_best(markets, source):
        if not markets:
            return None
        slug_l = str(market_slug or "").strip().lower()
        q_l = str(market_question or "").strip().lower()
        if slug_l:
            for m in markets:
                if str(m.get("slug") or "").strip().lower() == slug_l:
                    return _from_market(m, source + "+slug_exact")
        if q_l:
            for m in markets:
                if str(m.get("question") or "").strip().lower() == q_l:
                    return _from_market(m, source + "+question_exact")
        if token_id:
            tid = str(token_id)
            for m in markets:
                raw = m.get("clobTokenIds")
                try:
                    ids = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    ids = None
                if isinstance(ids, list) and tid in [str(x) for x in ids]:
                    return _from_market(m, source + "+token")
        return {
            "resolved": False,
            "yes_wins": None,
            "settle_price": None,
            "closed": False,
            "uma_status": "",
            "outcome_prices": None,
            "source": source + "+no_exact_match",
            "raw_question": None,
            "matched_slug": None,
            "candidates": [str(m.get("slug") or "") for m in markets[:12]],
        }

    # Path A: public-search with exact market slug
    if market_slug:
        try:
            data = fetch_with_retry(
                "https://gamma-api.polymarket.com/public-search",
                params={"q": str(market_slug), "limit_per_type": 20},
                max_retries=2,
                timeout=timeout,
            )
            markets = _collect_markets_from_payload(data)
            picked = _pick_best(markets, "gamma_search_slug")
            if picked and (
                picked.get("resolved")
                or str(picked.get("source") or "").endswith(("slug_exact", "question_exact", "token"))
            ):
                return picked
            parent = re.sub(
                r"-(?:\d+c|\d+f|\d+corbelow|\d+forbelow)$",
                "",
                str(market_slug),
                flags=re.I,
            )
            if parent and parent != str(market_slug):
                data2 = fetch_with_retry(
                    "https://gamma-api.polymarket.com/public-search",
                    params={"q": parent, "limit_per_type": 20},
                    max_retries=2,
                    timeout=timeout,
                )
                markets2 = _collect_markets_from_payload(data2)
                picked2 = _pick_best(markets2, "gamma_search_parent")
                if picked2 and (
                    picked2.get("resolved")
                    or str(picked2.get("source") or "").endswith(("slug_exact", "question_exact", "token"))
                ):
                    return picked2
        except Exception:
            pass

    # Path B: public-search by full question
    if market_question:
        try:
            data = fetch_with_retry(
                "https://gamma-api.polymarket.com/public-search",
                params={"q": str(market_question)[:140], "limit_per_type": 20},
                max_retries=2,
                timeout=timeout,
            )
            markets = _collect_markets_from_payload(data)
            picked = _pick_best(markets, "gamma_search_question")
            if picked and (
                picked.get("resolved")
                or str(picked.get("source") or "").endswith(("slug_exact", "question_exact", "token"))
            ):
                return picked
        except Exception:
            pass

    # Path C: markets?slug=
    if market_slug:
        try:
            data = fetch_with_retry(
                "https://gamma-api.polymarket.com/markets",
                params={"slug": str(market_slug)},
                max_retries=2,
                timeout=timeout,
            )
            markets = _collect_markets_from_payload(data)
            picked = _pick_best(markets, "gamma_markets_slug")
            if picked and picked.get("resolved"):
                return picked
        except Exception:
            pass

    return {
        "resolved": False,
        "yes_wins": None,
        "settle_price": None,
        "closed": False,
        "uma_status": "",
        "outcome_prices": None,
        "source": "unresolved_or_fetch_failed",
        "raw_question": market_question,
        "matched_slug": market_slug,
    }

