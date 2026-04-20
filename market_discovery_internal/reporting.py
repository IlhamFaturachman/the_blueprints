"""Paper-state report payload helpers for market_discovery."""

from datetime import datetime, timezone
from market_discovery_internal.utils import _safe_float, _safe_div
from market_discovery_internal.config import PAPER_MIN_CITY_DIVERSITY


def parse_utc_datetime(value):
    """Parse datetime inputs into timezone-aware UTC datetime objects."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    return None

def _closed_reason_counts(closed_positions):
    """Aggregate counts of exit reasons for a set of closed positions."""
    counts = {}
    for pos in closed_positions:
        reason = str(pos.get("close_reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts

def build_cycle_acceptance_metrics(discovery, bucket_counts, opened_positions, closed_positions):
    """Compute per-cycle pipeline performance metrics (discovery -> entry -> exit)."""
    opportunities_total = len(discovery.get("opportunities", []))
    entry_candidates = int(bucket_counts.get("enter_swing", 0)) + int(bucket_counts.get("enter_hold_candidate", 0))
    opened = len(opened_positions)
    closed = len(closed_positions)
    closed_wins = sum(1 for pos in closed_positions if _safe_float(pos.get("realized_pnl_usd"), 0.0) > 0)
    closed_realized_pnl = round(sum(_safe_float(pos.get("realized_pnl_usd"), 0.0) for pos in closed_positions), 4)

    return {
        "opportunities_total": opportunities_total,
        "entry_candidates_total": entry_candidates,
        "opened_total": opened,
        "closed_total": closed,
        "closed_wins_total": closed_wins,
        "closed_realized_pnl_usd": closed_realized_pnl,
        "opportunity_to_open_rate": round(_safe_div(opened, opportunities_total, default=0.0), 4),
        "candidate_to_open_rate": round(_safe_div(opened, entry_candidates, default=0.0), 4),
        "close_win_rate": round(_safe_div(closed_wins, closed, default=0.0), 4),
    }

def build_rolling_acceptance_metrics(previous, cycle_metrics, bucket_counts):
    """Update cumulative acceptance metrics stored in paper state metadata."""
    previous = previous if isinstance(previous, dict) else {}

    bucket_totals_prev = previous.get("entry_bucket_totals", {})
    bucket_totals = {
        "reject": int(bucket_totals_prev.get("reject", 0)) + int(bucket_counts.get("reject", 0)),
        "watchlist": int(bucket_totals_prev.get("watchlist", 0)) + int(bucket_counts.get("watchlist", 0)),
        "enter_swing": int(bucket_totals_prev.get("enter_swing", 0)) + int(bucket_counts.get("enter_swing", 0)),
        "enter_hold_candidate": int(bucket_totals_prev.get("enter_hold_candidate", 0)) + int(bucket_counts.get("enter_hold_candidate", 0)),
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

def build_city_coverage_metrics(opportunity_city_keys, candidate_city_keys, opened_city_keys, min_city_target=PAPER_MIN_CITY_DIVERSITY):
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

def build_rolling_city_coverage_metrics(previous, city_coverage):
    """Update rolling city coverage counters for diversification monitoring."""
    previous = previous if isinstance(previous, dict) else {}
    cycles_total = int(previous.get("cycles_total", 0)) + 1
    opp_city_total = int(previous.get("opportunity_cities_total", 0)) + int(city_coverage.get("unique_opportunity_cities", 0))
    cand_city_total = int(previous.get("candidate_cities_total", 0)) + int(city_coverage.get("unique_candidate_cities", 0))
    opened_city_total = int(previous.get("opened_cities_total", 0)) + int(city_coverage.get("unique_opened_cities", 0))
    cycles_below_target = int(previous.get("cycles_below_target", 0)) + (1 if city_coverage.get("warning") else 0)

    return {
        "cycles_total": cycles_total,
        "opportunity_cities_total": opp_city_total,
        "candidate_cities_total": cand_city_total,
        "opened_cities_total": opened_city_total,
        "cycles_below_target": cycles_below_target,
        "avg_opportunity_cities": round(_safe_div(opp_city_total, cycles_total, default=0.0), 4),
        "avg_candidate_cities": round(_safe_div(cand_city_total, cycles_total, default=0.0), 4),
        "avg_opened_cities": round(_safe_div(opened_city_total, cycles_total, default=0.0), 4),
        "min_city_target": int(city_coverage.get("min_city_target", PAPER_MIN_CITY_DIVERSITY)),
    }

def build_cycle_journal_entry(now_utc, cycle_metrics, bucket_counts, cycle, city_coverage_metrics=None, performance_metrics=None, meta=None):
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
        "meta": dict(meta) if isinstance(meta, dict) else {},
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
            "total_ms": _safe_float(performance_metrics.get("total_ms")),
            "discovery_ms": _safe_float(performance_metrics.get("discovery_ms")),
            "position_management_ms": _safe_float(performance_metrics.get("position_management_ms")),
            "entry_selection_ms": _safe_float(performance_metrics.get("entry_selection_ms")),
            "metrics_persist_ms": _safe_float(performance_metrics.get("metrics_persist_ms")),
        }
    return entry


def build_journal_age_breakdown(journal_entries, now_utc=None, *, parse_utc_datetime_fn=parse_utc_datetime):
    """Return age-bucket counts and timestamp coverage information for journal entries."""
    now_dt = parse_utc_datetime_fn(now_utc) or datetime.now(timezone.utc)
    buckets = {
        "lt_24h": 0,
        "h24_to_72h": 0,
        "gt_72h": 0,
        "unknown": 0,
    }

    oldest = None
    newest = None

    for entry in journal_entries:
        timestamp = None
        if isinstance(entry, dict):
            timestamp = parse_utc_datetime_fn(entry.get("timestamp"))

        if timestamp is None:
            buckets["unknown"] += 1
            continue

        if oldest is None or timestamp < oldest:
            oldest = timestamp
        if newest is None or timestamp > newest:
            newest = timestamp

        age_hours = max((now_dt - timestamp).total_seconds() / 3600, 0.0)
        if age_hours < 24:
            buckets["lt_24h"] += 1
        elif age_hours <= 72:
            buckets["h24_to_72h"] += 1
        else:
            buckets["gt_72h"] += 1

    coverage_hours = 0.0
    if oldest and newest:
        coverage_hours = round(max((newest - oldest).total_seconds() / 3600, 0.0), 2)

    return {
        "age_buckets": buckets,
        "oldest_timestamp": oldest.isoformat() if oldest else None,
        "newest_timestamp": newest.isoformat() if newest else None,
        "coverage_hours": coverage_hours,
    }


def normalize_rolling_acceptance_metrics(rolling, *, safe_float_fn):
    """Normalize rolling acceptance metrics into report payload schema."""
    if not isinstance(rolling, dict):
        return {}

    return {
        "cycles_total": int(safe_float_fn(rolling.get("cycles_total"), 0.0)),
        "opportunities_total": int(safe_float_fn(rolling.get("opportunities_total"), 0.0)),
        "entry_candidates_total": int(safe_float_fn(rolling.get("entry_candidates_total"), 0.0)),
        "opened_total": int(safe_float_fn(rolling.get("opened_total"), 0.0)),
        "closed_total": int(safe_float_fn(rolling.get("closed_total"), 0.0)),
        "closed_wins_total": int(safe_float_fn(rolling.get("closed_wins_total"), 0.0)),
        "opportunity_to_open_rate": round(safe_float_fn(rolling.get("opportunity_to_open_rate"), 0.0), 4),
        "candidate_to_open_rate": round(safe_float_fn(rolling.get("candidate_to_open_rate"), 0.0), 4),
        "close_win_rate": round(safe_float_fn(rolling.get("close_win_rate"), 0.0), 4),
        "closed_realized_pnl_total_usd": round(safe_float_fn(rolling.get("closed_realized_pnl_total_usd"), 0.0), 4),
    }


def normalize_rolling_city_coverage_metrics(city_rolling, *, safe_float_fn, paper_min_city_diversity):
    """Normalize rolling city coverage metrics into report payload schema."""
    if not isinstance(city_rolling, dict):
        return {}

    return {
        "cycles_total": int(safe_float_fn(city_rolling.get("cycles_total"), 0.0)),
        "min_city_target": int(safe_float_fn(city_rolling.get("min_city_target"), paper_min_city_diversity)),
        "cycles_below_target": int(safe_float_fn(city_rolling.get("cycles_below_target"), 0.0)),
        "opportunity_cities_total": int(safe_float_fn(city_rolling.get("opportunity_cities_total"), 0.0)),
        "candidate_cities_total": int(safe_float_fn(city_rolling.get("candidate_cities_total"), 0.0)),
        "opened_cities_total": int(safe_float_fn(city_rolling.get("opened_cities_total"), 0.0)),
        "avg_opportunity_cities": round(safe_float_fn(city_rolling.get("avg_opportunity_cities"), 0.0), 4),
        "avg_candidate_cities": round(safe_float_fn(city_rolling.get("avg_candidate_cities"), 0.0), 4),
        "avg_opened_cities": round(safe_float_fn(city_rolling.get("avg_opened_cities"), 0.0), 4),
    }


def normalize_last_cycle_performance(last_cycle_performance, *, safe_float_fn):
    """Normalize last-cycle timing and prefetch details for report payload."""
    if not isinstance(last_cycle_performance, dict):
        return {}

    return {
        "total_ms": round(safe_float_fn(last_cycle_performance.get("total_ms"), 0.0), 3),
        "state_load_ms": round(safe_float_fn(last_cycle_performance.get("state_load_ms"), 0.0), 3),
        "discovery_ms": round(safe_float_fn(last_cycle_performance.get("discovery_ms"), 0.0), 3),
        "position_prefetch_ms": round(safe_float_fn(last_cycle_performance.get("position_prefetch_ms"), 0.0), 3),
        "position_management_ms": round(safe_float_fn(last_cycle_performance.get("position_management_ms"), 0.0), 3),
        "entry_selection_ms": round(safe_float_fn(last_cycle_performance.get("entry_selection_ms"), 0.0), 3),
        "metrics_persist_ms": round(safe_float_fn(last_cycle_performance.get("metrics_persist_ms"), 0.0), 3),
        "position_forecast_cache": (
            last_cycle_performance.get("position_forecast_cache")
            if isinstance(last_cycle_performance.get("position_forecast_cache"), dict)
            else {}
        ),
        "position_forecast_prefetch": (
            last_cycle_performance.get("position_forecast_prefetch")
            if isinstance(last_cycle_performance.get("position_forecast_prefetch"), dict)
            else {}
        ),
    }


def normalize_recent_journal_entries(journal, recent_entries, *, safe_float_fn):
    """Normalize recent journal entries and return shown raw count."""
    recent_limit = max(1, int(safe_float_fn(recent_entries, 5)))
    recent_slice = journal[-recent_limit:]
    recent_journal = []

    for entry in recent_slice:
        if not isinstance(entry, dict):
            continue

        counts = entry.get("counts") if isinstance(entry.get("counts"), dict) else {}
        rates = entry.get("acceptance_rates") if isinstance(entry.get("acceptance_rates"), dict) else {}
        recent_journal.append(
            {
                "timestamp": str(entry.get("timestamp") or "unknown"),
                "aggressive_scan": bool(entry.get("aggressive_scan", False)),
                "empty_temperature_cycles": int(safe_float_fn(entry.get("empty_temperature_cycles"), 0.0)),
                "counts": {
                    "opportunities": int(safe_float_fn(counts.get("opportunities"), 0.0)),
                    "entry_candidates": int(safe_float_fn(counts.get("entry_candidates"), 0.0)),
                    "opened": int(safe_float_fn(counts.get("opened"), 0.0)),
                    "closed": int(safe_float_fn(counts.get("closed"), 0.0)),
                    "open_positions_after": int(safe_float_fn(counts.get("open_positions_after"), 0.0)),
                },
                "bucket_counts": entry.get("bucket_counts") if isinstance(entry.get("bucket_counts"), dict) else {},
                "closed_reason_counts": (
                    entry.get("closed_reason_counts") if isinstance(entry.get("closed_reason_counts"), dict) else {}
                ),
                "acceptance_rates": {
                    "opportunity_to_open_rate": round(safe_float_fn(rates.get("opportunity_to_open_rate"), 0.0), 4),
                    "candidate_to_open_rate": round(safe_float_fn(rates.get("candidate_to_open_rate"), 0.0), 4),
                    "close_win_rate": round(safe_float_fn(rates.get("close_win_rate"), 0.0), 4),
                },
                "closed_realized_pnl_usd": round(safe_float_fn(entry.get("closed_realized_pnl_usd"), 0.0), 4),
                "city_coverage": entry.get("city_coverage") if isinstance(entry.get("city_coverage"), dict) else {},
            }
        )

    return recent_journal, len(recent_slice)


def build_journal_retention_payload(
    journal,
    shown_count,
    rolling,
    now_utc=None,
    *,
    safe_float_fn,
    safe_div_fn,
    build_journal_age_breakdown_fn,
    paper_journal_max_entries,
    paper_report_retention_warn_threshold,
):
    """Build journal retention and rotation summary payload."""
    journal_capacity_limit = max(1, int(safe_float_fn(paper_journal_max_entries, 1)))
    retention_warn_threshold = max(1, int(safe_float_fn(paper_report_retention_warn_threshold, 1)))
    age_breakdown = build_journal_age_breakdown_fn(journal_entries=journal, now_utc=now_utc)
    cycles_total = int(safe_float_fn(rolling.get("cycles_total"), 0.0)) if isinstance(rolling, dict) else 0

    return {
        "recent_entries_shown": shown_count,
        "older_entries_not_shown": max(len(journal) - shown_count, 0),
        "journal_capacity_limit": journal_capacity_limit,
        "journal_capacity_remaining": max(journal_capacity_limit - len(journal), 0),
        "journal_capacity_utilization_rate": round(safe_div_fn(len(journal), journal_capacity_limit, default=0.0), 4),
        "retention_warn_threshold": retention_warn_threshold,
        "retention_warning": len(journal) >= retention_warn_threshold,
        "age_buckets": age_breakdown["age_buckets"],
        "rotation_summary": {
            "policy": "rolling_tail_keep_latest",
            "capacity_reached": len(journal) >= journal_capacity_limit,
            "estimated_entries_rotated_out": max(cycles_total - len(journal), 0),
            "coverage_hours": age_breakdown["coverage_hours"],
            "oldest_entry_timestamp": age_breakdown["oldest_timestamp"],
            "newest_entry_timestamp": age_breakdown["newest_timestamp"],
        },
    }


def _max_true_streak(flags):
    """Return the longest contiguous True streak in a boolean sequence."""
    current = 0
    longest = 0
    for flag in flags:
        if flag:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _tail_true_streak(flags):
    """Return trailing contiguous True streak from sequence tail."""
    streak = 0
    for flag in reversed(flags):
        if flag:
            streak += 1
        else:
            break
    return streak


def _journal_entry_reject_ratio(entry, safe_float_fn):
    """Return reject ratio for one journal entry, or None when unavailable."""
    if not isinstance(entry, dict):
        return None

    bucket_counts = entry.get("bucket_counts") if isinstance(entry.get("bucket_counts"), dict) else {}
    total_bucketed = sum(max(0.0, safe_float_fn(value, 0.0)) for value in bucket_counts.values())
    if total_bucketed <= 0:
        return None

    reject_count = max(0.0, safe_float_fn(bucket_counts.get("reject"), 0.0))
    return reject_count / total_bucketed


def build_journal_anomaly_counters(
    journal,
    *,
    safe_float_fn,
    paper_report_anomaly_streak_alert,
    paper_report_reject_dominant_ratio,
):
    """Build anomaly streak counters from cycle journal entries."""
    streak_alert_threshold = max(1, int(safe_float_fn(paper_report_anomaly_streak_alert, 3)))
    reject_dominant_ratio = min(1.0, max(0.0, safe_float_fn(paper_report_reject_dominant_ratio, 0.85)))

    zero_opportunity_flags = []
    reject_dominant_flags = []
    latest_reject_ratio = None

    for entry in journal:
        counts = entry.get("counts") if isinstance(entry, dict) and isinstance(entry.get("counts"), dict) else {}
        opportunities = int(max(0.0, safe_float_fn(counts.get("opportunities"), 0.0)))
        zero_opportunity_flags.append(opportunities <= 0)

        reject_ratio = _journal_entry_reject_ratio(entry, safe_float_fn)
        if reject_ratio is not None:
            latest_reject_ratio = round(reject_ratio, 4)
        reject_dominant_flags.append(reject_ratio is not None and reject_ratio >= reject_dominant_ratio)

    current_zero_streak = _tail_true_streak(zero_opportunity_flags)
    max_zero_streak = _max_true_streak(zero_opportunity_flags)
    current_reject_streak = _tail_true_streak(reject_dominant_flags)
    max_reject_streak = _max_true_streak(reject_dominant_flags)

    return {
        "current_zero_opportunity_streak": current_zero_streak,
        "max_zero_opportunity_streak": max_zero_streak,
        "current_reject_dominant_streak": current_reject_streak,
        "max_reject_dominant_streak": max_reject_streak,
        "streak_alert_threshold": streak_alert_threshold,
        "reject_dominant_ratio_threshold": round(reject_dominant_ratio, 4),
        "latest_reject_ratio": latest_reject_ratio,
        "alerts": {
            "zero_opportunity_streak": current_zero_streak >= streak_alert_threshold,
            "reject_dominant_streak": current_reject_streak >= streak_alert_threshold,
        },
    }


def build_paper_state_report(
    state,
    state_path,
    recent_entries,
    now_utc,
    *,
    safe_float_fn,
    safe_div_fn,
    paper_min_city_diversity,
    paper_journal_max_entries,
    paper_report_retention_warn_threshold,
    normalize_rolling_acceptance_metrics_fn,
    normalize_rolling_city_coverage_metrics_fn,
    normalize_last_cycle_performance_fn,
    normalize_recent_journal_entries_fn,
    build_journal_retention_payload_fn,
    build_journal_anomaly_counters_fn=None,
    paper_report_anomaly_streak_alert=3,
    paper_report_reject_dominant_ratio=0.85,
):
    """Build a normalized paper-state report payload for text or JSON output."""
    state_data = state if isinstance(state, dict) else {}
    positions = state_data.get("positions") if isinstance(state_data.get("positions"), list) else []
    history = state_data.get("history") if isinstance(state_data.get("history"), list) else []
    journal = state_data.get("cycle_journal") if isinstance(state_data.get("cycle_journal"), list) else []
    meta = state_data.get("meta") if isinstance(state_data.get("meta"), dict) else {}
    rolling = meta.get("acceptance_metrics_rolling") if isinstance(meta.get("acceptance_metrics_rolling"), dict) else {}
    city_rolling = meta.get("city_coverage_rolling") if isinstance(meta.get("city_coverage_rolling"), dict) else {}
    last_cycle_performance = meta.get("last_cycle_performance")

    rolling_payload = normalize_rolling_acceptance_metrics_fn(
        rolling=rolling,
        safe_float_fn=safe_float_fn,
    )
    city_rolling_payload = normalize_rolling_city_coverage_metrics_fn(
        city_rolling=city_rolling,
        safe_float_fn=safe_float_fn,
        paper_min_city_diversity=paper_min_city_diversity,
    )
    last_cycle_performance_payload = normalize_last_cycle_performance_fn(
        last_cycle_performance=last_cycle_performance,
        safe_float_fn=safe_float_fn,
    )
    recent_journal, shown_count = normalize_recent_journal_entries_fn(
        journal=journal,
        recent_entries=recent_entries,
        safe_float_fn=safe_float_fn,
    )
    anomaly_builder = build_journal_anomaly_counters_fn or build_journal_anomaly_counters
    anomaly_counters = anomaly_builder(
        journal=journal,
        safe_float_fn=safe_float_fn,
        paper_report_anomaly_streak_alert=paper_report_anomaly_streak_alert,
        paper_report_reject_dominant_ratio=paper_report_reject_dominant_ratio,
    )
    journal_retention = build_journal_retention_payload_fn(
        journal=journal,
        shown_count=shown_count,
        rolling=rolling,
        now_utc=now_utc,
        safe_float_fn=safe_float_fn,
        safe_div_fn=safe_div_fn,
        build_journal_age_breakdown_fn=build_journal_age_breakdown,
        paper_journal_max_entries=paper_journal_max_entries,
        paper_report_retention_warn_threshold=paper_report_retention_warn_threshold,
    )

    return {
        "state_path": state_path,
        "updated_at": state_data.get("updated_at"),
        "open_positions_count": len(positions),
        "closed_history_count": len(history),
        "journal_entries_count": len(journal),
        "anomaly_counters": anomaly_counters,
        "journal_retention": journal_retention,
        "rolling_acceptance_metrics": rolling_payload,
        "rolling_city_coverage_metrics": city_rolling_payload,
        "last_cycle_performance": last_cycle_performance_payload,
        "recent_journal": recent_journal,
    }
