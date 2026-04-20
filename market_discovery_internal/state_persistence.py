import json
import os
from market_discovery_internal.database_manager import db

def _default_meta():
    """Build a default meta dict from config, evaluated lazily at call time."""
    from market_discovery_internal.config import PAPER_BASE_WALLET
    return {
        "base_wallet": PAPER_BASE_WALLET,
        "cash": PAPER_BASE_WALLET,
        "current_wallet": PAPER_BASE_WALLET,
        "daily_session": {},
        "auto_tuner": {},
        "acceptance_metrics_rolling": {"closed_realized_pnl_total_usd": 0.0},
    }

_EMPTY_STATE = {
    "positions": [],
    "history": [],
    "cycle_journal": [],
    "updated_at": None,
    "meta": {},
}


def load_paper_state(path=None):
    """Load paper-trading state from the SQLite Data Warehouse.

    If `path` is provided and differs from the default PAPER_STATE_FILE,
    we fall back to loading from that JSON file (test-isolation path).
    """
    from market_discovery_internal.config import PAPER_STATE_FILE
    if path and os.path.abspath(path) != os.path.abspath(PAPER_STATE_FILE):
        # Isolated / test path — read from JSON file only
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                # Inject meta defaults for any keys missing from the stored state
                defaults = _default_meta()
                loaded.setdefault("meta", {})
                for k, v in defaults.items():
                    loaded["meta"].setdefault(k, v)
                return loaded
            except Exception:
                pass
        # File missing — return fresh state with config-based defaults
        empty = dict(_EMPTY_STATE)
        empty["meta"] = _default_meta()
        return empty

    # [MODUL DB] Migration to Single Source of Truth (SQLite)
    portfolio = db.get_portfolio()
    if not portfolio:
        empty = dict(_EMPTY_STATE)
        empty["meta"] = _default_meta()
        return empty

    positions = db.get_active_positions()
    
    # Load history (Last 100 trades for UI performance)
    conn = db._get_conn()
    history_rows = conn.execute("SELECT raw_json FROM trade_history ORDER BY closed_at DESC LIMIT 100").fetchall()
    history = [json.loads(r['raw_json']) for r in history_rows]

    # Load cycle journal (Last 100 for UI)
    metrics = db.get_latest_metrics(limit=100)

    from market_discovery_internal.config import PAPER_BASE_WALLET
    base_wallet = float(portfolio.get('base_wallet') or PAPER_BASE_WALLET)
    # [ACCOUNTING FIX] Compute cash from first principles: base_wallet + realized_pnl - open_cost_basis
    # This ensures cash is always accurate regardless of prior accounting bugs.
    realized_pnl_total = sum(float(h.get('realized_pnl_usd', 0.0) or 0.0) for h in history)
    open_cost_basis = sum(float(p.get('cost_basis', 0.0) or 0.0) for p in positions)
    cash = round(base_wallet + realized_pnl_total - open_cost_basis, 4)

    state = {
        "positions": positions,
        "history": history,
        "cycle_journal": metrics,
        "updated_at": portfolio.get('updated_at'),
        "meta": {
            "base_wallet": base_wallet,
            "cash": cash,
            "current_wallet": round(cash + open_cost_basis, 4),
            "acceptance_metrics_rolling": {
                "closed_realized_pnl_total_usd": realized_pnl_total
            }
        },
    }

    # Sync additional meta keys if they exist in the latest metric
    if metrics:
        state["meta"].update(metrics[0].get("meta", {}))
        # Re-apply computed cash after metric merge to prevent stale override
        state["meta"]["cash"] = cash
        state["meta"]["current_wallet"] = round(cash + open_cost_basis, 4)

    return state


def save_paper_state(state, path=None):
    """Persist paper-trading state to the SQLite Data Warehouse."""
    if not state or not isinstance(state, dict):
        return

    from market_discovery_internal.config import PAPER_BASE_WALLET
    meta = state.get("meta", {})
    base_wallet = meta.get("base_wallet", PAPER_BASE_WALLET)
    cash = meta.get("cash", PAPER_BASE_WALLET)
    total_pnl = meta.get("acceptance_metrics_rolling", {}).get("closed_realized_pnl_total_usd", 0.0)

    # 1. Update Portfolio Summary
    db.update_portfolio(base_wallet, cash, total_pnl)

    # 2. Update Active Positions (Atomic Replace)
    conn = db._get_conn()
    conn.execute("DELETE FROM active_positions") # Clear to avoid ghosts
    for pos in state.get("positions", []):
        db.add_position(pos)

    # 3. Add Cycle Metric — idempotent: only persist if the latest entry's timestamp
    # differs from the most recently stored metric (prevents double-write per cycle).
    journal = state.get("cycle_journal", [])
    if journal:
        latest_entry = journal[-1]
        latest_ts = latest_entry.get("timestamp", "")
        existing = conn.execute(
            "SELECT 1 FROM cycle_metrics WHERE timestamp = ?", (latest_ts,)
        ).fetchone()
        if not existing:
            db.add_cycle_metric(latest_entry)

    # 4. Persist closed trade history.
    # Dedup key: token_id + opened_at (composite) so the same market token
    # can be traded more than once without losing history entries.
    for trade in state.get("history", []):
        token_id = trade.get("token_id")
        if not token_id:
            continue
        opened_at = trade.get("opened_at", "")
        exists = conn.execute(
            "SELECT 1 FROM trade_history WHERE token_id = ? AND opened_at = ?",
            (token_id, opened_at),
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO trade_history
                   (token_id, city, date, pnl_usd, roi_pct, close_reason, closed_at, opened_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    token_id,
                    trade.get("city"),
                    trade.get("date"),
                    trade.get("realized_pnl_usd"),
                    trade.get("realized_roi_pct"),
                    trade.get("close_reason"),
                    trade.get("closed_at"),
                    opened_at,
                    json.dumps(trade),
                ),
            )
    conn.commit()

    # 5. Mirror to JSON (Mirrored Copy for Dashboard/Manual Debug)
    if path:
        try:
            # Inject meta defaults so the JSON mirror is self-consistent for readers
            mirror = dict(state)
            defaults = _default_meta()
            mirror["meta"] = dict(defaults)
            mirror["meta"].update(state.get("meta") or {})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(mirror, f, indent=2)
            # Ensure it's world-readable for the nginx-served dashboard
            os.chmod(path, 0o644)
        except:
            pass
