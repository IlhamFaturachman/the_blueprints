import os
import json
import shutil
from datetime import datetime
from market_discovery_internal.database_manager import db

def migrate():
    json_path = "logs/paper_positions_5usd.json"
    backup_dir = "logs/backup"
    os.makedirs(backup_dir, exist_ok=True)

    if not os.path.exists(json_path):
        print(f"[MIGRATE] {json_path} not found. Skipping position migration.")
    else:
        print(f"[MIGRATE] Found {json_path}. Importing to SQLite...")
        with open(json_path, "r") as f:
            data = json.load(f)
        
        meta = data.get("meta", {})
        base_wallet = meta.get("base_wallet", 5.0)
        cash = meta.get("cash", 5.0)
        total_pnl = meta.get("acceptance_metrics_rolling", {}).get("closed_realized_pnl_total_usd", 0.0)
        
        # 1. Update Portfolio
        db.update_portfolio(base_wallet, cash, total_pnl)
        print(f"[MIGRATE] Portfolio initialized: Balance ${cash}, Base ${base_wallet}")

        # 2. Migrate Active Positions
        positions = data.get("positions", [])
        print(f"[MIGRATE] Importing {len(positions)} active positions...")
        for pos in positions:
            db.add_position(pos)

        # 3. Migrate Metrics
        journal = data.get("cycle_journal", [])
        print(f"[MIGRATE] Importing {len(journal)} cycle journal entries...")
        for entry in journal:
            db.add_cycle_metric(entry)

        # 3. Migrate History
        history = data.get("history", [])
        print(f"[MIGRATE] Importing {len(history)} historical trades...")
        for trade in history:
            conn = db._get_conn()
            conn.execute("""
                INSERT INTO trade_history (token_id, city, date, pnl_usd, roi_pct, close_reason, closed_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade.get("token_id"), trade.get("city"), trade.get("date"),
                trade.get("realized_pnl_usd"), trade.get("realized_roi_pct"),
                trade.get("close_reason"), trade.get("closed_at"), json.dumps(trade)
            ))
            conn.commit()

        # 4. Backup old file
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"paper_positions_5usd.{ts}.json.bak")
        shutil.move(json_path, backup_path)
        print(f"[MIGRATE] Original file moved to {backup_path}")

    # --- Weather Migration ---
    hist_json = "logs/cache/historical_avg.json"
    if os.path.exists(hist_json):
        print(f"[MIGRATE] Found weather cache. Importing...")
        with open(hist_json, "r") as f:
            w_data = json.load(f)
        for key, val in w_data.items():
            # key is city_MM-DD
            try:
                city, month_day = key.rsplit("_", 1)
                db.save_weather(city, month_day, max_temp=val.get("avg"))
            except:
                continue
        shutil.move(hist_json, os.path.join(backup_dir, f"historical_avg.{ts}.json.bak"))
        print("[MIGRATE] Weather cache migrated.")

if __name__ == "__main__":
    migrate()
    print("[MIGRATE] Migration complete. Database is now the Single Source of Truth.")
