"""Output/printing helpers for market_discovery."""

import json
import sys


def print_discovery_diagnostics(
    discovery,
    aggressive_scan,
    build_discovery_diagnostics_fn,
    format_discovery_timing_line_fn,
    format_discovery_cache_line_fn,
    format_discovery_prefetch_line_fn,
    format_discovery_daily_skip_line_fn,
):
    """Print a concise per-stage drop-off report for discovery debugging."""
    diag = build_discovery_diagnostics_fn(discovery)

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
    print(f"  Daily mode          : {'on' if diag.get('daily_mode_enabled') else 'off'}")
    print(f"  Parsed               : {diag['parsed']}")
    print(f"  Enriched             : {diag['enriched']}")
    print(f"  Evidence valid       : {diag['evidence_valid']}")
    print(f"  Evidence invalid     : {diag['evidence_invalid']}")
    ai_status_counts = diag.get("ai_status_counts", {})
    print(
        "  AI status           : "
        f"off={ai_status_counts.get('off', 0)}, "
        f"applied={ai_status_counts.get('applied', 0)}, "
        f"missing={ai_status_counts.get('missing_config', 0)}, "
        f"fallback_error={ai_status_counts.get('fallback_error', 0)}"
    )
    print(f"  Opportunities        : {diag['opportunities']}")
    perf = diag.get("performance") if isinstance(diag.get("performance"), dict) else {}
    if perf:
        print(format_discovery_timing_line_fn(perf))
        forecast_cache = perf.get("forecast_cache") if isinstance(perf.get("forecast_cache"), dict) else {}
        if forecast_cache:
            print(format_discovery_cache_line_fn(forecast_cache))
        forecast_prefetch = perf.get("forecast_prefetch") if isinstance(perf.get("forecast_prefetch"), dict) else {}
        if forecast_prefetch:
            print(format_discovery_prefetch_line_fn(forecast_prefetch))
    daily_skipped_total = int(diag.get("daily_skipped_total", 0))
    if daily_skipped_total or diag.get("daily_mode_enabled"):
        print(format_discovery_daily_skip_line_fn(diag))
    bucket_counts = diag.get("bucket_counts", {})
    print(
        "  Entry buckets        : "
        f"swing={bucket_counts.get('enter_swing', 0)}, "
        f"hold={bucket_counts.get('enter_hold_candidate', 0)}, "
        f"watch={bucket_counts.get('watchlist', 0)}, "
        f"reject={bucket_counts.get('reject', 0)}"
    )
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

    bucket_reasons = diag.get("bucket_reason_counts", {})
    if bucket_reasons:
        print("  Bucket reasons       :")
        for reason, count in sorted(bucket_reasons.items(), key=lambda item: item[1], reverse=True)[:4]:
            print(f"    - {reason}: {count}")

    print(f"{'=' * 49}")


def _print_paper_cycle_performance_block(performance, safe_float_fn):
    """Print timing/cache/prefetch block for paper cycle summary."""
    print(
        "  Timing (ms)        : "
        f"load={safe_float_fn(performance.get('state_load_ms'), 0.0):.2f}, "
        f"discover={safe_float_fn(performance.get('discovery_ms'), 0.0):.2f}, "
        f"prefetch={safe_float_fn(performance.get('position_prefetch_ms'), 0.0):.2f}, "
        f"position={safe_float_fn(performance.get('position_management_ms'), 0.0):.2f}, "
        f"entry={safe_float_fn(performance.get('entry_selection_ms'), 0.0):.2f}, "
        f"persist={safe_float_fn(performance.get('metrics_persist_ms'), 0.0):.2f}, "
        f"total={safe_float_fn(performance.get('total_ms'), 0.0):.2f}"
    )

    position_cache = performance.get("position_forecast_cache") if isinstance(performance.get("position_forecast_cache"), dict) else {}
    if position_cache:
        print(
            "  Position cache     : "
            f"size={int(safe_float_fn(position_cache.get('size'), 0.0))}, "
            f"hits={int(safe_float_fn(position_cache.get('hits'), 0.0))}, "
            f"misses={int(safe_float_fn(position_cache.get('misses'), 0.0))}"
        )

    position_prefetch = (
        performance.get("position_forecast_prefetch")
        if isinstance(performance.get("position_forecast_prefetch"), dict)
        else {}
    )
    if position_prefetch:
        print(
            "  Position prefetch  : "
            f"eligible={int(safe_float_fn(position_prefetch.get('eligible'), 0.0))}, "
            f"attempted={int(safe_float_fn(position_prefetch.get('attempted'), 0.0))}, "
            f"success={int(safe_float_fn(position_prefetch.get('successful'), 0.0))}, "
            f"failed={int(safe_float_fn(position_prefetch.get('failed'), 0.0))}, "
            f"workers={int(safe_float_fn(position_prefetch.get('workers'), 0.0))}, "
            f"skipped={'yes' if position_prefetch.get('skipped') else 'no'}"
        )


