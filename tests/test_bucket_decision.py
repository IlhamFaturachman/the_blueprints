import market_discovery as md


def make_opportunity(yes_price=0.25, model_prob=1.0, edge=0.75, hours_until_resolve=12):
    return {
        "city": "seoul",
        "date": "2026-04-16",
        "market_question": "Will the lowest temperature in Seoul be 18C or higher?",
        "threshold": 18.0,
        "unit": "C",
        "direction": "above",
        "yes_price": yes_price,
        "token_id": "0xabc",
        "hours_until_resolve": hours_until_resolve,
        "model_prob": model_prob,
        "edge": edge,
    }


def test_bucket_rejects_low_model_prob():
    opp = make_opportunity(model_prob=0.6, edge=0.5)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.3)
    assert result["bucket"] == "reject"
    assert result["reason"] == "low_model_prob"


def test_bucket_watchlists_price_outside_entry_band():
    opp = make_opportunity(yes_price=0.34, model_prob=1.0, edge=0.66)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.3)
    assert result["bucket"] == "watchlist"
    assert result["reason"] == "out_of_entry_band_watch"


def test_bucket_enters_swing_when_not_hold_candidate():
    opp = make_opportunity(yes_price=0.25, model_prob=0.75, edge=0.40, hours_until_resolve=36)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.3)
    assert result["bucket"] == "enter_swing"


def test_bucket_enters_hold_candidate_for_high_confidence_setup():
    opp = make_opportunity(yes_price=0.25, model_prob=1.0, edge=0.80, hours_until_resolve=10)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.3)
    assert result["bucket"] == "enter_hold_candidate"
    assert result["reason"] == "high_confidence_hold_candidate"


def test_ai_override_applies_when_enabled_and_confident(monkeypatch):
    opp = make_opportunity(yes_price=0.25, model_prob=0.75, edge=0.40, hours_until_resolve=36)
    opp["ai_bucket"] = "enter_hold_candidate"
    opp["ai_confidence"] = 0.95

    monkeypatch.setattr(md, "AI_AGENT_ENABLED", True)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.3)

    assert result["bucket"] == "enter_hold_candidate"
    assert result["ai_override_applied"] is True


def test_ai_override_cannot_bypass_entry_band_guardrail(monkeypatch):
    opp = make_opportunity(yes_price=0.35, model_prob=1.0, edge=0.65, hours_until_resolve=10)
    opp["ai_bucket"] = "enter_swing"
    opp["ai_confidence"] = 0.95

    monkeypatch.setattr(md, "AI_AGENT_ENABLED", True)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.3)

    assert result["bucket"] == "watchlist"
    assert result["ai_override_applied"] is False
    assert result["ai_override_reason"] == "blocked_by_entry_band"
