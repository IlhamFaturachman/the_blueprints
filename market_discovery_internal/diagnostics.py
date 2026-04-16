"""Discovery diagnostics helpers for market_discovery."""


def collect_discovery_evidence_and_ai(enriched_markets):
    """Aggregate evidence validity and AI status counters for diagnostics."""
    evidence_valid = 0
    evidence_invalid = 0
    ai_status_counts = {
        "off": 0,
        "applied": 0,
        "missing_config": 0,
        "fallback_error": 0,
        "unknown": 0,
    }

    for enriched_market in enriched_markets:
        if enriched_market.get("weather_evidence_valid"):
            evidence_valid += 1
        else:
            evidence_invalid += 1

        ai_status = str(enriched_market.get("ai_status") or "unknown")
        if ai_status not in ai_status_counts:
            ai_status = "unknown"
        ai_status_counts[ai_status] += 1

    return evidence_valid, evidence_invalid, ai_status_counts


def collect_discovery_bucket_counts(
    opportunities,
    *,
    decide_entry_bucket_fn,
    paper_entry_min_price,
    paper_entry_max_price,
):
    """Aggregate entry bucket and reason counters for diagnostics."""
    bucket_counts = {
        "reject": 0,
        "watchlist": 0,
        "enter_swing": 0,
        "enter_hold_candidate": 0,
    }
    bucket_reason_counts = {}

    for opportunity in opportunities:
        bucket = decide_entry_bucket_fn(opportunity, paper_entry_min_price, paper_entry_max_price)
        bucket_name = bucket.get("bucket", "reject")
        bucket_counts[bucket_name] = bucket_counts.get(bucket_name, 0) + 1

        reason = bucket.get("reason", "unknown")
        bucket_reason_counts[reason] = bucket_reason_counts.get(reason, 0) + 1

    return bucket_counts, bucket_reason_counts


def classify_discovery_rejection_reason(has_city, has_threshold, has_weather, has_direction):
    """Classify why a raw market is rejected from temperature-candidate stage."""
    if not has_city:
        return "no_city"
    if not has_threshold:
        return "no_threshold"
    if not (has_weather or has_direction):
        return "no_weather_context"
    return "other"


def analyze_discovery_raw_market(
    raw,
    *,
    market_search_text_fn,
    has_target_city_fn,
    threshold_re,
    weather_context_re,
    direction_candidate_re,
):
    """Return candidate-stage signals for one raw market item."""
    question = raw.get("question") or raw.get("title") or ""
    text = market_search_text_fn(raw)
    lower = text.lower()

    has_city = has_target_city_fn(lower)
    has_threshold = bool(threshold_re.search(text))
    has_weather = bool(weather_context_re.search(lower))
    has_direction = bool(direction_candidate_re.search(lower))

    return {
        "question": question,
        "has_city": has_city,
        "has_threshold": has_threshold,
        "has_weather": has_weather,
        "has_direction": has_direction,
        "is_candidate": has_city and has_threshold and (has_weather or has_direction),
    }


def build_discovery_diagnostics(
    discovery,
    *,
    collect_discovery_evidence_and_ai_fn,
    collect_discovery_bucket_counts_fn,
    analyze_discovery_raw_market_fn,
    classify_discovery_rejection_reason_fn,
    decide_entry_bucket_fn,
    paper_entry_min_price,
    paper_entry_max_price,
    daily_resolve_only,
    daily_min_hours_to_resolve,
):
    """Build drop-off statistics to debug why opportunities are zero."""
    raw_markets = discovery.get("markets_raw", [])
    sample_rejections = []
    daily_skip_reasons = discovery.get("daily_skip_reasons") if isinstance(discovery.get("daily_skip_reasons"), dict) else {}
    daily_date_mismatch = int(daily_skip_reasons.get("daily_date_mismatch", 0))
    daily_min_hours_not_met = int(daily_skip_reasons.get("daily_min_hours_not_met", 0))
    too_close_to_resolve = int(daily_skip_reasons.get("too_close_to_resolve", 0))

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
        "evidence_valid": 0,
        "evidence_invalid": 0,
        "daily_date_mismatch": daily_date_mismatch,
        "daily_min_hours_not_met": daily_min_hours_not_met,
        "too_close_to_resolve": too_close_to_resolve,
        "daily_skipped_total": daily_date_mismatch + daily_min_hours_not_met + too_close_to_resolve,
        "daily_mode_enabled": bool(discovery.get("daily_resolve_only", daily_resolve_only)),
        "daily_min_hours_to_resolve": float(
            discovery.get("daily_min_hours_to_resolve", daily_min_hours_to_resolve)
        ),
    }

    evidence_valid, evidence_invalid, ai_status_counts = collect_discovery_evidence_and_ai_fn(
        discovery.get("enriched", [])
    )
    counts["evidence_valid"] = evidence_valid
    counts["evidence_invalid"] = evidence_invalid

    bucket_counts, bucket_reason_counts = collect_discovery_bucket_counts_fn(
        discovery.get("opportunities", []),
        decide_entry_bucket_fn=decide_entry_bucket_fn,
        paper_entry_min_price=paper_entry_min_price,
        paper_entry_max_price=paper_entry_max_price,
    )

    for raw in raw_markets:
        analysis = analyze_discovery_raw_market_fn(raw)
        question = analysis["question"]
        has_city = analysis["has_city"]
        has_threshold = analysis["has_threshold"]
        has_weather = analysis["has_weather"]
        has_direction = analysis["has_direction"]

        if has_city:
            counts["city_match"] += 1
        if has_threshold:
            counts["temperature_hint"] += 1
        if has_weather:
            counts["weather_context"] += 1
        if has_direction:
            counts["direction_hint"] += 1

        if analysis["is_candidate"]:
            counts["temperature_candidates"] += 1
        elif len(sample_rejections) < 5 and question:
            reason = classify_discovery_rejection_reason_fn(
                has_city=has_city,
                has_threshold=has_threshold,
                has_weather=has_weather,
                has_direction=has_direction,
            )
            sample_rejections.append({"reason": reason, "question": question})

    performance = discovery.get("performance") if isinstance(discovery.get("performance"), dict) else {}

    return {
        **counts,
        "failed_cities": list(discovery.get("failed_cities", [])),
        "skipped_markets": discovery.get("skipped_markets", 0),
        "exact_skipped": discovery.get("exact_skipped", 0),
        "bucket_counts": bucket_counts,
        "bucket_reason_counts": bucket_reason_counts,
        "ai_status_counts": ai_status_counts,
        "sample_rejections": sample_rejections,
        "performance": performance,
    }
