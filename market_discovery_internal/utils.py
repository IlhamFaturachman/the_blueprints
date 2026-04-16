"""Common utilities for market_discovery."""

import time
import json
import os
from datetime import datetime, timezone
import requests

from market_discovery_internal.config import LOG_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

def send_telegram_alert(message):
    """[MODUL N] Send push notification via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
        return True
    except Exception:
        return False

def fetch_with_retry(url, params=None, headers=None, max_retries=3):
    """
    GET a URL and return parsed JSON. Retries up to max_retries times
    with exponential backoff (1s, 2s, 4s) on any request error.
    Raises the last exception if all retries are exhausted.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise last_error

def _safe_float(value, default=0.0):
    """Safely convert a value to float, returning default on failure."""
    try:
        if value is None:
            return default
        return float(value)
    except (ValueError, TypeError):
        return default

def _safe_div(numerator, denominator, default=0.0):
    """Safely divide two numbers, returning default on zero division."""
    try:
        n = float(numerator)
        d = float(denominator)
        if d == 0:
            return default
        return n / d
    except (ValueError, TypeError):
        return default

def _clamp(value, low=0.0, high=1.0):
    """Clamp a value between a lower and upper bound."""
    return max(low, min(high, value))

def _parse_iso_utc(value):
    """Parse an ISO 8601 string into a UTC datetime object."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

def _load_json_blob(path, default):
    """Load a JSON file, returning default if missing or invalid."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default

def _save_json_blob(path, payload):
    """Atomic-ish save of a JSON payload to a file."""
    temp_path = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)
        os.replace(temp_path, path)
    except OSError:
        if os.path.exists(temp_path):
            os.remove(temp_path)
