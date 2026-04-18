import sqlite3
import threading
import os
import json
from datetime import datetime, timezone

class BlueprintsDB:
    """Thread-safe SQLite manager for the Blueprints Data Warehouse (Gudang Data)."""
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(BlueprintsDB, cls).__new__(cls)
            return cls._instance

    def __init__(self, db_path="logs/blueprints_master.db"):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._local = threading.local()
        self._initialize_db()
        self._initialized = True

    def _get_conn(self):
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
            # [MODUL DB] Enable WAL mode for high concurrency
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            # [MODUL DB] Use FULL synchronization for absolute durability against corruption
            self._local.conn.execute("PRAGMA synchronous=FULL")
            
            # [MODUL DB] Integrity Shield: Check for corruption on every first connection
            try:
                check = self._local.conn.execute("PRAGMA integrity_check").fetchone()
                if check[0] != "ok":
                    print(f"[CRITICAL] Database integrity check FAILED: {check[0]}")
            except sqlite3.Error as e:
                print(f"[CRITICAL] Database integrity check ERROR: {e}")
        return self._local.conn

    def _initialize_db(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 1. Weather Archive (10-year data)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS weather_archive (
                city TEXT,
                month_day TEXT,
                max_temp REAL,
                min_temp REAL,
                precipitation REAL,
                ts REAL,
                PRIMARY KEY (city, month_day)
            )
        """)
        
        # 2. Portfolio Summary (Single Source of Truth for Balance)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_summary (
                id INTEGER PRIMARY KEY DEFAULT 1,
                base_wallet REAL,
                cash REAL,
                total_pnl REAL,
                updated_at TEXT
            )
        """)
        
        # 3. Active Positions — raw_json stores the FULL position object for lossless restore.
        # Indexed columns are kept for query convenience only; raw_json is the source of truth.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_positions (
                token_id TEXT PRIMARY KEY,
                city TEXT,
                date TEXT,
                direction TEXT,
                threshold REAL,
                unit TEXT,
                entry_price REAL,
                quantity REAL,
                opened_at TEXT,
                market_slug TEXT,
                icao_code TEXT,
                metadata TEXT,
                raw_json TEXT
            )
        """)
        
        # 4. Trade History
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT,
                city TEXT,
                date TEXT,
                pnl_usd REAL,
                roi_pct REAL,
                close_reason TEXT,
                closed_at TEXT,
                opened_at TEXT,
                raw_json TEXT
            )
        """)
        # Migration: add opened_at column for idempotency if table already exists without it
        try:
            cursor.execute("ALTER TABLE trade_history ADD COLUMN opened_at TEXT")
        except Exception:
            pass  # Column already exists

        # 5. Cycle Metrics (For Dashboard UI)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cycle_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                data_json TEXT
            )
        """)

        # 6. Discovery Cache (For Instant Filtering)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS discovery_cache (
                city TEXT,
                date TEXT,
                forecast_temp REAL,
                source TEXT,
                ts REAL,
                PRIMARY KEY (city, date)
            )
        """)

        # 7. Process Heartbeat (Watchdog Module)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS process_heartbeat (
                process_name TEXT PRIMARY KEY,
                last_pulse TEXT
            )
        """)

        # 8. AI Usage Ledger — atomic, thread-safe replacement for ai_usage_ledger.json
        # monthly: one row per month, accumulates total cost_usd
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_monthly_cost (
                month_key TEXT PRIMARY KEY,
                total_cost_usd REAL NOT NULL DEFAULT 0.0
            )
        """)
        # daily: one row per (day_key, call_kind), tracks call count
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_daily_calls (
                day_key TEXT NOT NULL,
                call_kind TEXT NOT NULL,
                call_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day_key, call_kind)
            )
        """)

        # 9. [PACK A] Probability Calibration Warehouse
        # Tracks prediction accuracy per (city, direction, horizon_bin, price_bin).
        # horizon_bin: 1=<12h, 2=12-24h, 3=24+h to resolve at entry.
        # price_bin: floor(yes_price * 10) → 0-9 (0-10%, 10-20%, ..., 90-100%).
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS calibration_stats (
                city TEXT NOT NULL,
                direction TEXT NOT NULL,
                horizon_bin INTEGER NOT NULL,
                price_bin INTEGER NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                brier_sum REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (city, direction, horizon_bin, price_bin)
            )
        """)

        # Schema migrations — idempotent via try/except for each new column.
        _migrations = [
            "ALTER TABLE active_positions ADD COLUMN raw_json TEXT",
            "ALTER TABLE trade_history ADD COLUMN direction TEXT",
            "ALTER TABLE trade_history ADD COLUMN entry_bucket TEXT",
            "ALTER TABLE trade_history ADD COLUMN horizon_bin INTEGER",
            "ALTER TABLE trade_history ADD COLUMN price_bin INTEGER",
            "ALTER TABLE trade_history ADD COLUMN entry_model_prob REAL",
            "ALTER TABLE trade_history ADD COLUMN outcome INTEGER",
        ]
        for migration in _migrations:
            try:
                cursor.execute(migration)
                conn.commit()
            except Exception:
                pass  # Column already exists

        conn.commit()

    def update_heartbeat(self, process_name):
        """Update the heartbeat timestamp for a specific process."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO process_heartbeat (process_name, last_pulse)
            VALUES (?, ?)
        """, (process_name, datetime.now(timezone.utc).isoformat()))
        conn.commit()

    def get_heartbeat(self, process_name):
        """Retrieve the last pulse timestamp for a specific process."""
        conn = self._get_conn()
        res = conn.execute(
            "SELECT last_pulse FROM process_heartbeat WHERE process_name = ?",
            (process_name,)
        ).fetchone()
        return res["last_pulse"] if res else None

    # --- Weather Logic ---
    def get_cached_forecast(self, city, date, ttl_seconds=3600):
        """Retrieve a fresh verified forecast from the discovery cache."""
        conn = self._get_conn()
        res = conn.execute(
            "SELECT forecast_temp, source, ts FROM discovery_cache WHERE city = ? AND date = ?",
            (city.lower(), date)
        ).fetchone()
        if res:
            if (datetime.now().timestamp() - res['ts']) < ttl_seconds:
                return dict(res)
        return None

    def save_cached_forecast(self, city, date, temp, source):
        """Save a verified forecast to the discovery cache."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO discovery_cache (city, date, forecast_temp, source, ts)
            VALUES (?, ?, ?, ?, ?)
        """, (city.lower(), date, temp, source, datetime.now().timestamp()))
        conn.commit()
    def get_bulk_cached_forecasts(self, keys, ttl_seconds=3600):
        """Retrieve multiple verified forecasts in one query."""
        if not keys: return {}
        conn = self._get_conn()
        
        # Build IN clause (city, date) pairs
        # SQLite doesn't directly support (a,b) IN ((1,2),(3,4)), so we use OR or a temp table
        # For small batches (30-60 cities), OR is fine.
        clauses = []
        params = []
        for city, date in keys:
            clauses.append("(city = ? AND date = ?)")
            params.extend([city.lower(), date])
        
        query = f"SELECT city, date, forecast_temp, source, ts FROM discovery_cache WHERE {' OR '.join(clauses)}"
        rows = conn.execute(query, params).fetchall()
        
        result = {}
        now = datetime.now().timestamp()
        for r in rows:
            if (now - r['ts']) < ttl_seconds:
                result[(r['city'], r['date'])] = dict(r)
        return result

    def get_weather(self, city, month_day):
        conn = self._get_conn()
        res = conn.execute(
            "SELECT max_temp, min_temp, precipitation, ts FROM weather_archive WHERE city = ? AND month_day = ?",
            (city.lower(), month_day)
        ).fetchone()
        return dict(res) if res else None

    def save_weather(self, city, month_day, max_temp=None, min_temp=None, precip=None):
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO weather_archive (city, month_day, max_temp, min_temp, precipitation, ts)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (city.lower(), month_day, max_temp, min_temp, precip, datetime.now().timestamp()))
        conn.commit()

    # --- Portfolio & Position Logic ---
    def get_portfolio(self):
        conn = self._get_conn()
        res = conn.execute("SELECT * FROM portfolio_summary WHERE id = 1").fetchone()
        return dict(res) if res else None

    def update_portfolio(self, base_wallet, cash, total_pnl):
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO portfolio_summary (id, base_wallet, cash, total_pnl, updated_at)
            VALUES (1, ?, ?, ?, ?)
        """, (base_wallet, cash, total_pnl, datetime.now(timezone.utc).isoformat()))
        conn.commit()

    def get_active_positions(self):
        """Restore full position objects from raw_json for lossless runtime shape."""
        conn = self._get_conn()
        rows = conn.execute("SELECT raw_json FROM active_positions WHERE raw_json IS NOT NULL").fetchall()
        positions = []
        for r in rows:
            try:
                positions.append(json.loads(r['raw_json']))
            except (json.JSONDecodeError, TypeError):
                pass
        return positions

    def add_position(self, pos_dict):
        """Persist a position — stores full payload as raw_json for lossless restore."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO active_positions
            (token_id, city, date, direction, threshold, unit, entry_price, quantity,
             opened_at, market_slug, icao_code, metadata, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pos_dict.get('token_id'), pos_dict.get('city'), pos_dict.get('date'),
            pos_dict.get('direction'), pos_dict.get('threshold'), pos_dict.get('unit'),
            pos_dict.get('entry_price'), pos_dict.get('quantity'), pos_dict.get('opened_at'),
            pos_dict.get('market_slug'), pos_dict.get('icao_code'),
            json.dumps(pos_dict.get('metadata', {})),
            json.dumps(pos_dict),
        ))
        conn.commit()

    def remove_position(self, token_id):
        conn = self._get_conn()
        conn.execute("DELETE FROM active_positions WHERE token_id = ?", (token_id,))
        conn.commit()

    # --- Metrics & History ---
    def add_cycle_metric(self, metric_dict):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO cycle_metrics (timestamp, data_json) VALUES (?, ?)",
            (metric_dict.get('timestamp', datetime.now(timezone.utc).isoformat()), json.dumps(metric_dict))
        )
        # Keep only last 500 metrics to save space
        conn.execute("DELETE FROM cycle_metrics WHERE id NOT IN (SELECT id FROM cycle_metrics ORDER BY id DESC LIMIT 500)")
        conn.commit()

    def get_latest_metrics(self, limit=100):
        conn = self._get_conn()
        rows = conn.execute("SELECT data_json FROM cycle_metrics ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r['data_json']) for r in rows]

    # --- AI Usage Ledger (Atomic SQLite replacement for ai_usage_ledger.json) ---

    def ai_get_month_cost(self, month_key):
        """Return total AI cost in USD for a given month key (e.g. '2026-04')."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT total_cost_usd FROM ai_monthly_cost WHERE month_key = ?", (month_key,)
        ).fetchone()
        return float(row["total_cost_usd"]) if row else 0.0

    def ai_add_month_cost(self, month_key, cost_usd):
        """Atomically add cost_usd to the monthly accumulator."""
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO ai_monthly_cost (month_key, total_cost_usd)
            VALUES (?, ?)
            ON CONFLICT(month_key) DO UPDATE SET total_cost_usd = total_cost_usd + excluded.total_cost_usd
        """, (month_key, float(cost_usd)))
        conn.commit()

    def ai_get_day_calls(self, day_key, call_kind):
        """Return call count for a given day+kind pair."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT call_count FROM ai_daily_calls WHERE day_key = ? AND call_kind = ?",
            (day_key, call_kind)
        ).fetchone()
        return int(row["call_count"]) if row else 0

    def ai_try_reserve_call(self, day_key, call_kind, limit):
        """Atomically increment daily call count if below limit.
        Returns True if the slot was reserved, False if limit already reached."""
        conn = self._get_conn()
        # Ensure row exists
        conn.execute("""
            INSERT OR IGNORE INTO ai_daily_calls (day_key, call_kind, call_count)
            VALUES (?, ?, 0)
        """, (day_key, call_kind))
        # Read current count
        row = conn.execute(
            "SELECT call_count FROM ai_daily_calls WHERE day_key = ? AND call_kind = ?",
            (day_key, call_kind)
        ).fetchone()
        current = int(row["call_count"]) if row else 0
        if current >= limit:
            conn.commit()
            return False
        conn.execute("""
            UPDATE ai_daily_calls SET call_count = call_count + 1
            WHERE day_key = ? AND call_kind = ?
        """, (day_key, call_kind))
        conn.commit()
        return True

    # --- [PACK A] Probability Calibration ---

    def get_calibration(self, city, direction, horizon_bin, price_bin):
        """Return (hits, total, brier_sum) for a calibration key. Returns (0,0,0.0) if missing."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT hits, total, brier_sum FROM calibration_stats "
            "WHERE city=? AND direction=? AND horizon_bin=? AND price_bin=?",
            (str(city).lower(), str(direction), int(horizon_bin), int(price_bin))
        ).fetchone()
        if row:
            return int(row["hits"]), int(row["total"]), float(row["brier_sum"])
        return 0, 0, 0.0

    def update_calibration(self, city, direction, horizon_bin, price_bin, outcome, model_prob):
        """Record one prediction outcome into the calibration table.
        outcome: 1 if YES resolved, 0 if NO resolved.
        model_prob: the raw model probability at entry.
        """
        brier = (float(model_prob) - float(outcome)) ** 2
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO calibration_stats (city, direction, horizon_bin, price_bin, hits, total, brier_sum)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(city, direction, horizon_bin, price_bin)
            DO UPDATE SET
                hits = hits + excluded.hits,
                total = total + 1,
                brier_sum = brier_sum + excluded.brier_sum
        """, (str(city).lower(), str(direction), int(horizon_bin), int(price_bin),
              int(outcome), brier))
        conn.commit()

    def get_trade_history_for_city(self, city, limit=30):
        """Return recent closed trades for a city (for auto-tuner)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT pnl_usd, close_reason, raw_json FROM trade_history "
            "WHERE city = ? ORDER BY id DESC LIMIT ?",
            (str(city).lower(), int(limit))
        ).fetchall()
        result = []
        for r in rows:
            try:
                obj = json.loads(r["raw_json"]) if r["raw_json"] else {}
            except Exception:
                obj = {}
            obj.setdefault("pnl_usd", r["pnl_usd"])
            obj.setdefault("close_reason", r["close_reason"])
            result.append(obj)
        return result

    def get_recent_trade_history(self, limit=200):
        """Return last N trades across all cities for profit attribution."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT raw_json, pnl_usd, city, close_reason FROM trade_history ORDER BY id DESC LIMIT ?",
            (int(limit),)
        ).fetchall()
        result = []
        for r in rows:
            try:
                obj = json.loads(r["raw_json"]) if r["raw_json"] else {}
            except Exception:
                obj = {}
            obj.setdefault("pnl_usd", r["pnl_usd"])
            obj.setdefault("city", r["city"])
            obj.setdefault("close_reason", r["close_reason"])
            result.append(obj)
        return result

    def record_trade_history(self, position):
        """Persist a closed position to trade_history with calibration fields."""
        conn = self._get_conn()
        token_id = position.get("token_id")
        opened_at = position.get("opened_at", "")
        # Idempotency guard: block duplicate close records for the same position instance
        if token_id and opened_at:
            exists = conn.execute(
                "SELECT 1 FROM trade_history WHERE token_id = ? AND opened_at = ?",
                (token_id, opened_at)
            ).fetchone()
            if exists:
                return None
        pnl = float(position.get("realized_pnl_usd", 0.0))
        roi = float(position.get("realized_roi_pct", 0.0))
        exit_price = float(position.get("exit_price", 0.0))
        outcome = 1 if exit_price >= 0.9 else (0 if exit_price <= 0.1 else None)
        conn.execute("""
            INSERT INTO trade_history
            (token_id, city, date, pnl_usd, roi_pct, close_reason, closed_at, opened_at,
             direction, entry_bucket, horizon_bin, price_bin, entry_model_prob, outcome, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            token_id,
            str(position.get("city", "")).lower(),
            position.get("date"),
            pnl, roi,
            position.get("close_reason"),
            position.get("closed_at"),
            opened_at,
            position.get("direction"),
            position.get("entry_bucket"),
            position.get("horizon_bin"),
            position.get("price_bin"),
            position.get("entry_model_prob"),
            outcome,
            json.dumps(position),
        ))
        conn.commit()
        return outcome


# Singleton instance
db = BlueprintsDB()
