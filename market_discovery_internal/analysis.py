"""AI Analysis and weather evidence logic for market_discovery."""

import os
import json
import time
from datetime import datetime, timezone
import math

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

    from market_discovery_internal.config import (
        HAIKU_ENTRY_MAX_CALLS_PER_DAY, HAIKU_MONITOR_MAX_CALLS_PER_DAY, HAIKU_SENSING_MAX_CALLS_PER_DAY
    )
    if call_kind == "haiku_entry":
        limit = HAIKU_ENTRY_MAX_CALLS_PER_DAY
    elif call_kind == "haiku_sensing":
        limit = HAIKU_SENSING_MAX_CALLS_PER_DAY
    else:
        limit = HAIKU_MONITOR_MAX_CALLS_PER_DAY
    
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
    """Extract the first valid-looking JSON block from unstructured text using robust regex."""
    if not text: return None
    import re
    # Match the first '{' and corresponding last '}' even with whitespace/newlines
    match = re.search(r'(\{.*\})', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
            
    # Fallback to older bracket searching if regex somehow misses
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

def _haiku_entry_analysis(opportunity):
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
            "forecast_temp_c": opportunity.get("forecast_temp_c"),
            "forecast_source": opportunity.get("forecast_source"),
        }
        
        prompt = (
            "SYSTEM: You are a clinical, paranoid weather trading auditor. Return ONLY a JSON object.\n"
            "TASKS:\n"
            "1. Scrutinize the payload below for anomalies (impossible temps, missing units, stale metadata).\n"
            "2. Decide if the trade is 'enter' or 'skip'.\n"
            "3. If ANY anomaly or extreme inconsistency exists, you MUST 'skip'.\n\n"
            f"PAYLOAD: {json.dumps(payload)}\n\n"
            "FORMAT: {\"recommendation\": \"enter\"/\"skip\", \"confidence\": 0.0-1.0, \"reasoning\": \"brief string\"}"
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
        print(f"[AI-SENSING] Error: {e}")
        
    return None

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
            pass

    if not _reserve_ai_call_slot("haiku_monitor"):
        return {"action": "hold", "confidence": 1.0, "reasoning": "Budget or daily limit reached"}

    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)

        payload = {
            "city": position.get("city"),
            "market_question": position.get("market_question"),
            "direction": position.get("direction"),
            "threshold": position.get("threshold"),
            "unit": position.get("unit"),
            "target_date": position.get("date"),
            "entry_price": position.get("entry_price"),
            "current_yes_price": round(float(current_yes_price), 4) if current_yes_price is not None else None,
            "hours_until_resolve": round(float(hours_until_resolve), 1) if hours_until_resolve is not None else None,
            "entry_model_prob": position.get("entry_model_prob"),
            "forecast_temp_c": position.get("forecast_temp_c"),
        }

        prompt = (
            "CRITICAL: You are an aggressive risk sentinel.\n"
            f"Active Position: {json.dumps(payload)}\n\n"
            "VULNERABILITY CHECK:\n"
            "1. If the current price suggests a massive deviation from the forecast, analyze why.\n"
            "2. If hours until resolution is very low and price is stalled, consider closing.\n"
            "Return JSON only: {\"action\": \"hold\"|\"close\", \"confidence\": 0.0-1.0, \"reasoning\": \"brief string\"}"
        )

        response = _anthropic_create_message(
            client,
            model=HAIKU_MONITOR_MODEL,
            max_tokens=HAIKU_MONITOR_MAX_TOKENS,
            prompt=prompt,
        )
        _record_ai_usage_cost("haiku_monitor", HAIKU_MONITOR_MODEL, response)

        text = _extract_text_from_anthropic_response(response)
        result = _extract_json_payload(text)

        if not isinstance(result, dict) or "action" not in result:
            result = {"action": "hold", "confidence": 1.0, "reasoning": "Parse failed"}

        # Clamp confidence
        try:
            result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 1.0))))
        except (TypeError, ValueError):
            result["confidence"] = 1.0

        # Cache the result
        if token_id:
            cache[token_id] = {"at": datetime.now(timezone.utc).isoformat(), "result": result}
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

    # HOLD: highest-conviction signal — hold through to resolution
    if prob >= ENTRY_BUCKET_HOLD_MIN_PROB and edge >= ENTRY_BUCKET_HOLD_MIN_EDGE:
        return {
            "bucket": "enter_hold_candidate",
            "strategy": "hold_until_resolve",
            "reason": (
                f"Prob {prob_pct} & Edge {edge_pct} meet HOLD criteria "
                f"(Min {ENTRY_BUCKET_HOLD_MIN_PROB*100:.0f}%/{ENTRY_BUCKET_HOLD_MIN_EDGE*100:.0f}%)."
            ),
            "confidence": round(prob, 4),
        }

    # SWING: price in the mid-range above the watchlist ceiling — enter now
    if ENTRY_BUCKET_WATCH_MAX_PRICE < price <= _max:
        return {
            "bucket": "enter_swing",
            "strategy": "swing",
            "reason": (
                f"Price USD {price:.2f} in swing range "
                f"[{ENTRY_BUCKET_WATCH_MAX_PRICE:.2f}-{_max:.2f}] with Prob {prob_pct}, Edge {edge_pct}."
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
