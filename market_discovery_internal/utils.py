"""Common utilities for market_discovery."""

import logging
import time
import json
import os
import string
from datetime import datetime, timezone
import requests

from market_discovery_internal.config import LOG_FILE, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_PROXY

logger = logging.getLogger(__name__)

class SafeFormatter(string.Formatter):
    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            return kwargs.get(key, f"[{key} N/A]")
        return string.Formatter.get_value(self, key, args, kwargs)

def load_telegram_template(category: str, type_name: str, **kwargs) -> str:
    """
    Loads an HTML template and injects variables safely.
    Uses absolute pathing relative to project root.
    Dynamic values are HTML-escaped so < > & chars don't break Telegram HTML parsing.
    """
    import html as _html
    # HTML-escape all string kwargs so special chars in dynamic values are safe
    safe_kwargs = {
        k: _html.escape(str(v)) if isinstance(v, str) else v
        for k, v in kwargs.items()
    }
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_path = os.path.join(base_dir, "telegram_msg", category, f"{type_name}.html")

    if not os.path.exists(template_path):
        return f"⚠️ Template not found: {template_path}"

    try:
        with open(template_path, "r") as f:
            template_content = f.read()
            return SafeFormatter().format(template_content, **safe_kwargs)
    except Exception as e:
        return f"❌ Error rendering template {category}/{type_name}: {str(e)}"

_telegram_consecutive_failures = 0

def send_telegram_alert(message, is_html=True):
    """[MODUL N] Send push notification via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    global _telegram_consecutive_failures
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": str(TELEGRAM_CHAT_ID),
        "text": message,
        "disable_web_page_preview": True
    }
    if is_html:
        payload["parse_mode"] = "HTML"

    # Add proxy support (for VPS that can't reach api.telegram.org directly)
    proxies = None
    if TELEGRAM_PROXY:
        proxies = {
            "http": TELEGRAM_PROXY,
            "https": TELEGRAM_PROXY,
        }

    # Use longer timeout when proxy is configured (proxy adds latency)
    timeout = 10 if TELEGRAM_PROXY else 5

    try:
        response = requests.post(url, json=payload, timeout=timeout, proxies=proxies)
        response.raise_for_status()
        _telegram_consecutive_failures = 0
        return True
    except Exception as e:
        _telegram_consecutive_failures += 1
        if _telegram_consecutive_failures >= 10:
            logger.error(f"[TELEGRAM ERROR] {_telegram_consecutive_failures} consecutive failures. Latest: {e}")
        else:
            logger.warning(f"[TELEGRAM ERROR] {str(e)}")
        if 'response' in locals() and response is not None:
             logger.warning(f"[TELEGRAM RESP] {response.text}")
        return False

def fetch_with_retry(url, params=None, headers=None, max_retries=3, fail_fast_on_429=False, timeout=10):
    """
    GET a URL and return parsed JSON. Retries up to max_retries times
    with exponential backoff (1s, 2s, 4s) on any request error.
    
    [MODUL U] If fail_fast_on_429=True, it will not retry on 429 errors 
    at all, raising the error immediately to prevent cycle blocking.
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code == 429:
                if fail_fast_on_429:
                    raise requests.HTTPError(f"429 Client Error: Too Many Requests (Fail-Fast) for url: {url}", response=response)
                
                # Default backoff behavior (for non-weather or aggregated calls)
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
