from datetime import datetime, timedelta, timezone

from market_discovery import build_weather_evidence, is_weather_evidence_valid


def test_build_weather_evidence_shape_and_values():
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    evidence = build_weather_evidence(
        city="seoul",
        date="2026-04-15",
        forecast_temp_c=23.2,
        now_utc=now,
        fetched_at=now,
    )

    assert evidence["city"] == "seoul"
    assert evidence["date"] == "2026-04-15"
    assert evidence["source"] == "open-meteo"
    assert evidence["forecast_temp_c"] == 23.2
    assert evidence["age_hours"] == 0.0
    assert 0.0 <= evidence["quality_score"] <= 1.0


def test_weather_evidence_valid_when_fresh_and_quality_high():
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    evidence = build_weather_evidence(
        city="seoul",
        date="2026-04-15",
        forecast_temp_c=23.2,
        now_utc=now,
        fetched_at=now,
    )

    assert is_weather_evidence_valid(evidence, max_age_hours=3.0, min_quality_score=0.65) is True


def test_weather_evidence_invalid_when_stale():
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    old = now - timedelta(hours=10)
    evidence = build_weather_evidence(
        city="seoul",
        date="2026-04-15",
        forecast_temp_c=23.2,
        now_utc=now,
        fetched_at=old,
    )

    assert is_weather_evidence_valid(evidence, max_age_hours=3.0, min_quality_score=0.65) is False


def test_weather_evidence_invalid_when_forecast_missing():
    now = datetime(2026, 4, 14, 10, 0, tzinfo=timezone.utc)
    evidence = build_weather_evidence(
        city="seoul",
        date="2026-04-15",
        forecast_temp_c=None,
        now_utc=now,
        fetched_at=now,
    )

    assert is_weather_evidence_valid(evidence, max_age_hours=3.0, min_quality_score=0.65) is False
