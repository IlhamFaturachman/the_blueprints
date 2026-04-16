"""AI Analysis and weather evidence logic for market_discovery."""

import os
import json
import time
from datetime import datetime, timezone
import math

from market_discovery_internal.config import (
    WEATHER_EVIDENCE_MAX_AGE_HOURS, WEATHER_EVIDENCE_MIN_QUALITY_SCORE,
    AI_MONTHLY_BUDGET_USD, AI_USAGE_LEDGER_FILE,
    SONNET_ENTRY_ENABLED, SONNET_ENTRY_CACHE_FILE, SONNET_ENTRY_CACHE_TTL_HOURS,
    SONNET_ENTRY_MODEL, SONNET_ENTRY_MAX_TOKENS, SONNET_ENTRY_MIN_CONFIDENCE,
    HAIKU_MONITOR_ENABLED, HAIKU_MONITOR_CACHE_FILE, HAIKU_MONITOR_MODEL,
    HAIKU_MONITOR_MAX_TOKENS, HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT,
    ENTRY_BUCKET_HOLD_MIN_PROB, ENTRY_BUCKET_HOLD_MIN_EDGE,
    ENTRY_BUCKET_WATCH_MAX_PRICE,
    ANTHROPIC_API_KEY, AI_AGENT_ENABLED, AI_AGENT_TIMEOUT_SECONDS,
    PAPER_ENTRY_MIN_PRICE, PAPER_ENTRY_MAX_PRICE
)
from market_discovery_internal.utils import (
    _load_json_blob, _save_json_blob, _clamp, _safe_float, _safe_div
)

def build_weather_evidence(city, date, forecast_temp_c, source="open-meteo", now_utc=None, fetched_at=None):
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
            prev = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            evidence["age_hours"] = round((now_utc - prev).total_seconds() / 3600, 2)
        except (ValueError, TypeError):
            pass

    if forecast_temp_c is not None:
        age_component = max(0, 1.0 - (evidence["age_hours"] / 72.0))
        if source == "dual-source":
            source_component = 0.95
        elif source == "open-meteo":
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

def _ensure_ai_usage_ledger(now_utc=None):
    ledger = _load_json_blob(AI_USAGE_LEDGER_FILE, {"monthly": {}, "daily_calls": {}})
    m_key = _ai_month_key(now_utc)
    if m_key not in ledger["monthly"]:
        ledger["monthly"][m_key] = {"total_cost_usd": 0.0}
    return ledger

def _save_ai_usage_ledger(ledger):
    _save_json_blob(AI_USAGE_LEDGER_FILE, ledger)

def _reserve_ai_call_slot(call_kind, now_utc=None):
    """Check if budget and daily call limits allow another AI call."""
    if not ANTHROPIC_API_KEY: return False
    
    ledger = _ensure_ai_usage_ledger(now_utc)
    m_key = _ai_month_key(now_utc)
    current_month_cost = ledger["monthly"][m_key]["total_cost_usd"]
    
    if current_month_cost >= AI_MONTHLY_BUDGET_USD:
        return False

    from market_discovery_internal.config import SONNET_ENTRY_MAX_CALLS_PER_DAY, HAIKU_MONITOR_MAX_CALLS_PER_DAY
    limit = SONNET_ENTRY_MAX_CALLS_PER_DAY if call_kind == "sonnet_entry" else HAIKU_MONITOR_MAX_CALLS_PER_DAY
    
    d_key = f"{_ai_day_key(now_utc)}:{call_kind}"
    current_calls = ledger["daily_calls"].get(d_key, 0)
    
    if current_calls >= limit:
        return False
        
    ledger["daily_calls"][d_key] = current_calls + 1
    _save_ai_usage_ledger(ledger)
    return True

def _record_ai_usage_cost(call_kind, model, response, now_utc=None):
    """Parse usage from AI response and update the cost ledger."""
    usage = _extract_response_usage(response)
    cost = _estimate_ai_usage_cost_usd(model, usage)
    
    ledger = _ensure_ai_usage_ledger(now_utc)
    m_key = _ai_month_key(now_utc)
    ledger["monthly"][m_key]["total_cost_usd"] += cost
    _save_ai_usage_ledger(ledger)

def _extract_response_usage(response):
    if not hasattr(response, 'usage'): return {"input_tokens": 0, "output_tokens": 0}
    return {"input_tokens": getattr(response.usage, 'input_tokens', 0), 
            "output_tokens": getattr(response.usage, 'output_tokens', 0)}

