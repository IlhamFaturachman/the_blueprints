"""
reset_warehouse.py — Clean Slate Reset for THE BLUEPRINTS

Resets the SQLite Data Warehouse and JSON mirror to a fresh state.
Clears ALL data tables (positions, history, metrics, calibration, AI usage, cache).
Sets portfolio to the target wallet amount.

Usage:
    python scripts/reset_warehouse.py                  # Reset to $5.00 (default)
    python scripts/reset_warehouse.py --wallet 10.0    # Reset to $10.00
    python scripts/reset_warehouse.py --force          # Skip bot-running safety check
"""

import argparse
import os
import json
import subprocess
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = "logs/blueprints_master.db"
JSON_PATH = "logs/paper_positions_5usd.json"


def _is_bot_running():
    """Check if the blueprints bot service is active (Linux systemd only)."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "blueprints.service"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # Not on Linux or systemctl not available — skip check
        return False


def reset_all(wallet: float = 5.0, force: bool = False):
    print(f"--- [RESET] Starting Clean Slate for THE BLUEPRINTS (target: ${wallet:.2f}) ---")

    # Safety check: refuse to reset while bot is running
    if not force and _is_bot_running():
        print("[ERROR] Bot service is ACTIVE. Stop it first:")
        print("        systemctl stop blueprints")
        print("  Or use --force to override (dangerous).")
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # 1. Clear ALL data tables
        tables_to_clear = [
            "active_positions",
            "trade_history",
            "cycle_metrics",
            "calibration_stats",
            "ai_daily_calls",
            "ai_monthly_cost",
            "discovery_cache",
        ]
        for table in tables_to_clear:
            try:
                conn.execute(f"DELETE FROM {table}")
                print(f"  [CLEAN] Cleared {table}")
            except sqlite3.OperationalError:
                print(f"  [SKIP]  Table {table} does not exist (OK)")

        # 2. Reset Portfolio Summary
        now_str = datetime.now(timezone.utc).isoformat()
        # Use INSERT OR REPLACE to handle both fresh DB and existing row
        conn.execute("""
            INSERT OR REPLACE INTO portfolio_summary (id, base_wallet, cash, total_pnl, updated_at)
            VALUES (1, ?, ?, 0.0, ?)
        """, (wallet, wallet, now_str))

        conn.commit()
        print(f"  [RESET] Portfolio set to ${wallet:.2f}")

        # 3. Verify
        row = conn.execute("SELECT * FROM portfolio_summary WHERE id = 1").fetchone()
        if row:
            print(f"\n  [VERIFY] DB State:")
            print(f"    base_wallet : ${float(row['base_wallet']):.2f}")
            print(f"    cash        : ${float(row['cash']):.2f}")
            print(f"    total_pnl   : ${float(row['total_pnl']):.4f}")
            print(f"    updated_at  : {row['updated_at']}")
        else:
            print("  [WARN] Portfolio row not found after reset!")

        pos_count = conn.execute("SELECT COUNT(*) FROM active_positions").fetchone()[0]
        hist_count = conn.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]
        metric_count = conn.execute("SELECT COUNT(*) FROM cycle_metrics").fetchone()[0]
        print(f"    positions   : {pos_count}")
        print(f"    history     : {hist_count}")
        print(f"    metrics     : {metric_count}")

    except Exception as e:
        print(f"[FAILED] Reset error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()

    # 4. Reset Mirror JSON for UI
    empty_state = {
        "positions": [],
        "history": [],
        "cycle_journal": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "base_wallet": wallet,
            "cash": wallet,
            "current_wallet": wallet,
            "daily_session": {},
            "auto_tuner": {},
            "acceptance_metrics_rolling": {
                "closed_realized_pnl_total_usd": 0.0
            }
        }
    }

    try:
        os.makedirs(os.path.dirname(JSON_PATH), exist_ok=True)
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(empty_state, f, indent=2)
        print(f"  [RESET] JSON mirror {JSON_PATH} written")
    except Exception as e:
        print(f"  [WARN] Could not reset JSON mirror: {e}")

    print(f"\n--- [RESET] Clean Slate achieved. Wallet: ${wallet:.2f} ---")
    print("  Next: systemctl start blueprints")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset THE BLUEPRINTS to clean slate")
    parser.add_argument("--wallet", type=float, default=5.0, help="Target wallet amount (default: $5.00)")
    parser.add_argument("--force", action="store_true", help="Skip bot-running safety check")
    args = parser.parse_args()

    if args.wallet <= 0:
        print("[ERROR] Wallet amount must be positive")
        sys.exit(1)

    reset_all(wallet=args.wallet, force=args.force)
