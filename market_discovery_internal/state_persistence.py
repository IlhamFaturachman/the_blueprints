import json
import os
from market_discovery_internal.database_manager import db

_EMPTY_STATE = {
    "positions": [],
    "history": [],
    "cycle_journal": [],
    "updated_at": None,
    "meta": {},
}


def load_paper_state(path=None):
    """Load paper-trading state from the SQLite Data Warehouse."""
    # [MODUL DB] Migration to Single Source of Truth (SQLite)
    portfolio = db.get_portfolio()
    if not portfolio:
        return dict(_EMPTY_STATE)

    positions = db.get_active_positions()
    
    # Load history (Last 100 trades for UI performance)
    conn = db._get_conn()
    history_rows = conn.execute("SELECT raw_json FROM trade_history ORDER BY closed_at DESC LIMIT 100").fetchall()
    history = [json.loads(r['raw_json']) for r in history_rows]

    # Load cycle journal (Last 100 for UI)
    metrics = db.get_latest_metrics(limit=100)

    state = {
        "positions": positions,
        "history": history,
        "cycle_journal": metrics,
        "updated_at": portfolio.get('updated_at'),
        "meta": {
            "base_wallet": portfolio.get('base_wallet'),
            "cash": portfolio.get('cash'),
            "current_wallet": portfolio.get('cash') + sum(p.get('cost_basis', 0) for p in positions),
            "acceptance_metrics_rolling": {
                "closed_realized_pnl_total_usd": portfolio.get('total_pnl', 0.0)
            }
        },
    }
    
    # Sync additional meta keys if they exist in the latest metric
    if metrics:
        state["meta"].update(metrics[0].get("meta", {}))

    return state


def save_paper_state(state, path=None):
    """Persist paper-trading state to the SQLite Data Warehouse."""
    if not state or not isinstance(state, dict):
        return

    meta = state.get("meta", {})
    base_wallet = meta.get("base_wallet", 5.0)
    cash = meta.get("cash", 5.0)
    total_pnl = meta.get("acceptance_metrics_rolling", {}).get("closed_realized_pnl_total_usd", 0.0)

    # 1. Update Portfolio Summary
    db.update_portfolio(base_wallet, cash, total_pnl)

    # 2. Update Active Positions (Atomic Replace)
    conn = db._get_conn()
    conn.execute("DELETE FROM active_positions") # Clear to avoid ghosts
    for pos in state.get("positions", []):
        db.add_position(pos)

    # 3. Add Cycle Metric (If new journal entry exists)
    journal = state.get("cycle_journal", [])
    if journal:
        db.add_cycle_metric(journal[-1])

    # 4. Persist closed trade history (new entries only — skip existing token_ids)
    for trade in state.get("history", []):
        token_id = trade.get("token_id")
        if not token_id:
            continue
        exists = conn.execute(
            "SELECT 1 FROM trade_history WHERE token_id = ?", (token_id,)
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO trade_history
                   (token_id, city, date, pnl_usd, roi_pct, close_reason, closed_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    token_id,
                    trade.get("city"),
                    trade.get("date"),
                    trade.get("realized_pnl_usd"),
                    trade.get("realized_roi_pct"),
                    trade.get("close_reason"),
                    trade.get("closed_at"),
                    json.dumps(trade),
                ),
            )
    conn.commit()

    # 5. Mirror to JSON (Mirrored Copy for Dashboard/Manual Debug)
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            # Ensure it's world-readable for the nginx-served dashboard
            os.chmod(path, 0o644)
        except:
            pass
