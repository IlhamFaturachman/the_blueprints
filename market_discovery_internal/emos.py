# market_discovery_internal/emos.py
"""EMOS per-station bias correction. Fits Gaussian: mu=a+b*mean, sigma^2=c+d*var.
Minimizes CRPS. Training: 60-day rolling, IEM labels. Fallback: raw ensemble.

Literature context (Homleid 1995, Bremnes 2024):
- EMOS/Kalman removes bias but does NOT reduce standard deviation.
- After bias correction, forecast error goes from ~1.15C to ~0.76C.
- Sigma stays ~0.95C — still slightly worse than market (0.90C).
- Main value: stops wrong-bracket losses, not creates edge over market.
"""
import logging, math
from typing import Optional

from market_discovery_internal.config import EMOS_MIN_TRAINING_SAMPLES

logger = logging.getLogger(__name__)


def gaussian_crps(mu, sigma, y):
    """CRPS of Gaussian(mu, sigma) at observation y."""
    if sigma <= 0:
        return abs(mu - y)
    z = (y - mu) / sigma
    return sigma * (z * (2 * _norm_cdf(z) - 1) + 2 * _norm_pdf(z) - 1 / math.sqrt(math.pi))


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def fit_emos(ensemble_means, ensemble_stds, observations, min_samples=EMOS_MIN_TRAINING_SAMPLES):
    """Fit EMOS coefficients by CRPS minimization via grid search."""
    n = len(observations)
    if n < min_samples:
        return None
    variances = [s * s for s in ensemble_stds]
    best_crps = 1e9
    best = {"a": 0.0, "b": 1.0, "c": 1.0, "d": 1.0}
    for a in [-2, -1, 0, 1, 2]:
        for b in [0.7, 0.8, 0.9, 1.0, 1.1]:
            for c in [0.1, 0.5, 1.0, 2.0]:
                for d in [0.5, 1.0, 1.5, 2.0]:
                    total = 0.0
                    for i in range(n):
                        mu = a + b * ensemble_means[i]
                        sig = math.sqrt(max(0.01, c + d * variances[i]))
                        total += gaussian_crps(mu, sig, observations[i])
                    avg = total / n
                    if avg < best_crps:
                        best_crps = avg
                        best = {"a": a, "b": b, "c": c, "d": d}
    logger.info("[EMOS] Fitted: a=%.2f b=%.2f c=%.2f d=%.2f crps=%.4f (n=%d)",
                best["a"], best["b"], best["c"], best["d"], best_crps, n)
    return best


def predict_emos(model, ensemble_mean, ensemble_std):
    """Predict calibrated mu and sigma from EMOS model."""
    if model is None:
        return {"mu": ensemble_mean, "sigma": max(0.1, ensemble_std)}
    mu = model["a"] + model["b"] * ensemble_mean
    sigma = math.sqrt(max(0.01, model["c"] + model["d"] * ensemble_std * ensemble_std))
    return {"mu": mu, "sigma": sigma}


def compute_bracket_prob(mu, sigma, low, high):
    """P(low <= X <= high) for X ~ N(mu, sigma)."""
    if sigma <= 0:
        return 1.0 if low <= mu <= high else 0.0
    p_low = _norm_cdf((low - mu) / sigma)
    p_high = _norm_cdf((high - mu) / sigma)
    return max(0.0, min(1.0, p_high - p_low))
