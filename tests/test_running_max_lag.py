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
