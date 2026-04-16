"""Centralized runtime config/constants for market_discovery."""

import os
import re

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name, default=False):
    """Parse boolean environment variables using common truthy values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


GAMMA_API = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS_API = "https://gamma-api.polymarket.com/events/pagination"
CLOB_BOOK_API = os.getenv("CLOB_BOOK_API", "https://clob.polymarket.com/book")
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
LOG_FILE = "logs/unmatched_markets.log"

# Target cities with hardcoded coordinates (lat, lon)
TARGET_CITIES = {
    "new york city": {"lat": 40.7128,  "lon": -74.0060},
    "chicago":       {"lat": 41.8781,  "lon": -87.6298},
    "london":        {"lat": 51.5074,  "lon":  -0.1278},
    "hong kong":     {"lat": 22.3193,  "lon": 114.1694},
    "miami":         {"lat": 25.7617,  "lon": -80.1918},
    "toronto":       {"lat": 43.6532,  "lon": -79.3832},
    "paris":         {"lat": 48.8566,  "lon":   2.3522},
    "seoul":         {"lat": 37.5665,  "lon": 126.9780},
    "singapore":     {"lat":  1.3521,  "lon": 103.8198},
    "shanghai":      {"lat": 31.2304,  "lon": 121.4737},
    "beijing":       {"lat": 39.9042,  "lon": 116.4074},
    "los angeles":   {"lat": 34.0522,  "lon": -118.2437},
    "houston":       {"lat": 29.7604,  "lon": -95.3698},
    "dallas":        {"lat": 32.7767,  "lon": -96.7970},
    "denver":        {"lat": 39.7392,  "lon": -104.9903},
    "atlanta":       {"lat": 33.7490,  "lon": -84.3880},
    "seattle":       {"lat": 47.6062,  "lon": -122.3321},
    "austin":        {"lat": 30.2672,  "lon": -97.7431},
    "madrid":        {"lat": 40.4168,  "lon":  -3.7038},
    "milan":         {"lat": 45.4642,  "lon":   9.1900},
    "tel aviv":      {"lat": 32.0853,  "lon":  34.7818},
    "warsaw":        {"lat": 52.2297,  "lon":  21.0122},
    "ankara":        {"lat": 39.9334,  "lon":  32.8597},
    "taipei":        {"lat": 25.0330,  "lon": 121.5654},
    "sao paulo":     {"lat": -23.5505, "lon": -46.6333},
    "buenos aires":  {"lat": -34.6037, "lon": -58.3816},
    "chengdu":       {"lat": 30.5728,  "lon": 104.0668},
    "chongqing":     {"lat": 29.4316,  "lon": 106.9123},
    "shenzhen":      {"lat": 22.5431,  "lon": 114.0579},
    "wuhan":         {"lat": 30.5928,  "lon": 114.3055},
    "lucknow":       {"lat": 26.8467,  "lon":  80.9462},
}

# Regex patterns for matching city names (handles abbreviations/variants)
CITY_PATTERNS = {
    "new york city": r"\bnew york(?:\s+city)?\b|\bnyc\b",
    "chicago":       r"\bchicago\b",
    "london":        r"\blondon\b",
    "hong kong":     r"\bhong kong\b|\bhk\b|\bhkg\b",
    "miami":         r"\bmiami\b",
    "toronto":       r"\btoronto\b",
    "paris":         r"\bparis\b",
    "seoul":         r"\bseoul\b",
    "singapore":     r"\bsingapore\b",
    "shanghai":      r"\bshanghai\b",
    "beijing":       r"\bbeijing\b",
    "los angeles":   r"\blos angeles\b|\bla\b|\blax\b",
    "houston":       r"\bhouston\b",
    "dallas":        r"\bdallas\b",
    "denver":        r"\bdenver\b",
    "atlanta":       r"\batlanta\b",
    "seattle":       r"\bseattle\b",
    "austin":        r"\baustin\b",
    "madrid":        r"\bmadrid\b",
    "milan":         r"\bmilan\b",
    "tel aviv":      r"\btel aviv\b",
    "warsaw":        r"\bwarsaw\b",
    "ankara":        r"\bankara\b",
    "taipei":        r"\btaipei\b",
    "sao paulo":     r"\bsao paulo\b",
    "buenos aires":  r"\bbuenos aires\b",
    "chengdu":       r"\bchengdu\b",
    "chongqing":     r"\bchongqing\b",
    "shenzhen":      r"\bshenzhen\b",
    "wuhan":         r"\bwuhan\b",
    "lucknow":       r"\blucknow\b",
}

# Matches: "75F", "80F", "25C", "30 C", "75 degrees F"
THRESHOLD_PATTERN = r"(\d+(?:\.\d+)?)\s*(?:degrees?\s*)?(?:°\s*)?([FC])\b"

# Direction hints
EXACT_KEYWORDS = r"\b(exact(?:ly)?|equal(?:s| to)?|at exactly|precisely|on the dot)\b"
ABOVE_KEYWORDS = r"\bor higher\b|\bor above\b|\bor more\b|\bat least\b|\b(above|over|exceed|reach|hit)\b"
BELOW_KEYWORDS = r"\bor lower\b|\bor below\b|\bor less\b|\bat most\b|\b(below|under|less than|cooler than|drop below|stay under)\b"
WEATHER_CONTEXT_PATTERN = r"\b(weather|forecast|temperature|temp|degrees?|rain|snow|humidity|hot|cold|heat|chill|high|low)\b"
DIRECTION_CANDIDATE_PATTERN = r"\b(above|below|under|over|exceed|reach|hit|drop|stay|exact(?:ly)?|equal(?:s| to)?)\b"

# Precompiled regex keeps hot-path parsing/candidate checks faster and cleaner.
THRESHOLD_RE = re.compile(THRESHOLD_PATTERN, re.IGNORECASE)
EXACT_RE = re.compile(EXACT_KEYWORDS, re.IGNORECASE)
ABOVE_RE = re.compile(ABOVE_KEYWORDS, re.IGNORECASE)
BELOW_RE = re.compile(BELOW_KEYWORDS, re.IGNORECASE)
WEATHER_CONTEXT_RE = re.compile(WEATHER_CONTEXT_PATTERN, re.IGNORECASE)
DIRECTION_CANDIDATE_RE = re.compile(DIRECTION_CANDIDATE_PATTERN, re.IGNORECASE)
CITY_REGEXES = [
    (city, re.compile(pattern, re.IGNORECASE))
    for city, pattern in CITY_PATTERNS.items()
]

# Paper-trading strategy defaults (can be overridden via environment variables)
PAPER_STAKE_USD = float(os.getenv("PAPER_STAKE_USD", "100"))
HYBRID_TAKE_PROFIT_MULTIPLIER = float(os.getenv("HYBRID_TAKE_PROFIT_MULTIPLIER", "2.0"))
HYBRID_TAKE_PROFIT_MIN_PRICE = float(os.getenv("HYBRID_TAKE_PROFIT_MIN_PRICE", "0.50"))
HYBRID_TAKE_PROFIT_MAX_PRICE = float(os.getenv("HYBRID_TAKE_PROFIT_MAX_PRICE", "0.60"))
HYBRID_STOP_LOSS_MULTIPLIER = float(os.getenv("HYBRID_STOP_LOSS_MULTIPLIER", "0.48"))
HYBRID_LATE_WINDOW_HOURS = float(os.getenv("HYBRID_LATE_WINDOW_HOURS", "2.0"))
HYBRID_MIN_CONFIDENCE_TO_HOLD = float(os.getenv("HYBRID_MIN_CONFIDENCE_TO_HOLD", "0.75"))
HYBRID_CONFIDENCE_EDGE_SCALE = float(os.getenv("HYBRID_CONFIDENCE_EDGE_SCALE", "0.35"))
PAPER_STATE_FILE = os.getenv("PAPER_STATE_FILE", "logs/paper_positions.json")
PAPER_MAX_OPEN_POSITIONS = int(os.getenv("PAPER_MAX_OPEN_POSITIONS", "3"))
PAPER_MAX_OPEN_PER_CITY = max(1, int(os.getenv("PAPER_MAX_OPEN_PER_CITY", "1")))
PAPER_BASE_WALLET = max(
    0.01,
    float(
        os.getenv(
            "PAPER_BASE_WALLET",
            str(PAPER_STAKE_USD * PAPER_MAX_OPEN_POSITIONS),
        )
    ),
)
PAPER_MIN_CITY_DIVERSITY = max(1, int(os.getenv("PAPER_MIN_CITY_DIVERSITY", "5")))
PAPER_ENTRY_MIN_PRICE = float(os.getenv("PAPER_ENTRY_MIN_PRICE", "0.10"))
PAPER_ENTRY_MAX_PRICE = float(os.getenv("PAPER_ENTRY_MAX_PRICE", "0.65"))

# Exact-bracket market liquidity gate (uses Gamma bestAsk/spread fields)
MARKET_MAX_SPREAD_GATE = float(os.getenv("MARKET_MAX_SPREAD_GATE", "0.12"))
MARKET_MIN_VOLUME_24HR = float(os.getenv("MARKET_MIN_VOLUME_24HR", "500"))

# Probability model for exact-bracket markets (Gaussian around forecast)
MODEL_EXACT_SIGMA_C = float(os.getenv("MODEL_EXACT_SIGMA_C", "1.5"))
STRATEGY_EXACT_MIN_EDGE = float(os.getenv("STRATEGY_EXACT_MIN_EDGE", "0.02"))
STRATEGY_EXACT_MIN_MODEL_PROB = float(os.getenv("STRATEGY_EXACT_MIN_MODEL_PROB", "0.10"))
PAPER_LOOP_INTERVAL_SECONDS = int(os.getenv("PAPER_LOOP_INTERVAL_SECONDS", "300"))
PAPER_LOOP_CONTINUE_ON_ERROR = _env_bool("PAPER_LOOP_CONTINUE_ON_ERROR", True)
PAPER_LOOP_ERROR_BACKOFF_SECONDS = max(
    1,
    int(os.getenv("PAPER_LOOP_ERROR_BACKOFF_SECONDS", "30")),
)
PAPER_LOOP_MAX_ERROR_BACKOFF_SECONDS = max(
    PAPER_LOOP_ERROR_BACKOFF_SECONDS,
    int(
        os.getenv(
            "PAPER_LOOP_MAX_ERROR_BACKOFF_SECONDS",
            str(max(PAPER_LOOP_INTERVAL_SECONDS, 300)),
        )
    ),
)
PAPER_JOURNAL_MAX_ENTRIES = int(os.getenv("PAPER_JOURNAL_MAX_ENTRIES", "300"))
PAPER_RUNTIME_ERROR_LOG = os.getenv("PAPER_RUNTIME_ERROR_LOG", "logs/paper_runtime_errors.log")
PAPER_REPORT_RETENTION_WARN_THRESHOLD = int(os.getenv("PAPER_REPORT_RETENTION_WARN_THRESHOLD", "1000"))
PAPER_REPORT_ANOMALY_STREAK_ALERT = max(1, int(os.getenv("PAPER_REPORT_ANOMALY_STREAK_ALERT", "3")))
PAPER_REPORT_REJECT_DOMINANT_RATIO = min(
    1.0,
    max(0.0, float(os.getenv("PAPER_REPORT_REJECT_DOMINANT_RATIO", "0.85"))),
)

# Discovery + entry strategy (runtime-tunable)
DISCOVERY_MAX_FETCH_PAGES = int(os.getenv("DISCOVERY_MAX_FETCH_PAGES", "2"))
DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN = _env_bool("DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN", True)
DISCOVERY_AGGRESSIVE_SCAN_PAGES = int(os.getenv("DISCOVERY_AGGRESSIVE_SCAN_PAGES", "3"))
DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES = int(
    os.getenv("DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES", "3")
)
DAILY_RESOLVE_ONLY = _env_bool("DAILY_RESOLVE_ONLY", False)
DAILY_MIN_HOURS_TO_RESOLVE = float(os.getenv("DAILY_MIN_HOURS_TO_RESOLVE", "0"))
DISCOVERY_FORECAST_PREFETCH_MIN_KEYS = max(
    0,
    int(os.getenv("DISCOVERY_FORECAST_PREFETCH_MIN_KEYS", "4")),
)
DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS = max(
    1,
    int(os.getenv("DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS", "4")),
)
PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS = max(
    0,
    int(os.getenv("PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS", "3")),
)
PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS = max(
    1,
    int(os.getenv("PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS", "4")),
)
STRATEGY_MAX_YES_PRICE = float(os.getenv("STRATEGY_MAX_YES_PRICE", "0.35"))
STRATEGY_MIN_MODEL_PROB = float(os.getenv("STRATEGY_MIN_MODEL_PROB", "0.70"))
STRATEGY_MIN_EDGE = float(os.getenv("STRATEGY_MIN_EDGE", "0.35"))
DAILY_TARGET_MULTIPLIER = max(1.0, float(os.getenv("DAILY_TARGET_MULTIPLIER", "2.0")))

# History-driven self-learning tuner (pre-AI adaptive layer)
AUTO_TUNER_ENABLED = _env_bool("AUTO_TUNER_ENABLED", True)
AUTO_TUNER_MIN_TRADES = max(1, int(os.getenv("AUTO_TUNER_MIN_TRADES", "3")))
AUTO_TUNER_EDGE_RELAX = max(0.0, float(os.getenv("AUTO_TUNER_EDGE_RELAX", "0.05")))
AUTO_TUNER_EDGE_PENALTY = max(0.0, float(os.getenv("AUTO_TUNER_EDGE_PENALTY", "0.15")))
AUTO_TUNER_AGGRESSIVE_WIN_RATE = min(
    1.0,
    max(0.0, float(os.getenv("AUTO_TUNER_AGGRESSIVE_WIN_RATE", "0.60"))),
)
AUTO_TUNER_CAUTION_WIN_RATE = min(
    1.0,
    max(0.0, float(os.getenv("AUTO_TUNER_CAUTION_WIN_RATE", "0.45"))),
)
AUTO_TUNER_BLACKLIST_PNL = float(os.getenv("AUTO_TUNER_BLACKLIST_PNL", "-1.50"))
AUTO_TUNER_BLACKLIST_STOP_LOSS_RATE = min(
    1.0,
    max(0.0, float(os.getenv("AUTO_TUNER_BLACKLIST_STOP_LOSS_RATE", "0.75"))),
)

# Entry bucket engine (deterministic-first, AI-ready)
ENTRY_BUCKET_HOLD_MIN_PROB = float(os.getenv("ENTRY_BUCKET_HOLD_MIN_PROB", "0.90"))
ENTRY_BUCKET_HOLD_MIN_EDGE = float(os.getenv("ENTRY_BUCKET_HOLD_MIN_EDGE", "0.60"))
ENTRY_BUCKET_HOLD_MIN_CONFIDENCE = float(
    os.getenv("ENTRY_BUCKET_HOLD_MIN_CONFIDENCE", str(HYBRID_MIN_CONFIDENCE_TO_HOLD))
)
ENTRY_BUCKET_WATCH_MAX_PRICE = float(os.getenv("ENTRY_BUCKET_WATCH_MAX_PRICE", "0.40"))

# AI integration placeholders (still optional and fully guardrailed)
AI_AGENT_ENABLED = _env_bool("AI_AGENT_ENABLED", False)
AI_AGENT_MIN_OVERRIDE_CONFIDENCE = float(os.getenv("AI_AGENT_MIN_OVERRIDE_CONFIDENCE", "0.80"))
AI_AGENT_TIMEOUT_SECONDS = float(os.getenv("AI_AGENT_TIMEOUT_SECONDS", "5.0"))

# Anthropic API integration (entry gate + monitor)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_ENABLE_WEB_SEARCH = _env_bool("AI_ENABLE_WEB_SEARCH", True)

# Hard budget and usage controls (keeps monthly cost <= configured budget)
AI_MONTHLY_BUDGET_USD = max(0.0, float(os.getenv("AI_MONTHLY_BUDGET_USD", "5.0")))
AI_USAGE_LEDGER_FILE = os.getenv("AI_USAGE_LEDGER_FILE", "logs/ai_usage_ledger.json")

# Sonnet entry gate
SONNET_ENTRY_ENABLED = _env_bool("SONNET_ENTRY_ENABLED", True)
SONNET_ENTRY_MIN_CONFIDENCE = float(os.getenv("SONNET_ENTRY_MIN_CONFIDENCE", "0.80"))
SONNET_ENTRY_MODEL = os.getenv("SONNET_ENTRY_MODEL", "claude-sonnet-4-6")
SONNET_ENTRY_MAX_TOKENS = max(64, int(os.getenv("SONNET_ENTRY_MAX_TOKENS", "500")))
SONNET_ENTRY_MAX_CALLS_PER_DAY = max(0, int(os.getenv("SONNET_ENTRY_MAX_CALLS_PER_DAY", "6")))
SONNET_ENTRY_CACHE_FILE = os.getenv("SONNET_ENTRY_CACHE_FILE", "logs/sonnet_entry_cache.json")
SONNET_ENTRY_CACHE_TTL_HOURS = max(1.0, float(os.getenv("SONNET_ENTRY_CACHE_TTL_HOURS", "6")))

# Haiku monitor gate for open positions
HAIKU_MONITOR_ENABLED = _env_bool("HAIKU_MONITOR_ENABLED", True)
HAIKU_MONITOR_INTERVAL_HOURS = max(1.0, float(os.getenv("HAIKU_MONITOR_INTERVAL_HOURS", "6.0")))
HAIKU_MONITOR_MODEL = os.getenv("HAIKU_MONITOR_MODEL", "claude-haiku-4-5-20251001")
HAIKU_MONITOR_MAX_TOKENS = max(64, int(os.getenv("HAIKU_MONITOR_MAX_TOKENS", "220")))
HAIKU_MONITOR_MAX_CALLS_PER_DAY = max(0, int(os.getenv("HAIKU_MONITOR_MAX_CALLS_PER_DAY", "24")))
HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT = min(
    1.0,
    max(0.0, float(os.getenv("HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT", "0.75"))),
)
HAIKU_MONITOR_CACHE_FILE = os.getenv("HAIKU_MONITOR_CACHE_FILE", "logs/haiku_monitor_cache.json")

# Weather evidence quality controls (for hold-to-resolve safety)
WEATHER_EVIDENCE_MAX_AGE_HOURS = float(os.getenv("WEATHER_EVIDENCE_MAX_AGE_HOURS", "3.0"))
WEATHER_EVIDENCE_MIN_QUALITY_SCORE = float(os.getenv("WEATHER_EVIDENCE_MIN_QUALITY_SCORE", "0.65"))

# WebSocket real-time position monitor
WS_PRICE_WATCHER_ENABLED = _env_bool("WS_PRICE_WATCHER_ENABLED", True)
WS_PRICE_WATCHER_URL = os.getenv(
    "WS_PRICE_WATCHER_URL",
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
)
WS_RECONNECT_DELAY_SECONDS = max(1, int(os.getenv("WS_RECONNECT_DELAY_SECONDS", "5")))
WS_PING_INTERVAL_SECONDS = max(5, int(os.getenv("WS_PING_INTERVAL_SECONDS", "10")))

WS_BROADCAST_HOST = os.getenv("WS_BROADCAST_HOST", "127.0.0.1")
WS_BROADCAST_PORT = max(1, int(os.getenv("WS_BROADCAST_PORT", "8081")))
WS_BROADCAST_MAX_CLIENTS = max(1, int(os.getenv("WS_BROADCAST_MAX_CLIENTS", "10")))
WS_BROADCAST_PING_INTERVAL_SECONDS = max(
    5,
    int(os.getenv("WS_BROADCAST_PING_INTERVAL_SECONDS", "20")),
)
