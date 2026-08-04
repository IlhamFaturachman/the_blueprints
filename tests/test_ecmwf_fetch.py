# tests/test_ecmwf_fetch.py
"""Tests for ECMWF fetch, EMOS calibration, and IEM label integration."""
import pytest


def test_ecmwf_config_constants_exist():
    from market_discovery_internal.config import (
        ECMWF_OPEN_DATA_ENABLED,
        ECMWF_ENSEMBLE_MEMBERS,
        ECMWF_FORECAST_VARIABLE,
        EMOS_TRAINING_WINDOW_DAYS,
        EMOS_MIN_TRAINING_SAMPLES,
        IEM_API_BASE,
        IEM_BACKFILL_DAYS,
    )
    assert ECMWF_OPEN_DATA_ENABLED in (True, False)
    assert ECMWF_ENSEMBLE_MEMBERS == 51
    assert ECMWF_FORECAST_VARIABLE == "2t"
    assert EMOS_TRAINING_WINDOW_DAYS >= 30
    assert EMOS_MIN_TRAINING_SAMPLES >= 20
    assert "iastate" in IEM_API_BASE
    assert IEM_BACKFILL_DAYS >= 365


def test_ecmwf_ensemble_table_crud(tmp_path):
    import os, threading
    from market_discovery_internal.database_manager import BlueprintsDB
    test_db = BlueprintsDB.__new__(BlueprintsDB)
    test_db.db_path = str(tmp_path / "test_ecmwf.db")
    test_db._local = threading.local()
    os.makedirs(str(tmp_path), exist_ok=True)
    test_db._initialized = True

    conn = test_db._get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ecmwf_ensemble_raw (
            city TEXT NOT NULL, date TEXT NOT NULL, member_id INTEGER NOT NULL,
            forecast_temp REAL, fetched_at TEXT,
            PRIMARY KEY (city, date, member_id)
        )
    """)
    conn.commit()

    test_db.save_ecmwf_ensemble("dallas", "2026-08-05", [
        {"member_id": 0, "temp_c": 35.2},
        {"member_id": 1, "temp_c": 34.8},
        {"member_id": 2, "temp_c": 36.1},
    ])
    members = test_db.get_ecmwf_ensemble("dallas", "2026-08-05")
    assert len(members) == 3
    assert members[0]["forecast_temp"] == 35.2
    stats = test_db.get_ecmwf_ensemble_stats("dallas", "2026-08-05")
    assert stats["count"] == 3
    assert abs(stats["mean"] - 35.367) < 0.01