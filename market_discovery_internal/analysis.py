"""AI Analysis and weather evidence logic for market_discovery."""
from __future__ import annotations

import os
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from market_discovery_internal.config import (
    WEATHER_EVIDENCE_MAX_AGE_HOURS, WEATHER_EVIDENCE_MIN_QUALITY_SCORE,
    AI_MONTHLY_BUDGET_USD, AI_USAGE_LEDGER_FILE,
    HAIKU_ENTRY_ENABLED, HAIKU_ENTRY_CACHE_FILE, HAIKU_ENTRY_CACHE_TTL_HOURS,
    HAIKU_ENTRY_MODEL, HAIKU_ENTRY_MAX_TOKENS, HAIKU_ENTRY_MIN_CONFIDENCE,
    HAIKU_FAIL_OPEN,
    HAIKU_MONITOR_ENABLED, HAIKU_MONITOR_CACHE_FILE, HAIKU_MONITOR_MODEL,
    HAIKU_MONITOR_MAX_TOKENS, HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT,
    HAIKU_SENSING_ENABLED, HAIKU_SENSING_MODEL, HAIKU_SENSING_MAX_TOKENS,
    STATION_KNOWLEDGE_FILE, STATION_KNOWLEDGE_TTL_DAYS,
    ENTRY_BUCKET_HOLD_MIN_PROB, ENTRY_BUCKET_HOLD_MIN_EDGE,
    ENTRY_BUCKET_HOLD_MIN_CONFIDENCE,
    ENTRY_BUCKET_WATCH_MAX_PRICE,
    ANTHROPIC_API_KEY, AI_AGENT_ENABLED, AI_AGENT_TIMEOUT_SECONDS,
    PAPER_ENTRY_MIN_PRICE, PAPER_ENTRY_MAX_PRICE
)
from market_discovery_internal.utils import (
    _load_json_blob, _save_json_blob, _clamp, _safe_float, _safe_div
)

