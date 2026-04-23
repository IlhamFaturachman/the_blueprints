import os
import sys
import json
from datetime import datetime, timezone

# Add project root (parent of scripts/ directory)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.config import (
    PAPER_STATE_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANOMALY_LOG_FILE
)
from market_discovery_internal.utils import send_telegram_alert, load_telegram_template, _load_json_blob
from market_discovery_internal.reporting import build_paper_state_report, build_journal_anomaly_counters
from market_discovery_internal.output import print_paper_state_report

def generate_daily_report():
    print(f"[{datetime.now()}] Generating Sempurna Sprint Daily Report...")
    
    if not os.path.exists(PAPER_STATE_FILE):
        print("Paper state file not found. Skipping report.")
        return

    state = _load_json_blob(PAPER_STATE_FILE, {})
    
    # 1. Capture Anomaly Counts
    anomalies_today = 0
    # [FIX-M-T5-7] Use UTC for consistent day boundary (bot operates on UTC)
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if os.path.exists(ANOMALY_LOG_FILE):
        try:
            with open(ANOMALY_LOG_FILE, "r") as f:
                for line in f:
                    if today_prefix in line:
                        anomalies_today += 1
        except Exception:
            pass

    # 2. Build Metrics
    # Using internal helpers to get the summary
    from market_discovery_internal.utils import _safe_float, _safe_div
    from market_discovery_internal.reporting import (
        normalize_rolling_acceptance_metrics, normalize_rolling_city_coverage_metrics,
        normalize_last_cycle_performance, normalize_recent_journal_entries,
        build_journal_retention_payload, build_journal_anomaly_counters
    )

    report = build_paper_state_report(
        state=state,
        state_path=PAPER_STATE_FILE,
        recent_entries=1,
        now_utc=datetime.now(timezone.utc),
        safe_float_fn=_safe_float,
        safe_div_fn=_safe_div,
        paper_min_city_diversity=1,
        paper_journal_max_entries=1000,
        paper_report_retention_warn_threshold=900,
        normalize_rolling_acceptance_metrics_fn=normalize_rolling_acceptance_metrics,
        normalize_rolling_city_coverage_metrics_fn=normalize_rolling_city_coverage_metrics,
        normalize_last_cycle_performance_fn=normalize_last_cycle_performance,
        normalize_recent_journal_entries_fn=normalize_recent_journal_entries,
        build_journal_retention_payload_fn=build_journal_retention_payload,
        build_journal_anomaly_counters_fn=build_journal_anomaly_counters
    )

    # 3. Format Telegram Message using Template
    # [FIX-T5-7-HIGH] Use .get() with defaults to prevent KeyError crashes
    rolling = report.get("rolling_acceptance_metrics", {})
    pnl_val = float(rolling.get('closed_realized_pnl_total_usd', 0.0))
    _wins = int(rolling.get('closed_wins', 0))
    _losses = int(rolling.get('closed_losses', 0))
    _total_fees = float(rolling.get('closed_total_fees_usd', 0.0))
    _net_pnl = pnl_val - _total_fees
    _baseline = float(state.get('base_wallet', 0))
    _meta = state.get('meta', {}) if isinstance(state.get('meta'), dict) else {}
    _cycles_run = _meta.get('total_cycles', 'N/A')
    _uptime_h = (int(_cycles_run) * 5 / 60) if str(_cycles_run).isdigit() else 0
    _uptime_str = f"{_uptime_h:.1f}h" if _uptime_h else "N/A"
    _anomaly_counters = report.get('anomaly_counters', {})
    _zero_opp_streak = _anomaly_counters.get('current_zero_opportunity_streak', 0)
    
    msg = load_telegram_template(
        category="system",
        type_name="summary",
        date_str=today_prefix,
        status="ONLINE" if _zero_opp_streak < 5 else "HALTED",
        total_trades=int(rolling.get('closed_total', 0)),
        win_rate=f"{rolling.get('close_win_rate', 0.0)*100:.1f}",
        wins=_wins,
        losses=_losses,
        pnl_emoji="🟢" if pnl_val >= 0 else "🔴",
        pnl_usd=f"{pnl_val:+.4f}",
        total_fees=f"{_total_fees:.4f}",
        net_pnl=f"{_net_pnl:+.4f}",
        baseline=f"{_baseline:.2f}",
        cash=f"{_baseline + pnl_val:.2f}",
        cycles_run=_cycles_run,
        uptime=_uptime_str
    )

    success = send_telegram_alert(msg)
    if success:
        print("Telegram report sent successfully.")
    else:
        print("Failed to send Telegram report.")

if __name__ == "__main__":
    generate_daily_report()
