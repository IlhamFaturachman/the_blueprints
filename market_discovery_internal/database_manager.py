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
        
        # 3. Active Positions
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
                metadata TEXT
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
                raw_json TEXT
            )
        """)

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
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM active_positions").fetchall()
        return [dict(r) for r in rows]

    def add_position(self, pos_dict):
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO active_positions 
            (token_id, city, date, direction, threshold, unit, entry_price, quantity, opened_at, market_slug, icao_code, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pos_dict['token_id'], pos_dict['city'], pos_dict['date'], pos_dict['direction'],
            pos_dict['threshold'], pos_dict['unit'], pos_dict['entry_price'], pos_dict['quantity'],
            pos_dict['opened_at'], pos_dict.get('market_slug'), pos_dict.get('icao_code'),
            json.dumps(pos_dict.get('metadata', {}))
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

# Singleton instance
db = BlueprintsDB()
