# tests/test_lock_probability.py
import pytest


def test_lock_prob_increases_with_hour():
    from market_discovery_internal.lock_probability import compute_lock_probability
    p14 = compute_lock_probability(hour_local=14, margin_c=0.0, city="dallas")
    p17 = compute_lock_probability(hour_local=17, margin_c=0.0, city="dallas")
    p20 = compute_lock_probability(hour_local=20, margin_c=0.0, city="dallas")
    assert p14 < p17 < p20
    assert p20 > 0.90


def test_lock_prob_higher_with_margin():
    from market_discovery_internal.lock_probability import compute_lock_probability
    p_small = compute_lock_probability(hour_local=16, margin_c=0.5, city="dallas")
    p_large = compute_lock_probability(hour_local=16, margin_c=3.0, city="dallas")
    assert p_large > p_small


def test_lock_prob_low_before_noon():
    from market_discovery_internal.lock_probability import compute_lock_probability
    p = compute_lock_probability(hour_local=10, margin_c=0.0, city="dallas")
    assert p < 0.10


def test_lock_prob_clamped():
    from market_discovery_internal.lock_probability import compute_lock_probability
    p = compute_lock_probability(hour_local=23, margin_c=10.0, city="dallas")
    assert 0.0 <= p <= 1.0


def test_fit_lock_model():
    from market_discovery_internal.lock_probability import fit_lock_model, compute_lock_probability
    history = []
    for day in range(30):
        temps = {h: 20.0 for h in range(24)}
        temps[15] = 30.0
        for h in range(16, 24):
            temps[h] = 30.0 - (h - 15) * 0.5
        history.append({"day": day, "temps_by_hour": temps, "final_max": 30.0, "final_max_hour": 15})
    model = fit_lock_model(history, city="test")
    p17 = compute_lock_probability(hour_local=17, margin_c=1.0, city="test", model=model)
    assert p17 > 0.80
