import market_discovery as md
from market_discovery_internal.config import (
    ENTRY_BUCKET_HOLD_MIN_PROB, ENTRY_BUCKET_HOLD_MIN_EDGE,
    ENTRY_BUCKET_WATCH_MAX_PRICE
)


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


def test_bucket_rejects_price_outside_valid_range():
    """Price outside [min, max] entry range → reject."""
    opp = make_opportunity(yes_price=0.50, model_prob=0.6, edge=0.1)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.40)
    assert result["bucket"] == "reject"


def test_bucket_watchlists_price_in_watchlist_range():
    """Price in [min_entry, WATCH_MAX_PRICE] → watchlist (monitoring)."""
    opp = make_opportunity(yes_price=0.10, model_prob=0.75, edge=0.40)
    result = md.decide_entry_bucket(opp, min_entry_price=0.05, max_entry_price=0.50)
    assert result["bucket"] == "watchlist"


def test_bucket_enters_swing_when_price_in_swing_range():
    """Price in (WATCH_MAX_PRICE, max_entry] → enter_swing."""
    # Price must be > ENTRY_BUCKET_WATCH_MAX_PRICE (0.15) and <= max_entry_price
    opp = make_opportunity(yes_price=0.45, model_prob=0.75, edge=0.40, hours_until_resolve=36)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.50)
    assert result["bucket"] == "enter_swing"


def test_bucket_enters_hold_candidate_for_high_confidence_setup():
    """Prob >= HOLD_MIN_PROB and edge >= HOLD_MIN_EDGE → enter_hold_candidate."""
    opp = make_opportunity(yes_price=0.25, model_prob=1.0, edge=0.80, hours_until_resolve=10)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.50)
    assert result["bucket"] == "enter_hold_candidate"
    assert "HOLD criteria" in result["reason"]


def test_ai_bucket_override_applies_when_present():
    """When ai_bucket is set on opportunity, it takes priority (AI decision path)."""
    opp = make_opportunity(yes_price=0.25, model_prob=0.75, edge=0.40, hours_until_resolve=36)
    opp["ai_bucket"] = "enter_hold_candidate"
    opp["ai_confidence"] = 0.95

    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.50)

    assert result["bucket"] == "enter_hold_candidate"
    assert result["reason"] == "ai_decision"


def test_hold_requires_both_prob_and_edge_thresholds():
    """If prob is high but edge is below HOLD_MIN_EDGE, should NOT be hold_candidate.
    With ENTRY_BUCKET_WATCH_MAX_PRICE=0.15, price 0.25 lands in enter_swing (not watchlist).
    """
    opp = make_opportunity(yes_price=0.25, model_prob=0.95, edge=0.50)
    result = md.decide_entry_bucket(opp, min_entry_price=0.2, max_entry_price=0.50)
    # Edge 0.50 < ENTRY_BUCKET_HOLD_MIN_EDGE (0.60), so not hold_candidate.
    # Price 0.25 > ENTRY_BUCKET_WATCH_MAX_PRICE (0.15), so enters swing.
    assert result["bucket"] == "enter_swing"
