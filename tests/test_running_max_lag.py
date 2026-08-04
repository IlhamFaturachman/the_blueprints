# tests/test_running_max_lag.py
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


def test_running_max_accumulates():
    from market_discovery_internal.running_max_tracker import RunningMaxTracker
    tracker = RunningMaxTracker(icao="KDFW", local_tz="America/Chicago")
    tracker.update(20.0, datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc))
    tracker.update(25.0, datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc))
    tracker.update(22.0, datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc))
    assert tracker.running_max == 25.0


def test_running_max_resets_at_midnight():
    from market_discovery_internal.running_max_tracker import RunningMaxTracker
    tracker = RunningMaxTracker(icao="KDFW", local_tz="America/Chicago")
    tracker.update(30.0, datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc))
    assert tracker.running_max == 30.0
    tracker.update(15.0, datetime(2026, 8, 6, 13, 0, tzinfo=timezone.utc))
    assert tracker.running_max == 15.0


def test_running_max_second_max():
    from market_discovery_internal.running_max_tracker import RunningMaxTracker
    tracker = RunningMaxTracker(icao="KDFW", local_tz="America/Chicago")
    tracker.update(20.0, datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc))
    tracker.update(25.0, datetime(2026, 8, 5, 15, 0, tzinfo=timezone.utc))
    tracker.update(22.0, datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc))
    assert tracker.running_max == 25.0
    assert tracker.second_max == 22.0
    assert tracker.margin == pytest.approx(3.0, abs=0.01)


def test_fetch_metar_24h():
    from market_discovery_internal.running_max_tracker import fetch_metar_24h
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"temp": 20.0, "reportTime": "2026-08-05T14:00:00Z"},
        {"temp": 25.0, "reportTime": "2026-08-05T15:00:00Z"},
    ]
    with patch("market_discovery_internal.running_max_tracker.requests.get", return_value=mock_resp):
        obs = fetch_metar_24h("KDFW")
    assert len(obs) == 2
    assert obs[0]["temp"] == 20.0


def test_determine_winning_bracket():
    from market_discovery_internal.running_max_tracker import determine_winning_bracket
    brackets = [
        {"threshold": 30, "threshold_high": 31, "token_id": "t30"},
        {"threshold": 35, "threshold_high": 36, "token_id": "t35"},
    ]
    winner = determine_winning_bracket(35.2, brackets)
    assert winner["token_id"] == "t35"


def test_determine_winning_bracket_no_match():
    from market_discovery_internal.running_max_tracker import determine_winning_bracket
    brackets = [{"threshold": 30, "threshold_high": 31, "token_id": "t30"}]
    assert determine_winning_bracket(40.0, brackets) is None


def test_lag_study_records_lock_event():
    """Test that lock detection works — recording the lock event itself, not delayed price fetches."""
    from scripts.running_max_lag_study import detect_lock_event
    mock_metar = [{"temp": 35.2, "reportTime": "2026-08-05T22:00:00Z"}]
    mock_quote = {"bid": 0.14, "ask": 0.15}
    brackets = [{"threshold": 35, "threshold_high": 36, "token_id": "tok35"}]
    with patch("market_discovery_internal.running_max_tracker.fetch_metar_24h", return_value=mock_metar), \
         patch("scripts.running_max_lag_study.fetch_orderbook_quote", return_value=mock_quote):
        result = detect_lock_event("dallas", "KDFW", "America/Chicago", brackets)
    assert result is not None
    assert result["winning_bracket_token"] == "tok35"
    assert result["price_at_lock"] == 0.15
    assert result["running_max_c"] == 35.2


def test_detect_lock_returns_none_before_min_hour():
    """Before lock_min_hour_local, should return None."""
    from scripts.running_max_lag_study import detect_lock_event
    mock_metar = [{"temp": 35.2, "reportTime": "2026-08-05T15:00:00Z"}]
    brackets = [{"threshold": 35, "threshold_high": 36, "token_id": "tok35"}]
    with patch("market_discovery_internal.running_max_tracker.fetch_metar_24h", return_value=mock_metar):
        result = detect_lock_event("dallas", "KDFW", "America/Chicago", brackets,
                                   lock_min_hour_local=16)
    assert result is None


def test_track_price_convergence_records_over_time():
    """Test that price tracker records prices at wall-clock intervals using injectable quote_fn."""
    from scripts.running_max_lag_study import track_price_convergence
    prices_by_time = {
        0: {"bid": 0.14, "ask": 0.15},
        60: {"bid": 0.18, "ask": 0.20},
        300: {"bid": 0.28, "ask": 0.30},
        900: {"bid": 0.40, "ask": 0.42},
        1800: {"bid": 0.55, "ask": 0.58},
        3600: {"bid": 0.72, "ask": 0.75},
        7200: {"bid": 0.85, "ask": 0.88},
        10800: {"bid": 0.92, "ask": 0.95},
    }
    def mock_quote_fn(token_id, elapsed_seconds):
        return prices_by_time.get(elapsed_seconds)
    result = track_price_convergence(
        token_id="tok35",
        entry_price=0.15,
        quote_fn=mock_quote_fn,
        intervals=[("+1m", 60), ("+5m", 300), ("+15m", 900),
                    ("+30m", 1800), ("+1h", 3600), ("+2h", 7200), ("+3h", 10800)],
    )
    assert result["+30m"]["bid"] == 0.55
    assert result["time_to_050"] == 1800
    assert result["time_to_090"] == 10800