def _estimate_ai_usage_cost_usd(model, usage_counts):
    # Rough Anthropic pricing (Oct 2024)
    # Sonnet 3.5: $3/MTok input, $15/MTok output
    # Haiku 3.5: $0.25/MTok input, $1.25/MTok output
    m = str(model).lower()
    in_t = usage_counts.get("input_tokens", 0)
    out_t = usage_counts.get("output_tokens", 0)
    
    if "sonnet" in m:
        return (in_t * (3.0/1_000_000)) + (out_t * (15.0/1_000_000))
    if "haiku" in m:
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
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end+1])
    except json.JSONDecodeError:
        pass
    return None

def _anthropic_create_message(client, *, model, max_tokens, prompt):
    return client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )

# ---------------------------------------------------------------------------
# Sonnet Entry Analysis
# ---------------------------------------------------------------------------

def _get_sonnet_entry_cache():
    return _load_json_blob(SONNET_ENTRY_CACHE_FILE, {})

def _save_sonnet_entry_cache(cache):
    _save_json_blob(SONNET_ENTRY_CACHE_FILE, cache)

def _sonnet_failure_result(reason):
    from market_discovery_internal.config import SONNET_FAIL_OPEN
    return {
        "recommendation": "enter" if SONNET_FAIL_OPEN else "skip",
        "confidence": 0.5,
        "reasoning": f"Analysis failed: {reason}"
    }

def _sonnet_entry_analysis(opportunity):
    """Use Claude Sonnet to validate a high-alpha entry candidate."""
    if not SONNET_ENTRY_ENABLED or not ANTHROPIC_API_KEY:
        return _sonnet_failure_result("Disabled or missing API key")

    cache = _get_sonnet_entry_cache()
    token_id = opportunity.get("token_id")
    if token_id in cache:
        entry = cache[token_id]
        prev = datetime.fromisoformat(entry["at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - prev).total_seconds() / 3600
        if age < SONNET_ENTRY_CACHE_TTL_HOURS:
            return entry["analysis"]

    if not _reserve_ai_call_slot("sonnet_entry"):
        return _sonnet_failure_result("Budget or daily limit reached")

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
            "forecast_temp_c": opportunity.get("forecast_temp_c"),
            "forecast_source": opportunity.get("forecast_source"),
        }
        
        prompt = (
            f"Analyze this Polymarket weather prediction: {json.dumps(payload)}\n"
            "Return JSON: {\"recommendation\": \"enter\"|\"skip\", \"confidence\": 0.0-1.0, \"reasoning\": \"string\"}"
        )
        
        response = _anthropic_create_message(client, model=SONNET_ENTRY_MODEL, max_tokens=SONNET_ENTRY_MAX_TOKENS, prompt=prompt)
        _record_ai_usage_cost("sonnet_entry", SONNET_ENTRY_MODEL, response)
        
        text = _extract_text_from_anthropic_response(response)
        analysis = _extract_json_payload(text)
        
        if not analysis or "recommendation" not in analysis:
            analysis = _sonnet_failure_result("Invalid AI response format")

        cache[token_id] = {"at": datetime.now(timezone.utc).isoformat(), "analysis": analysis}
        _save_sonnet_entry_cache(cache)
        return analysis
        
    except Exception as e:
        return _sonnet_failure_result(str(e))

# ---------------------------------------------------------------------------
# Haiku Position Monitor
# ---------------------------------------------------------------------------

def _get_haiku_monitor_cache():
    return _load_json_blob(HAIKU_MONITOR_CACHE_FILE, {})

def _save_haiku_monitor_cache(cache):
    _save_json_blob(HAIKU_MONITOR_CACHE_FILE, cache)

def _haiku_position_monitor(position, current_yes_price=None, hours_until_resolve=None):
    """Use Claude Haiku to monitor an open position for unexpected risks."""
    if not HAIKU_MONITOR_ENABLED or not ANTHROPIC_API_KEY:
        return {"action": "hold", "confidence": 1.0}

    # Simplified: Haiku logic here would check news or updated trends.
    # For now, return hold as default placeholder for WAVE 1 logic.
    return {"action": "hold", "confidence": 1.0}

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
    """Classify an opportunity into action buckets based on price/prob goals."""
    # If AI has already decided a bucket, use it.
    if opportunity.get("ai_bucket"):
        return {"bucket": opportunity["ai_bucket"], "reason": "ai_decision"}

    prob = opportunity.get("model_prob", 0.0)
    edge = opportunity.get("edge", 0.0)
    price = opportunity.get("yes_price", 1.0)
    
    if prob >= ENTRY_BUCKET_HOLD_MIN_PROB and edge >= ENTRY_BUCKET_HOLD_MIN_EDGE:
        return {"bucket": "enter_hold_candidate", "reason": "high_edge_hold"}
    if price <= ENTRY_BUCKET_WATCH_MAX_PRICE:
        return {"bucket": "watchlist", "reason": "low_price_watch"}
    return {"bucket": "reject", "reason": "low_edge_or_prob"}
