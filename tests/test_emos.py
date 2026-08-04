# tests/test_emos.py
import pytest, math


def test_gaussian_crps():
    from market_discovery_internal.emos import gaussian_crps
    crps = gaussian_crps(0.0, 1.0, 0.0)
    expected = (math.sqrt(2) - 1) / math.sqrt(math.pi)
    assert abs(crps - expected) < 0.001


def test_emos_optimize():
    from market_discovery_internal.emos import fit_emos, predict_emos
    import random; random.seed(42)
    n = 100
    means = [20.0 + random.gauss(0, 1) for _ in range(n)]
    stds = [0.5 + abs(random.gauss(0, 0.3)) for _ in range(n)]
    obs = [random.gauss(1.0 + 0.9 * means[i], max(0.1, 0.5 + 0.1 * stds[i])) for i in range(n)]
    model = fit_emos(means, stds, obs)
    pred = predict_emos(model, 25.0, 1.0)
    assert "mu" in pred and "sigma" in pred
    assert pred["sigma"] > 0
    assert abs(model["b"]) > 0.5


def test_emos_predict():
    from market_discovery_internal.emos import predict_emos
    pred = predict_emos({"a": 0.0, "b": 1.0, "c": 0.5, "d": 0.5}, 20.0, 1.0)
    assert abs(pred["mu"] - 20.0) < 0.01
    assert pred["sigma"] > 0


def test_emos_insufficient():
    from market_discovery_internal.emos import fit_emos
    assert fit_emos([20.0], [0.5], [20.5], min_samples=30) is None


def test_bracket_prob():
    from market_discovery_internal.emos import compute_bracket_prob
    prob = compute_bracket_prob(35.0, 1.0, 34.5, 35.5)
    assert abs(prob - 0.383) < 0.05
