# market_discovery_internal/lock_probability.py
"""Lock probability model: P(running_max = final_daily_max | hour, margin, city).

After the diurnal peak (~15:00 local), running daily max becomes increasingly
likely to be final. This is NOT certain arbitrage — it's a high-probability bet
(~85-98%). The remaining risk: temp could still rise, changing the winning bracket.
"""
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = {"center_hour": 16.0, "steepness": 0.8, "margin_coefficient": 0.15}
_CITY_MODELS = {}


def compute_lock_probability(hour_local, margin_c, city, model=None):
    """P(running_max = final_daily_max) given hour and margin."""
    m = model or _CITY_MODELS.get(city, _DEFAULT_MODEL)
    logit = m["steepness"] * (hour_local - m["center_hour"]) + m["margin_coefficient"] * margin_c
    if hour_local < 12:
        return max(0.0, min(0.05, 1.0 / (1.0 + math.exp(-logit)) * 0.1))
    prob = 1.0 / (1.0 + math.exp(-logit))
    return max(0.0, min(1.0, prob))


def fit_lock_model(history, city):
    """Fit lock probability from historical daily temperature curves."""
    data_points = []
    for day in history:
        temps = day["temps_by_hour"]
        final_max = day["final_max"]
        running_max = -999.0
        second_max = -999.0
        for h in range(24):
            t = temps.get(h, 0.0)
            if t > running_max:
                second_max = running_max
                running_max = t
            elif t > second_max:
                second_max = t
            margin = running_max - second_max if second_max > -900 else 0.0
            was_final = 1.0 if running_max >= final_max - 0.01 else 0.0
            data_points.append((float(h), margin, was_final))

    if len(data_points) < 50:
        _CITY_MODELS[city] = dict(_DEFAULT_MODEL)
        return _CITY_MODELS[city]

    best_ll = -1e9
    best = dict(_DEFAULT_MODEL)
    for center in [13.0, 14.0, 15.0, 16.0, 17.0]:
        for steep in [0.5, 0.8, 1.0, 1.2, 1.5]:
            for mcoef in [0.05, 0.10, 0.15, 0.20, 0.30]:
                ll = 0.0
                for hour, margin, wf in data_points:
                    logit = steep * (hour - center) + mcoef * margin
                    p = max(1e-6, min(1.0 - 1e-6, 1.0 / (1.0 + math.exp(-logit))))
                    ll += math.log(p) if wf > 0.5 else math.log(1.0 - p)
                if ll > best_ll:
                    best_ll = ll
                    best = {"center_hour": center, "steepness": steep, "margin_coefficient": mcoef}
    _CITY_MODELS[city] = best
    logger.info("[LOCK] Fitted %s: center=%.1f steep=%.2f margin=%.2f", city, best["center_hour"], best["steepness"], best["margin_coefficient"])
    return best


def get_lock_model(city):
    return _CITY_MODELS.get(city, _DEFAULT_MODEL)
