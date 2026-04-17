import sqlite3
import os
import json
from datetime import datetime, timezone

DB_PATH = "logs/blueprints_master.db"
JSON_PATH = "logs/paper_positions_5usd.json"

def reset_all():
    print(f"--- [RESET] Starting Clean Slate for THE BLUEPRINTS ---")
    
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        # 1. Clear Data Tables
        print("[CLEAN] Clearing active_positions...")
        conn.execute("DELETE FROM active_positions")
        
        print("[CLEAN] Clearing trade_history...")
        conn.execute("DELETE FROM trade_history")
        
        print("[CLEAN] Clearing cycle_metrics...")
        conn.execute("DELETE FROM cycle_metrics")
        
        # 2. Reset Portfolio Summary
        print("[RESET] Setting Wallet to $5.00...")
        now_str = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE portfolio_summary 
            SET base_wallet = 5.0, 
                cash = 5.0, 
                total_pnl = 0.0, 
                updated_at = ? 
            WHERE id = 1
        """, (now_str,))
        
        conn.commit()
        print("[SUCCESS] SQLite Warehouse has been reset.")
        
    except Exception as e:
        print(f"[FAILED] Reset error: {e}")
        conn.rollback()
    finally:
        conn.close()

    # 3. Reset Mirror JSON for UI
    empty_state = {
        "positions": [],
        "history": [],
        "cycle_journal": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "base_wallet": 5.0,
            "cash": 5.0,
            "current_wallet": 5.0,
            "acceptance_metrics_rolling": {
                "closed_realized_pnl_total_usd": 0.0
            }
        }
    }
    
    try:
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(empty_state, f, indent=2)
        print(f"[SUCCESS] UI Mirror {JSON_PATH} reset.")
    except Exception as e:
        print(f"[WARN] Could not reset JSON mirror: {e}")

    print("--- [RESET] THE BLUEPRINTS is now fresh. Clean Slate achieved. ---")

if __name__ == "__main__":
    reset_all()
