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
