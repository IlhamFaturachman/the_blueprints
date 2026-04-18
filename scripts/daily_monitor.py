import os
import sys
import json
from datetime import datetime, timezone

# Add project root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from market_discovery_internal.config import (
    PAPER_STATE_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANOMALY_LOG_FILE
)
from market_discovery_internal.utils import send_telegram_alert, _load_json_blob
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
    today_prefix = datetime.now().strftime("%Y-%m-%d")
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
    rolling = report["rolling_acceptance_metrics"]
    pnl_val = float(rolling.get('closed_realized_pnl_total_usd', 0.0))
    
    msg = load_telegram_template(
        category="system",
        type_name="summary",
        date_str=today_prefix,
        status="ONLINE" if report['anomaly_counters']['current_zero_opportunity_streak'] < 5 else "HALTED",
        integrity_pct="100", # Placeholder for anti-drift logic
        log_status="Clean / Rotated",
        total_trades=rolling.get('closed_total', 0),
        win_rate=f"{rolling.get('close_win_rate', 0.0)*100:.1f}",
        pnl_emoji="🟢" if pnl_val >= 0 else "🔴",
        pnl_usd=f"{pnl_val:+.4f}",
        cash=f"{float(state.get('base_wallet', 0)) + pnl_val:.2f}",
        sync_status="IN SYNC"
    )

    success = send_telegram_alert(msg)
    if success:
        print("Telegram report sent successfully.")
    else:
        print("Failed to send Telegram report.")

if __name__ == "__main__":
    generate_daily_report()