def _print_paper_cycle_acceptance_block(acceptance, safe_float_fn):
    """Print acceptance metrics lines for paper cycle summary."""
    print(
        "  Acceptance rates   : "
        f"opp->open={safe_float_fn(acceptance.get('opportunity_to_open_rate'), 0.0):.2%}, "
        f"cand->open={safe_float_fn(acceptance.get('candidate_to_open_rate'), 0.0):.2%}, "
        f"close-win={safe_float_fn(acceptance.get('close_win_rate'), 0.0):.2%}"
    )
    print(
        "  Closed cycle PnL   : "
        f"{safe_float_fn(acceptance.get('closed_realized_pnl_usd'), 0.0):+.4f}"
    )


def _print_paper_cycle_city_coverage_block(city_coverage, safe_float_fn, paper_min_city_diversity):
    """Print city diversification lines for paper cycle summary."""
    print(
        "  City coverage      : "
        f"opp-cities={int(safe_float_fn(city_coverage.get('unique_opportunity_cities'), 0.0))}, "
        f"cand-cities={int(safe_float_fn(city_coverage.get('unique_candidate_cities'), 0.0))}, "
        f"opened-cities={int(safe_float_fn(city_coverage.get('unique_opened_cities'), 0.0))}, "
        f"target={int(safe_float_fn(city_coverage.get('min_city_target'), paper_min_city_diversity))}"
    )
    if bool(city_coverage.get("warning")):
        print(
            "  City warning       : "
            f"below target by {int(safe_float_fn(city_coverage.get('shortfall'), 0.0))} city(ies)"
        )


def _print_paper_cycle_rolling_block(rolling, rolling_city, safe_float_fn):
    """Print rolling metrics lines for paper cycle summary."""
    if rolling:
        print(
            "  Rolling rates      : "
            f"opp->open={safe_float_fn(rolling.get('opportunity_to_open_rate'), 0.0):.2%}, "
            f"cand->open={safe_float_fn(rolling.get('candidate_to_open_rate'), 0.0):.2%}, "
            f"close-win={safe_float_fn(rolling.get('close_win_rate'), 0.0):.2%}"
        )
        print(
            "  Rolling closed PnL : "
            f"{safe_float_fn(rolling.get('closed_realized_pnl_total_usd'), 0.0):+.4f}"
        )

    if rolling_city:
        print(
            "  Rolling city avg   : "
            f"opp={safe_float_fn(rolling_city.get('avg_opportunity_cities'), 0.0):.2f}, "
            f"cand={safe_float_fn(rolling_city.get('avg_candidate_cities'), 0.0):.2f}, "
            f"opened={safe_float_fn(rolling_city.get('avg_opened_cities'), 0.0):.2f}, "
            f"below-target-cycles={int(safe_float_fn(rolling_city.get('cycles_below_target'), 0.0))}"
        )


def _position_market_label(position):
    """Return a human-readable market label for opened/closed token prints."""
    question = str(position.get("market_question") or "").strip()
    if question:
        return question

    city = str(position.get("city") or "").strip()
    if city:
        return city.title()

    token_id = str(position.get("token_id") or "").strip()
    return token_id or "unknown-market"


