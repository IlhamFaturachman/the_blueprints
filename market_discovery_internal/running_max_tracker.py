# market_discovery_internal/running_max_tracker.py
"""Running-max METAR tracker for observation lag study.

Tracks running daily max from METAR observations since local midnight.
METAR reports current temp per hour, not daily high — we accumulate all
observations to compute running max. The existing fetch_noaa_metar() only
reads data[0] (latest), which is insufficient.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from market_discovery_internal.config import NOAA_METAR_API

logger = logging.getLogger(__name__)


def fetch_metar_24h(icao):
    """Fetch all METAR observations for the last 24 hours. Returns list of {temp, reportTime}."""
    if not icao:
        return []
    try:
        resp = requests.get(NOAA_METAR_API, params={"ids": icao, "format": "json", "hours": 24}, timeout=10)
        if not resp or resp.status_code != 200:
            return []
        data = resp.json()
        if not data or not isinstance(data, list):
            return []
        observations = []
        for obs in data:
            temp_c = obs.get("temp")
            if temp_c is None:
                raw = obs.get("rawOb", "")
                m = re.search(r'\b(M?\d{2})/(M?\d{2})\b', raw)
                if m:
                    t = m.group(1)
                    temp_c = -int(t[1:]) if t.startswith('M') else int(t)
            if temp_c is not None:
                observations.append({"temp": float(temp_c), "reportTime": obs.get("reportTime") or obs.get("obsTime")})
        return observations
    except Exception as e:
        logger.warning("[METAR-24H] fetch failed for %s: %s", icao, e)
        return []


class RunningMaxTracker:
    """Tracks running daily max from METAR. Resets at local midnight."""

    def __init__(self, icao, local_tz="America/Chicago"):
        self.icao = icao
        self.local_tz = local_tz
        self.running_max = -999.0
        self.second_max = -999.0
        self.running_max_hour_local = -1
        self.current_local_date = None

    def _local_hour(self, utc_dt):
        try:
            from zoneinfo import ZoneInfo
            return utc_dt.astimezone(ZoneInfo(self.local_tz)).hour
        except Exception:
            return (utc_dt.hour - 6) % 24

    def _local_date(self, utc_dt):
        try:
            from zoneinfo import ZoneInfo
            return utc_dt.astimezone(ZoneInfo(self.local_tz)).strftime("%Y-%m-%d")
        except Exception:
            return (utc_dt - timedelta(hours=6)).strftime("%Y-%m-%d")

    def update(self, temp_c, observation_time_utc):
        local_date = self._local_date(observation_time_utc)
        if self.current_local_date is None:
            self.current_local_date = local_date
        elif local_date != self.current_local_date:
            self.running_max = -999.0
            self.second_max = -999.0
            self.running_max_hour_local = -1
            self.current_local_date = local_date
        if temp_c > self.running_max:
            self.second_max = self.running_max
            self.running_max = temp_c
            self.running_max_hour_local = self._local_hour(observation_time_utc)
        elif temp_c > self.second_max:
            self.second_max = temp_c

    @property
    def margin(self):
        if self.second_max <= -900 or self.running_max <= -900:
            return 0.0
        return round(self.running_max - self.second_max, 2)

    def refresh_from_metar(self):
        """Fetch all METAR observations and rebuild running max."""
        observations = fetch_metar_24h(self.icao)
        observations.sort(key=lambda o: o.get("reportTime", ""))
        for obs in observations:
            temp = obs.get("temp")
            if temp is None:
                continue
            time_str = obs.get("reportTime")
            try:
                if isinstance(time_str, (int, float)):
                    dt = datetime.fromtimestamp(float(time_str), tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(time_str).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            self.update(float(temp), dt)
        logger.info("[RUNNING-MAX] %s: max=%.1f second=%.1f margin=%.1f hour=%d",
                    self.icao, self.running_max, self.second_max, self.margin, self.running_max_hour_local)


def determine_winning_bracket(running_max_c, brackets):
    """Given running max temp and bracket list, find the winning bracket."""
    for b in brackets:
        low = b.get("threshold")
        high = b.get("threshold_high")
        if low is None:
            continue
        if high is not None:
            if low <= running_max_c < high:
                return b
        else:
            if abs(running_max_c - low) <= 0.5:
                return b
    return None
