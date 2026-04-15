import market_discovery as md


def make_opportunity():
    return {
        "city": "seoul",
        "date": "2026-04-16",
        "market_question": "Will Seoul be 18C or higher?",
        "threshold": 18.0,
        "unit": "C",
        "direction": "above",
        "yes_price": 0.25,
        "token_id": "0xabc",
        "hours_until_resolve": 12.0,
        "model_prob": 1.0,
        "edge": 0.75,
        "weather_evidence": {"quality_score": 0.9, "age_hours": 0.1},
    }


def test_ai_disabled_keeps_deterministic_path(monkeypatch):
    monkeypatch.setattr(md, "AI_AGENT_ENABLED", False)
    opp = make_opportunity()

    enriched = md._maybe_apply_ai_decision(opp)

    assert enriched["ai_status"] == "off"
    assert "ai_bucket" not in enriched
    assert "ai_confidence" not in enriched


def test_ai_enabled_missing_config_falls_back_safely(monkeypatch):
    monkeypatch.setattr(md, "AI_AGENT_ENABLED", True)
    monkeypatch.delenv("AI_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("AI_AGENT_MODEL", raising=False)
    monkeypatch.delenv("AI_AGENT_API_KEY", raising=False)

    enriched = md._maybe_apply_ai_decision(make_opportunity())

    assert enriched["ai_status"] == "missing_config"
    assert "ai_bucket" not in enriched


def test_ai_mock_provider_applies_signal(monkeypatch):
    monkeypatch.setattr(md, "AI_AGENT_ENABLED", True)
    monkeypatch.setenv("AI_AGENT_PROVIDER", "mock")
    monkeypatch.setenv("AI_AGENT_MODEL", "mock-model")
    monkeypatch.setenv("AI_AGENT_API_KEY", "dummy")

    enriched = md._maybe_apply_ai_decision(make_opportunity())

    assert enriched["ai_status"] == "applied"
    assert enriched["ai_bucket"] in {"reject", "watchlist", "enter_swing", "enter_hold_candidate"}
    assert 0.0 <= float(enriched["ai_confidence"]) <= 1.0


def test_ai_unknown_provider_falls_back_error(monkeypatch):
    monkeypatch.setattr(md, "AI_AGENT_ENABLED", True)
    monkeypatch.setenv("AI_AGENT_PROVIDER", "unknown-provider")
    monkeypatch.setenv("AI_AGENT_MODEL", "x")
    monkeypatch.setenv("AI_AGENT_API_KEY", "dummy")

    enriched = md._maybe_apply_ai_decision(make_opportunity())

    assert enriched["ai_status"] == "fallback_error"
    assert "ai_error" in enriched
