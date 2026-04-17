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
    payload = {
        "chat_id": str(TELEGRAM_CHAT_ID),
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
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
            if response.status_code == 429:
                # [MODUL U] Smart Backoff: Archive API is extremely strict.
                # First 429: 30s, Second: 60s, Third: 120s
                backoff = 30 * (2 ** attempt)
                print(f"[RETRY] Rate limit hit (429). Cooling down for {backoff}s before retry {attempt+1}/{max_retries}...")
                last_error = requests.HTTPError(f"429 Client Error: Too Many Requests for url: {url}", response=response)
                time.sleep(backoff)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as e:
            last_error = e
            if attempt < max_retries - 1:
                # Normal exponential backoff for other errors (timeout, 5xx, etc)
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
    if last_error:
        raise last_error
    return None

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
    """Atomic save of a JSON payload to a file (fsync before replace)."""
    temp_path = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except OSError:
        if os.path.exists(temp_path):
            os.remove(temp_path)