def build_weather_evidence(city: str, date: str, forecast_temp_c: Optional[float], source: str = "open-meteo", now_utc: Optional[datetime] = None, fetched_at: Any = None) -> dict[str, Any]:
    """Compile structured evidence for a market prediction based on forecast data."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    
    source = getattr(forecast_temp_c, 'source', source)
    
    evidence = {
        "city": city,
        "date": date,
        "forecast_temp_c": float(forecast_temp_c) if forecast_temp_c is not None else None,
        "source": source,
        "fetched_at": fetched_at or now_utc.isoformat(),
        "age_hours": 0.0,
        "quality_score": 0.0,
    }

    if fetched_at:
        try:
            if isinstance(fetched_at, datetime):
                prev = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=timezone.utc)
            else:
                prev = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            evidence["age_hours"] = round((now_utc - prev).total_seconds() / 3600, 2)
        except (ValueError, TypeError):
            pass

    if forecast_temp_c is not None:
        age_component = max(0, 1.0 - (evidence["age_hours"] / 72.0))
        # [FIX-H3] Use substring matching for source quality scoring.
        # Actual sources include "verified-triple-source", "verified-dual-source",
        # "verified-dual-source (METAR)", "open-meteo (bulk)", etc.
        _src = str(source).lower()
        if "triple" in _src or "dual" in _src:
            source_component = 0.95
        elif "open-meteo" in _src:
            source_component = 0.85
        else:
            source_component = 0.70
        evidence["quality_score"] = round(age_component * source_component, 2)

    return evidence

def is_weather_evidence_valid(evidence, max_age=WEATHER_EVIDENCE_MAX_AGE_HOURS, min_quality=WEATHER_EVIDENCE_MIN_QUALITY_SCORE):
    """Check if the provided weather evidence meets freshness and quality standards."""
    if not evidence or evidence.get("forecast_temp_c") is None:
        return False
    return (
        evidence["age_hours"] <= max_age and
        evidence["quality_score"] >= min_quality
    )

# ---------------------------------------------------------------------------
# AI Usage & Budgeting
# ---------------------------------------------------------------------------

def _ai_month_key(now_utc=None):
    if now_utc is None: now_utc = datetime.now(timezone.utc)
    return now_utc.strftime("%Y-%m")

def _ai_day_key(now_utc=None):
    if now_utc is None: now_utc = datetime.now(timezone.utc)
    return now_utc.strftime("%Y-%m-%d")

def _reserve_ai_call_slot(call_kind, now_utc=None):
    """Check if budget and daily call limits allow another AI call.

    Uses SQLite (atomic) as the primary source of truth.
    Falls back to JSON ledger if DB is unavailable.
    """
    if not ANTHROPIC_API_KEY:
        return False

    from market_discovery_internal.config import (
        HAIKU_ENTRY_MAX_CALLS_PER_DAY, HAIKU_MONITOR_MAX_CALLS_PER_DAY, HAIKU_SENSING_MAX_CALLS_PER_DAY
    )
    if call_kind == "haiku_entry":
        limit = HAIKU_ENTRY_MAX_CALLS_PER_DAY
    elif call_kind == "haiku_sensing":
        limit = HAIKU_SENSING_MAX_CALLS_PER_DAY
    else:
        limit = HAIKU_MONITOR_MAX_CALLS_PER_DAY

    m_key = _ai_month_key(now_utc)
    d_key = _ai_day_key(now_utc)

    try:
        from market_discovery_internal.database_manager import db
        # 1. Budget gate
        current_month_cost = db.ai_get_month_cost(m_key)
        if current_month_cost >= AI_MONTHLY_BUDGET_USD:
            return False
        # 2. Daily call gate — atomic reserve
        return db.ai_try_reserve_call(d_key, call_kind, limit)
    except Exception:
        logger.warning("[AI-BUDGET] DB unavailable for call slot reservation, falling back to JSON")
        # Fallback to JSON if DB unavailable (graceful degradation)
        try:
            ledger = _load_json_blob(AI_USAGE_LEDGER_FILE, {"monthly": {}, "daily_calls": {}})
            if m_key not in ledger["monthly"]:
                ledger["monthly"][m_key] = {"total_cost_usd": 0.0}
            if ledger["monthly"][m_key]["total_cost_usd"] >= AI_MONTHLY_BUDGET_USD:
                return False
            dk = f"{d_key}:{call_kind}"
            if ledger["daily_calls"].get(dk, 0) >= limit:
                return False
            ledger["daily_calls"][dk] = ledger["daily_calls"].get(dk, 0) + 1
            _save_json_blob(AI_USAGE_LEDGER_FILE, ledger)
            return True
        except Exception:
            logger.warning("[AI-BUDGET] JSON fallback also failed for call slot reservation")
            return False

def _record_ai_usage_cost(call_kind, model, response, now_utc=None):
    """Parse usage from AI response and update the cost ledger (SQLite, atomic)."""
    usage = _extract_response_usage(response)
    cost = _estimate_ai_usage_cost_usd(model, usage)
    m_key = _ai_month_key(now_utc)

    try:
        from market_discovery_internal.database_manager import db
        db.ai_add_month_cost(m_key, cost)
    except Exception:
        logger.warning("[AI-COST] DB unavailable for cost recording, falling back to JSON")
        # Fallback to JSON mirror
        try:
            ledger = _load_json_blob(AI_USAGE_LEDGER_FILE, {"monthly": {}, "daily_calls": {}})
            if m_key not in ledger["monthly"]:
                ledger["monthly"][m_key] = {"total_cost_usd": 0.0}
            ledger["monthly"][m_key]["total_cost_usd"] += cost
            _save_json_blob(AI_USAGE_LEDGER_FILE, ledger)
        except Exception:
            logger.warning("[AI-COST] JSON fallback also failed for cost recording")

def _extract_response_usage(response):
    if not hasattr(response, 'usage'): return {"input_tokens": 0, "output_tokens": 0}
    return {"input_tokens": getattr(response.usage, 'input_tokens', 0), 
            "output_tokens": getattr(response.usage, 'output_tokens', 0)}

def _estimate_ai_usage_cost_usd(model, usage_counts):
    # Anthropic pricing (2025)
    # Claude Haiku 4.5: $0.80/MTok input, $4.00/MTok output
    # Claude Haiku 3.x: $0.25/MTok input, $1.25/MTok output
    m = str(model).lower()
    in_t = usage_counts.get("input_tokens", 0)
    out_t = usage_counts.get("output_tokens", 0)

    if "haiku" in m:
        # haiku-4-5 is more expensive than haiku-3.x
        if "4-5" in m or "4_5" in m:
            return (in_t * (0.80/1_000_000)) + (out_t * (4.00/1_000_000))
        return (in_t * (0.25/1_000_000)) + (out_t * (1.25/1_000_000))
    return 0.0

# ---------------------------------------------------------------------------
# Anthropic API Helpers
# ---------------------------------------------------------------------------

def _extract_text_from_anthropic_response(response):
    try:
        return response.content[0].text
    except (AttributeError, IndexError):
        return ""

def _extract_json_payload(text):
    """Extract the first valid-looking JSON block from unstructured text."""
    if not text: return None

    # Priority 1: Try parsing the full text as JSON directly
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Priority 2: Find the outermost { ... } using bracket matching
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end+1])
    except (json.JSONDecodeError, ValueError):
        pass

    # Priority 3: Greedy regex fallback (least reliable)
    import re
    match = re.search(r'(\{.*\})', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    return None

def _anthropic_create_message(client, *, model, max_tokens, prompt):
    return client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )

# ---------------------------------------------------------------------------
# Haiku Entry Analysis
# ---------------------------------------------------------------------------

def _get_haiku_entry_cache():
    return _load_json_blob(HAIKU_ENTRY_CACHE_FILE, {})

def _save_haiku_entry_cache(cache):
    _save_json_blob(HAIKU_ENTRY_CACHE_FILE, cache)

def _haiku_entry_failure_result(reason):
    return {
        "recommendation": "enter" if HAIKU_FAIL_OPEN else "skip",
        "confidence": 0.5,
        "reasoning": f"Analysis failed: {reason}"
    }

def _haiku_entry_analysis(opportunity: dict[str, Any]) -> dict[str, Any]:
    """Use Claude Haiku to validate a high-alpha entry candidate."""
    if not HAIKU_ENTRY_ENABLED or not ANTHROPIC_API_KEY:
        return _haiku_entry_failure_result("Disabled or missing API key")

    cache = _get_haiku_entry_cache()
    token_id = opportunity.get("token_id")
    if token_id in cache:
        entry = cache[token_id]
        prev = datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - prev).total_seconds() / 3600
        if age < HAIKU_ENTRY_CACHE_TTL_HOURS:
            return entry["analysis"]

    if not _reserve_ai_call_slot("haiku_entry"):
        return _haiku_entry_failure_result("Budget or daily limit reached")

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        payload = {
            "market_question": opportunity.get("market_question"),
            "city": opportunity.get("city"),
            "target_date": opportunity.get("date"),
            "direction": opportunity.get("direction"),
            "threshold": opportunity.get("threshold"),
            "unit": opportunity.get("unit"),
            "current_yes_price": opportunity.get("yes_price"),
            "model_prob": opportunity.get("model_prob"),
            "edge": opportunity.get("edge"),
            "hours_until_resolve": opportunity.get("hours_until_resolve"),
            "forecast_temp_c": opportunity.get("forecast_temp_c"),
            "forecast_source": opportunity.get("forecast_source"),
        }

        prompt = (
            "SYSTEM: You are a paranoid weather trading auditor. Return ONLY a JSON object.\n"
            "CONTEXT: A bot calculated model_prob and edge using weather forecasts vs market price. "
            "Your job is a final sanity check — not to re-calculate edge, but to catch anomalies the formula misses.\n\n"
            "CHECK IN ORDER:\n"
            "1. Is forecast_temp_c physically plausible for this city and date? Flag impossible values.\n"
            "2. Is hours_until_resolve enough to act? If <6h, be very skeptical.\n"
            "3. Does the direction+threshold make sense given forecast_temp_c? "
            "(e.g. direction=above threshold=20 but forecast=14 means model_prob should be low — if high, something is wrong.)\n"
            "4. If edge < 0.15 or model_prob < 0.55, skip — margin too thin.\n"
            "5. If all checks pass, enter.\n\n"
            f"PAYLOAD: {json.dumps(payload)}\n\n"
            "Return ONLY: {\"recommendation\": \"enter\"|\"skip\", \"confidence\": 0.0-1.0, \"reasoning\": \"brief string\"}"
        )
        
        response = _anthropic_create_message(client, model=HAIKU_ENTRY_MODEL, max_tokens=HAIKU_ENTRY_MAX_TOKENS, prompt=prompt)
        _record_ai_usage_cost("haiku_entry", HAIKU_ENTRY_MODEL, response)
        
        text = _extract_text_from_anthropic_response(response)
        analysis = _extract_json_payload(text) or {}
        
        if not analysis or "recommendation" not in analysis:
            # Final fallback: if JSON is broken but we are in a high-stakes simulation, we log it.
            analysis = _haiku_entry_failure_result("Critical: AI returned non-compliant data format")

        cache[token_id] = {"at": datetime.now(timezone.utc).isoformat(), "analysis": analysis}
        _save_haiku_entry_cache(cache)
        return analysis
        
    except Exception as e:
        return _haiku_entry_failure_result(str(e))

# ---------------------------------------------------------------------------
# [MODUL A] Station Knowledge & Haiku Sensing
# ---------------------------------------------------------------------------

def _get_station_knowledge_cache():
    return _load_json_blob(STATION_KNOWLEDGE_FILE, {})

def _save_station_knowledge_cache(cache):
    _save_json_blob(STATION_KNOWLEDGE_FILE, cache)

def resolve_station_with_ai(city, description):
    """
    Attempt to extract a specific ICAO code from human-readable rules using AI.
    Includes a persistent cache to minimize costs.
    """
    if not HAIKU_SENSING_ENABLED or not ANTHROPIC_API_KEY or not description:
        return None

    # Normalization for cache key
    cache_key = f"{city.lower()}:{description.strip()[:200]}" # Use first 200 chars for key
    cache = _get_station_knowledge_cache()

    if cache_key in cache:
        entry = cache[cache_key]
        # Check TTL
        prev = datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - prev).days
        if age_days < STATION_KNOWLEDGE_TTL_DAYS:
            return entry.get("icao")

    # Reserve call slot
    if not _reserve_ai_call_slot("haiku_sensing"):
        return None

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        
        prompt = (
            "CRITICAL: Identify the weather station with 100% precision.\n"
            f"Context: Polymarket weather market for {city}.\n"
            f"Rules text: \"{description}\"\n\n"
            "Task: Locate the official ICAO/IATA code. If ambiguous, choose the most likely central station.\n"
            "If the text mentions 'Station NOT found' or similar anomaly, return null for icao.\n"
            "Return ONLY a JSON object: {\"station_name\": \"string\", \"icao\": \"4-letter-code-or-null\"}"
        )

        response = client.messages.create(
            model=HAIKU_SENSING_MODEL,
            max_tokens=HAIKU_SENSING_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}]
        )
        _record_ai_usage_cost("haiku_sensing", HAIKU_SENSING_MODEL, response)
        
        text = response.content[0].text
        data = _extract_json_payload(text)
        
        if data and data.get("icao"):
            icao = data["icao"].upper()
            # Cache it
            cache[cache_key] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "icao": icao,
                "station_name": data.get("station_name")
            }
            _save_station_knowledge_cache(cache)
            return icao
            
    except Exception as e:
        logger.error("[AI-SENSING] Error: %s", e)
        
    return None

# ---------------------------------------------------------------------------
# Haiku Position Monitor
# ---------------------------------------------------------------------------

def _get_haiku_monitor_cache():
    return _load_json_blob(HAIKU_MONITOR_CACHE_FILE, {})

def _save_haiku_monitor_cache(cache):
    _save_json_blob(HAIKU_MONITOR_CACHE_FILE, cache)

def _haiku_position_monitor(position: dict[str, Any], current_yes_price: Optional[float] = None, hours_until_resolve: Optional[float] = None, current_forecast_temp_c: Optional[float] = None) -> dict[str, Any]:
    """Use Claude Haiku to monitor an open position for unexpected risks."""
    if not HAIKU_MONITOR_ENABLED or not ANTHROPIC_API_KEY:
        return {"action": "hold", "confidence": 1.0}

    # Cache: only re-query Haiku every HAIKU_MONITOR_INTERVAL_HOURS per token
    cache = _get_haiku_monitor_cache()
    token_id = str(position.get("token_id", ""))
    if token_id and token_id in cache:
        entry = cache[token_id]
        try:
            prev = datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - prev).total_seconds() / 3600
            from market_discovery_internal.config import HAIKU_MONITOR_INTERVAL_HOURS
            if age_hours < HAIKU_MONITOR_INTERVAL_HOURS:
                return entry["result"]
        except Exception:
            logger.debug("[HAIKU-MONITOR] Cache entry parse failed for token %s", token_id)

    if not _reserve_ai_call_slot("haiku_monitor"):
        return {"action": "hold", "confidence": 1.0, "reasoning": "Budget or daily limit reached"}

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)

        entry_forecast = position.get("forecast_temp_c")
        forecast_drift = None
        if current_forecast_temp_c is not None and entry_forecast is not None:
            try:
                forecast_drift = round(float(current_forecast_temp_c) - float(entry_forecast), 2)
            except (TypeError, ValueError):
                pass

        entry_price = position.get("entry_price")
        try:
            _ep = float(entry_price) if entry_price is not None else 0.0
            pnl_pct = round(((float(current_yes_price) - _ep) / _ep) * 100, 1) if _ep > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            pnl_pct = None

        payload = {
            "city": position.get("city"),
            "market_question": position.get("market_question"),
            "direction": position.get("direction"),
            "threshold": position.get("threshold"),
            "unit": position.get("unit"),
            "target_date": position.get("date"),
            "entry_price": entry_price,
            "current_yes_price": round(float(current_yes_price), 4) if current_yes_price is not None else None,
            "pnl_pct": pnl_pct,
            "hours_until_resolve": round(float(hours_until_resolve), 1) if hours_until_resolve is not None else None,
            "entry_model_prob": position.get("entry_model_prob"),
            "entry_edge": position.get("entry_edge"),
            "forecast_temp_at_entry_c": entry_forecast,
            "forecast_temp_now_c": current_forecast_temp_c,
            "forecast_drift_c": forecast_drift,
        }

        prompt = (
            "You are a risk sentinel for a weather prediction market. Return ONLY a JSON object.\n"
            "CONTEXT: A bot opened this position based on a weather forecast vs market price edge. "
            "You must decide: hold or close? Base your decision on the signals below.\n\n"
            "DECISION RULES:\n"
            "1. forecast_drift_c = how much forecast shifted since entry. "
            "Drift that moves AGAINST the thesis (e.g. direction=above but forecast dropped) = strong close signal.\n"
            "2. pnl_pct < -30% AND forecast drifted against thesis → close.\n"
            "3. pnl_pct < -30% BUT forecast_drift_c is small/None → market panic, NOT thesis failure → hold or cautious.\n"
            "4. hours_until_resolve <= 2 AND pnl_pct still deeply negative → close, cut losses.\n"
            "5. hours_until_resolve <= 2 AND position profitable OR flat → hold to resolve.\n"
            "6. If unsure, default to hold — do NOT close profitable or flat positions without clear reason.\n\n"
            f"POSITION: {json.dumps(payload)}\n\n"
            "Return ONLY: {\"action\": \"hold\"|\"close\", \"confidence\": 0.0-1.0, \"reasoning\": \"brief string\"}"
        )

        response = _anthropic_create_message(client, model=HAIKU_MONITOR_MODEL, max_tokens=HAIKU_MONITOR_MAX_TOKENS, prompt=prompt)
        text = _extract_text_from_anthropic_response(response)
        result = _extract_json_payload(text)
        
        if not result or not isinstance(result, dict):
            return {"action": "hold", "confidence": 1.0, "reasoning": "Malformed AI response"}

        # [FIX] Profit Guard + Newborn Guard: Override AI if position is winning OR too new.
        # This protects from 'False Panic' during price feed lags and spread noise.
        if result.get("action") == "close":
            try:
                cur_pnl = float(pnl_pct) if pnl_pct is not None else 0.0

                # Calculate position age in hours
                age_hours = 0.0
                opened_at_str = position.get("opened_at")
                if opened_at_str:
                    try:
                        from datetime import datetime, timezone
                        opened_at = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                        age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                    except Exception:
                        logger.debug("[NEWBORN-GUARD] Failed to parse opened_at for age calculation")

                is_newborn = age_hours < 2.0  # Position under 2 hours old

                if cur_pnl > 5.0:
                    # Clearly profitable — block exit
                    result["action"] = "hold"
                    result["reasoning"] = f"[PROFIT-GUARD] Blocked: P&L={cur_pnl:.1f}% > 5%. " + result.get("reasoning", "")
                elif is_newborn and cur_pnl > -15.0:
                    # Position under 2h old, not deeply underwater — allow spread to settle
                    result["action"] = "hold"
                    result["reasoning"] = f"[NEWBORN-GUARD] Blocked: Age={age_hours:.1f}h < 2h, P&L={cur_pnl:.1f}%. Spread still settling. " + result.get("reasoning", "")
            except Exception:
                logger.warning("[PROFIT-GUARD] Guard evaluation failed for position %s", token_id)

        _record_ai_usage_cost("haiku_monitor", HAIKU_MONITOR_MODEL, response)
        
        # Save to cache
        cache[token_id] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "result": result
        }
        _save_haiku_monitor_cache(cache)
        return result


    except Exception as e:
        return {"action": "hold", "confidence": 1.0, "reasoning": f"Error: {e}"}

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
        "ai_strategy": "hold_until_resolve" if bucket == "enter_hold_candidate" else "swing"
    }

def _call_ai_provider(payload, config):
    """Call AI provider adapter (mock/deterministic fallback supported)."""
    provider = config.get("provider", "")
    if provider == "mock":
        price = _safe_float(payload.get("yes_price"), 1.0)
        model_prob = _safe_float(payload.get("model_prob"), 0.0)
        edge = _safe_float(payload.get("edge"), 0.0)

        if price < PAPER_ENTRY_MIN_PRICE:
            bucket = "watchlist"
        elif (model_prob >= ENTRY_BUCKET_HOLD_MIN_PROB and edge >= ENTRY_BUCKET_HOLD_MIN_EDGE):
            bucket = "enter_hold_candidate"
        elif PAPER_ENTRY_MIN_PRICE <= price <= PAPER_ENTRY_MAX_PRICE:
            bucket = "enter_swing"
        else:
            bucket = "reject"
        return {"ai_bucket": bucket, "ai_confidence": 0.85}

    raise NotImplementedError(f"AI provider adapter not implemented: {provider}")

def _maybe_apply_ai_decision(opportunity):
    """Optionally enrich opportunity with AI bucket signals."""
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

def decide_entry_bucket(opportunity, min_entry_price, max_entry_price):
    """Classify an opportunity into action buckets based on price/prob goals.

    Priority order:
    1. enter_hold_candidate — high-confidence, high-edge positions
    2. enter_swing          — mid-range price within entry bounds (above watchlist threshold)
    3. watchlist            — cheap but interesting (below watchlist threshold)
    4. reject               — price outside valid entry range or too expensive
    """
    # 1. AI-Driven Decision (Override)
    if HAIKU_ENTRY_ENABLED and opportunity.get("ai_bucket"):
        return {"bucket": opportunity["ai_bucket"], "reason": "ai_decision", "confidence": opportunity.get("ai_confidence", 1.0)}

    # Legacy AI support (if any)
    if opportunity.get("ai_bucket"):
        return {"bucket": opportunity["ai_bucket"], "reason": "ai_decision", "confidence": 1.0}

    prob = opportunity.get("model_prob", 0.0)
    edge = opportunity.get("edge", 0.0)
    price = opportunity.get("yes_price", 1.0)

    prob_pct = f"{prob*100:.1f}%"
    edge_pct = f"{edge*100:.1f}%"
    _min = float(min_entry_price)
    _max = float(max_entry_price)

    # HOLD: high-conviction signal — hold through to resolution.
    # Measured collection: also accept hold_confidence path so hold bucket is not dead.
    try:
        _hold_conf = float(opportunity.get("hold_confidence") or opportunity.get("hold_conf") or 0.0)
    except (TypeError, ValueError):
        _hold_conf = 0.0
    _hold_ok = (
        (prob >= ENTRY_BUCKET_HOLD_MIN_PROB and edge >= ENTRY_BUCKET_HOLD_MIN_EDGE)
        or (
            _hold_conf >= ENTRY_BUCKET_HOLD_MIN_CONFIDENCE
            and edge >= max(0.12, ENTRY_BUCKET_HOLD_MIN_EDGE * 0.5)
            and prob >= max(0.35, ENTRY_BUCKET_HOLD_MIN_PROB * 0.6)
        )
    )
    if _hold_ok and PAPER_ENTRY_MIN_PRICE <= price <= _max:
        return {
            "bucket": "enter_hold_candidate",
            "strategy": "hold_until_resolve",
            "reason": (
                f"HOLD path: Prob {prob_pct}, Edge {edge_pct}, hold_conf={_hold_conf:.2f} "
                f"(min prob/edge {ENTRY_BUCKET_HOLD_MIN_PROB*100:.0f}%/{ENTRY_BUCKET_HOLD_MIN_EDGE*100:.0f}% "
                f"or hold_conf>={ENTRY_BUCKET_HOLD_MIN_CONFIDENCE:.2f})."
            ),
            "confidence": round(max(prob, _hold_conf), 4),
        }

    # SWING: price in the mid-range above the watchlist ceiling — enter now
    # [FIX] Exclude exact brackets — they have their own stricter gate below
    _opp_direction = opportunity.get("direction", "")
    if _opp_direction != "exact" and ENTRY_BUCKET_WATCH_MAX_PRICE < price <= _max:
        return {
            "bucket": "enter_swing",
            "strategy": "swing",
            "reason": (
                f"Price USD {price:.2f} in swing range "
                f"[{ENTRY_BUCKET_WATCH_MAX_PRICE:.2f}-{_max:.2f}] with Prob {prob_pct}, Edge {edge_pct}."
            ),
            "confidence": round(prob, 4),
        }

    # EXACT BRACKET: cheap, high-probability, high-edge entries only
    # Exact brackets must pass strict quality gates: min prob 25%, min edge 10%, max price $0.15.
    # Plus proximity filter: forecast must be within EXACT_PROXIMITY_SIGMA × σ of threshold.
    # Plus market-forecast gap gate (skip if market mode far from model).
    from market_discovery_internal.config import (
        STRATEGY_EXACT_MIN_MODEL_PROB, STRATEGY_EXACT_MIN_EDGE, STRATEGY_EXACT_MAX_ENTRY_PRICE,
        EXACT_PROXIMITY_SIGMA,
    )
    direction = opportunity.get("direction", "")
    _exact_max = min(_max, STRATEGY_EXACT_MAX_ENTRY_PRICE)
    if direction == "exact":
        # Attach research fields if missing
        try:
            from market_discovery_internal.research_features import (
                attach_research_fields, passes_market_gap_gate, forecast_bracket_distance_c,
            )
            if "forecast_bracket_distance_c" not in opportunity:
                attach_research_fields(opportunity)
            gap_ok, gap_reason = passes_market_gap_gate(opportunity)
            if not gap_ok:
                return {
                    "bucket": "reject",
                    "strategy": "swing",
                    "reason": f"Exact rejected: {gap_reason}",
                    "confidence": round(prob, 4),
                }
        except Exception:
            gap_ok, gap_reason = True, "gap_check_skipped"

        # [PROXIMITY FILTER] Only enter exact brackets where forecast is close to threshold.
        _proximity_ok = True
        _distance = opportunity.get("forecast_bracket_distance_c")
        _threshold_c = opportunity.get("threshold")
        _evidence = opportunity.get("weather_evidence") or {}
        _forecast_c = _evidence.get("forecast_temp_c") or opportunity.get("forecast_temp_c")
        _sigma = None
        if _threshold_c is not None and _forecast_c is not None:
            from market_discovery_internal.pricing import _get_city_sigma
            _city = opportunity.get("city", "")
            _date = opportunity.get("date", "")
            _sigma = _get_city_sigma(_city, _date)
            _max_distance = float(EXACT_PROXIMITY_SIGMA) * float(_sigma)
            if _distance is None:
                # Convert threshold to C if needed
                _thr = float(_threshold_c)
                if str(opportunity.get("unit", "C")).upper() == "F":
                    _thr = (_thr - 32.0) * 5.0 / 9.0
                _distance = abs(float(_forecast_c) - _thr)
            if float(_distance) > _max_distance:
                _proximity_ok = False

        if not _proximity_ok:
            return {
                "bucket": "reject",
                "strategy": "swing",
                "reason": (
                    f"Exact rejected: proximity dist={_distance} "
                    f"> {EXACT_PROXIMITY_SIGMA}σ (σ={_sigma})."
                ),
                "confidence": round(prob, 4),
            }

        if (_proximity_ok
                and _min <= price <= _exact_max
                and edge >= STRATEGY_EXACT_MIN_EDGE
                and prob >= STRATEGY_EXACT_MIN_MODEL_PROB):
            # [CRITICAL FIX] Run full consensus gate before entering exact brackets.
            # This enforces max_prob, forecast gap, dead market, and golden window checks
            # that were previously bypassed in the live entry path (only called in shadow logging).
            try:
                from market_discovery_internal.research_features import passes_market_consensus_gate
                _gate_ok, _gate_reason = passes_market_consensus_gate(opportunity)
                if not _gate_ok:
                    return {
                        "bucket": "reject",
                        "strategy": "swing",
                        "reason": f"Exact rejected by consensus gate: {_gate_reason}",
                        "confidence": round(prob, 4),
                    }
            except Exception as _gate_exc:
                logger.warning("[BUCKET] Consensus gate check failed for %s: %s", opportunity.get("city", "?"), _gate_exc)
            return {
                "bucket": "enter_swing",
                "strategy": "swing",
                "reason": (
                    f"Exact bracket USD {price:.2f} with Prob {prob_pct}, Edge {edge_pct}. "
                    f"dist={_distance}C max=${_exact_max:.2f}."
                ),
                "confidence": round(prob, 4),
            }

    # WATCHLIST: cheap markets within the entry floor — monitor but don't enter yet
    if _min <= price <= ENTRY_BUCKET_WATCH_MAX_PRICE:
        return {
            "bucket": "watchlist",
            "strategy": "swing",
            "reason": (
                f"Price USD {price:.2f} in WATCHLIST range "
                f"[{_min:.2f}-{ENTRY_BUCKET_WATCH_MAX_PRICE:.2f}]. Monitoring."
            ),
        }

    # REJECT: price outside valid entry range
    return {
        "bucket": "reject",
        "strategy": "swing",
        "reason": (
            f"Price USD {price:.2f} outside valid entry range [{_min:.2f}-{_max:.2f}]. "
            f"Prob {prob_pct}, Edge {edge_pct}."
        ),
    }