def _safe_position_float(position, key):
    """Best-effort numeric extraction for cycle summary prints."""
    try:
        return float(position.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _print_paper_cycle_token_changes(cycle):
    """Print opened/closed token lines for paper cycle summary."""
    if cycle["opened"]:
        print("  Opened tokens      :")
        for position in cycle["opened"]:
            token_id = str(position.get("token_id") or "unknown")
            entry_price = _safe_position_float(position, "entry_price")
            print(
                f"    - {_position_market_label(position)} "
                f"[{token_id}] @ {entry_price:.4f}"
            )

    if cycle["closed"]:
        print("  Closed tokens      :")
        for position in cycle["closed"]:
            token_id = str(position.get("token_id") or "unknown")
            exit_price = _safe_position_float(position, "exit_price")
            realized_pnl = _safe_position_float(position, "realized_pnl_usd")
            close_reason = str(position.get("close_reason") or "unknown")
            print(
                f"    - {_position_market_label(position)} "
                f"[{token_id}] @ {exit_price:.4f} "
                f"({close_reason}, PnL {realized_pnl:+.4f})"
            )


def print_paper_cycle_summary(cycle, safe_float_fn, paper_min_city_diversity):
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
    bucket_counts = cycle.get("bucket_counts") or {}
    if bucket_counts:
        print(
            "  Buckets            : "
            f"swing={bucket_counts.get('enter_swing', 0)}, "
            f"hold={bucket_counts.get('enter_hold_candidate', 0)}, "
            f"watch={bucket_counts.get('watchlist', 0)}, "
            f"reject={bucket_counts.get('reject', 0)}"
        )
    print(f"  Aggressive scan    : {'on' if cycle.get('used_aggressive_scan') else 'off'}")
    print(f"  Empty temp cycles  : {cycle.get('empty_temperature_cycles', 0)}")
    # [PACK B] Regime distribution
    opps = discovery.get("opportunities") or []
    if opps:
        regime_counts = {}
        for m in opps:
            rc = m.get("regime_class", "neutral")
            regime_counts[rc] = regime_counts.get(rc, 0) + 1
        _regime_str = ", ".join(f"{k}={v}" for k, v in sorted(regime_counts.items()))
        print(f"  Regime classes     : {_regime_str}")
    # [PACK E] Auto-tuner summary
    from market_discovery_internal.state_persistence import load_paper_state
    from market_discovery_internal.config import PAPER_STATE_FILE
    try:
        _st = load_paper_state(PAPER_STATE_FILE)
        _at = (_st.get("meta") or {}).get("auto_tuner") or {}
        if _at and _at.get("blacklisted"):
            print(f"  Tuner blacklist    : {', '.join(_at['blacklisted'])}")
        elif _at.get("adjustments"):
            _n_adj = sum(1 for v in _at["adjustments"].values() if v.get("adj_edge", 0) != 0)
            print(f"  Tuner adjustments  : {_n_adj} cities adjusted")
    except Exception:
        pass
    performance = cycle.get("performance") if isinstance(cycle.get("performance"), dict) else {}
    if performance:
        _print_paper_cycle_performance_block(performance, safe_float_fn)

    acceptance = cycle.get("acceptance_metrics") if isinstance(cycle.get("acceptance_metrics"), dict) else {}
    if acceptance:
        _print_paper_cycle_acceptance_block(acceptance, safe_float_fn)

    city_coverage = cycle.get("city_coverage_metrics") if isinstance(cycle.get("city_coverage_metrics"), dict) else {}
    if city_coverage:
        _print_paper_cycle_city_coverage_block(city_coverage, safe_float_fn, paper_min_city_diversity)

    rolling = cycle.get("rolling_acceptance_metrics") if isinstance(cycle.get("rolling_acceptance_metrics"), dict) else {}
    rolling_city = (
        cycle.get("rolling_city_coverage_metrics")
        if isinstance(cycle.get("rolling_city_coverage_metrics"), dict)
        else {}
    )
    _print_paper_cycle_rolling_block(rolling, rolling_city, safe_float_fn)

    print(f"  State file         : {cycle['state_path']}")
    print(f"{'=' * 43}")

    _print_paper_cycle_token_changes(cycle)
    sys.stdout.flush()


def _print_report_journal_retention_block(journal_retention, safe_float_fn):
    """Print journal retention details for text report mode."""
    print(
        "  Journal retention  : "
        f"recent={int(safe_float_fn(journal_retention.get('recent_entries_shown'), 0.0))}, "
        f"older-hidden={int(safe_float_fn(journal_retention.get('older_entries_not_shown'), 0.0))}, "
        f"capacity={int(safe_float_fn(journal_retention.get('journal_capacity_limit'), 0.0))}, "
        f"util={safe_float_fn(journal_retention.get('journal_capacity_utilization_rate'), 0.0):.2%}"
    )

    age_buckets = journal_retention.get("age_buckets") if isinstance(journal_retention.get("age_buckets"), dict) else {}
    if age_buckets:
        print(
            "  Journal age buckets: "
            f"<24h={int(safe_float_fn(age_buckets.get('lt_24h'), 0.0))}, "
            f"24-72h={int(safe_float_fn(age_buckets.get('h24_to_72h'), 0.0))}, "
            f">72h={int(safe_float_fn(age_buckets.get('gt_72h'), 0.0))}, "
            f"unknown={int(safe_float_fn(age_buckets.get('unknown'), 0.0))}"
        )

    rotation_summary = (
        journal_retention.get("rotation_summary")
        if isinstance(journal_retention.get("rotation_summary"), dict)
        else {}
    )
    if rotation_summary:
        print(
            "  Journal rotation   : "
            f"policy={rotation_summary.get('policy', 'unknown')}, "
            f"capacity-reached={'yes' if rotation_summary.get('capacity_reached') else 'no'}, "
            f"rotated-est={int(safe_float_fn(rotation_summary.get('estimated_entries_rotated_out'), 0.0))}, "
            f"coverage={safe_float_fn(rotation_summary.get('coverage_hours'), 0.0):.2f}h"
        )

    if bool(journal_retention.get("retention_warning")):
        print(
            "  Retention warning  : "
            f"journal entries >= warn threshold ({int(safe_float_fn(journal_retention.get('retention_warn_threshold'), 0.0))})"
        )


def _print_report_anomaly_block(anomaly_counters, safe_float_fn):
    """Print anomaly streak summary and alert flags for text report mode."""
    alerts = anomaly_counters.get("alerts") if isinstance(anomaly_counters.get("alerts"), dict) else {}
    print(
        "  Anomaly streaks    : "
        f"zero-current={int(safe_float_fn(anomaly_counters.get('current_zero_opportunity_streak'), 0.0))}, "
        f"zero-max={int(safe_float_fn(anomaly_counters.get('max_zero_opportunity_streak'), 0.0))}, "
        f"reject-current={int(safe_float_fn(anomaly_counters.get('current_reject_dominant_streak'), 0.0))}, "
        f"reject-max={int(safe_float_fn(anomaly_counters.get('max_reject_dominant_streak'), 0.0))}"
    )
    print(
        "  Anomaly thresholds : "
        f"streak={int(safe_float_fn(anomaly_counters.get('streak_alert_threshold'), 0.0))}, "
        f"reject-ratio>={safe_float_fn(anomaly_counters.get('reject_dominant_ratio_threshold'), 0.0):.2f}, "
        f"latest-reject-ratio={safe_float_fn(anomaly_counters.get('latest_reject_ratio'), 0.0):.2f}"
    )
    if alerts.get("zero_opportunity_streak") or alerts.get("reject_dominant_streak"):
        print(
            "  Anomaly alerts     : "
            f"zero-streak={'on' if alerts.get('zero_opportunity_streak') else 'off'}, "
            f"reject-streak={'on' if alerts.get('reject_dominant_streak') else 'off'}"
        )


def _print_report_rolling_block(rolling, safe_float_fn):
    """Print rolling acceptance metrics lines for text report mode."""
    print(
        "  Rolling rates      : "
        f"opp->open={safe_float_fn(rolling.get('opportunity_to_open_rate'), 0.0):.2%}, "
        f"cand->open={safe_float_fn(rolling.get('candidate_to_open_rate'), 0.0):.2%}, "
        f"close-win={safe_float_fn(rolling.get('close_win_rate'), 0.0):.2%}"
    )
    print(
        "  Rolling totals     : "
        f"cycles={int(safe_float_fn(rolling.get('cycles_total'), 0.0))}, "
        f"opp={int(safe_float_fn(rolling.get('opportunities_total'), 0.0))}, "
        f"opens={int(safe_float_fn(rolling.get('opened_total'), 0.0))}, "
        f"closed={int(safe_float_fn(rolling.get('closed_total'), 0.0))}"
    )
    print(
        "  Rolling closed PnL : "
        f"{safe_float_fn(rolling.get('closed_realized_pnl_total_usd'), 0.0):+.4f}"
    )


def _print_report_rolling_city_block(rolling_city, safe_float_fn, paper_min_city_diversity):
    """Print rolling city coverage line for text report mode."""
    print(
        "  Rolling city cov   : "
        f"target={int(safe_float_fn(rolling_city.get('min_city_target'), paper_min_city_diversity))}, "
        f"avg-opp={safe_float_fn(rolling_city.get('avg_opportunity_cities'), 0.0):.2f}, "
        f"avg-opened={safe_float_fn(rolling_city.get('avg_opened_cities'), 0.0):.2f}, "
        f"below-target-cycles={int(safe_float_fn(rolling_city.get('cycles_below_target'), 0.0))}"
    )


def _print_report_last_cycle_performance_block(last_cycle_performance, safe_float_fn):
    """Print last cycle timing and prefetch lines for text report mode."""
    print(
        "  Last cycle timing  : "
        f"total={safe_float_fn(last_cycle_performance.get('total_ms'), 0.0):.2f}ms, "
        f"discover={safe_float_fn(last_cycle_performance.get('discovery_ms'), 0.0):.2f}ms, "
        f"prefetch={safe_float_fn(last_cycle_performance.get('position_prefetch_ms'), 0.0):.2f}ms, "
        f"position={safe_float_fn(last_cycle_performance.get('position_management_ms'), 0.0):.2f}ms, "
        f"entry={safe_float_fn(last_cycle_performance.get('entry_selection_ms'), 0.0):.2f}ms"
    )
    last_prefetch = (
        last_cycle_performance.get("position_forecast_prefetch")
        if isinstance(last_cycle_performance.get("position_forecast_prefetch"), dict)
        else {}
    )
    if last_prefetch:
        print(
            "  Last prefetch      : "
            f"eligible={int(safe_float_fn(last_prefetch.get('eligible'), 0.0))}, "
            f"attempted={int(safe_float_fn(last_prefetch.get('attempted'), 0.0))}, "
            f"success={int(safe_float_fn(last_prefetch.get('successful'), 0.0))}, "
            f"failed={int(safe_float_fn(last_prefetch.get('failed'), 0.0))}, "
            f"workers={int(safe_float_fn(last_prefetch.get('workers'), 0.0))}, "
            f"skipped={'yes' if last_prefetch.get('skipped') else 'no'}"
        )


def _print_report_recent_journal_block(recent_journal, safe_float_fn):
    """Print recent journal summary lines for text report mode."""
    print("  Recent journal     :")
    for entry in recent_journal:
        counts = entry.get("counts") if isinstance(entry.get("counts"), dict) else {}
        rates = entry.get("acceptance_rates") if isinstance(entry.get("acceptance_rates"), dict) else {}
        print(
            f"    - {entry.get('timestamp', 'unknown')}: "
            f"opp={int(safe_float_fn(counts.get('opportunities'), 0.0))}, "
            f"open={int(safe_float_fn(counts.get('opened'), 0.0))}, "
            f"closed={int(safe_float_fn(counts.get('closed'), 0.0))}, "
            f"opp->open={safe_float_fn(rates.get('opportunity_to_open_rate'), 0.0):.2%}"
        )


def print_paper_state_report(
    state,
    state_path,
    recent_entries,
    output_format,
    now_utc,
    build_paper_state_report_fn,
    safe_float_fn,
    paper_min_city_diversity,
):
    """Print persisted paper state summary without running a new cycle."""
    report = build_paper_state_report_fn(
        state=state,
        state_path=state_path,
        recent_entries=recent_entries,
        now_utc=now_utc,
    )

    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
        return report

    rolling = report.get("rolling_acceptance_metrics") if isinstance(report.get("rolling_acceptance_metrics"), dict) else {}
    rolling_city = (
        report.get("rolling_city_coverage_metrics")
        if isinstance(report.get("rolling_city_coverage_metrics"), dict)
        else {}
    )
    last_cycle_performance = (
        report.get("last_cycle_performance")
        if isinstance(report.get("last_cycle_performance"), dict)
        else {}
    )

    print(f"\n{'=' * 43}")
    print("  PAPER STATE REPORT")
    print(f"{'=' * 43}")
    print(f"  State file         : {report['state_path']}")
    print(f"  Open positions     : {report['open_positions_count']}")
    print(f"  Closed history     : {report['closed_history_count']}")
    print(f"  Journal entries    : {report['journal_entries_count']}")
    anomaly_counters = report.get("anomaly_counters") if isinstance(report.get("anomaly_counters"), dict) else {}
    if anomaly_counters:
        _print_report_anomaly_block(anomaly_counters, safe_float_fn)
    journal_retention = report.get("journal_retention") if isinstance(report.get("journal_retention"), dict) else {}
    if journal_retention:
        _print_report_journal_retention_block(journal_retention, safe_float_fn)

    if rolling:
        _print_report_rolling_block(rolling, safe_float_fn)

    if rolling_city:
        _print_report_rolling_city_block(rolling_city, safe_float_fn, paper_min_city_diversity)

    if last_cycle_performance:
        _print_report_last_cycle_performance_block(last_cycle_performance, safe_float_fn)

    recent_journal = report.get("recent_journal") if isinstance(report.get("recent_journal"), list) else []
    if recent_journal:
        _print_report_recent_journal_block(recent_journal, safe_float_fn)

    print(f"{'=' * 43}")
    return report


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
