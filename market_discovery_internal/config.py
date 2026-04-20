"""Centralized runtime config/constants for market_discovery."""

import os
import re

from dotenv import load_dotenv

load_dotenv()

# API URLs
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HISTORICAL_API = "https://archive-api.open-meteo.com/v1/archive"

def _env_bool(name, default=False):
    """Parse boolean environment variables using common truthy values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


GAMMA_API = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS_API = "https://gamma-api.polymarket.com/events/pagination"
CLOB_BOOK_API = os.getenv("CLOB_BOOK_API", "https://clob.polymarket.com/book")
POLYMARKET_CLOB_API_URL = "https://clob.polymarket.com"
CURRENT_REGIME_CONFIG = {
    "normal": {
        "max_slippage": 0.05,
        "min_liquidity_usd": 100
    },
    "aggressive": {
        "max_slippage": 0.10,
        "min_liquidity_usd": 50
    },
    "defensive": {
        "max_slippage": 0.02,
        "min_liquidity_usd": 500
    }
}
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_API = OPEN_METEO_FORECAST_URL

# Model & Liquidity Hardening
# MODEL_EXACT_SIGMA_C defined below at runtime (env-overridable, default 1.5)
LOG_FILE = "logs/unmatched_markets.log"

# Target cities with hardcoded coordinates (lat, lon) and ICAO codes for API matching.
# We focus NOAA validation on US airports. International fallback is Open-Meteo.
TARGET_CITIES = {
    # Coordinates are exact ICAO airport station locations, not city centers.
    # Polymarket resolves temperature markets using Wunderground data from these airport stations.
    # Forecasting at airport coords eliminates systematic 1-3°C bias from city-center grid forecasts.
    "new york city": {"lat": 40.6413,  "lon":  -73.7781, "icao": "KJFK", "tz": "America/New_York"},
    "chicago":       {"lat": 41.9742,  "lon":  -87.9073, "icao": "KORD", "tz": "America/Chicago"},
    "london":        {"lat": 51.4775,  "lon":   -0.4614, "icao": "EGLL", "tz": "Europe/London"},
    "hong kong":     {"lat": 22.3080,  "lon":  113.9185, "icao": "VHHH", "tz": "Asia/Hong_Kong"},
    "miami":         {"lat": 25.7959,  "lon":  -80.2870, "icao": "KMIA", "tz": "America/New_York"},
    "toronto":       {"lat": 43.6777,  "lon":  -79.6248, "icao": "CYYZ", "tz": "America/Toronto"},
    "paris":         {"lat": 49.0097,  "lon":    2.5479, "icao": "LFPG", "tz": "Europe/Paris"},
    "seoul":         {"lat": 37.4602,  "lon":  126.4407, "icao": "RKSI", "tz": "Asia/Seoul"},
    "singapore":     {"lat":  1.3644,  "lon":  103.9915, "icao": "WSSS", "tz": "Asia/Singapore"},
    "shanghai":      {"lat": 31.1443,  "lon":  121.8083, "icao": "ZSPD", "tz": "Asia/Shanghai"},
    "beijing":       {"lat": 40.0801,  "lon":  116.5846, "icao": "ZBAA", "tz": "Asia/Shanghai"},
    "los angeles":   {"lat": 33.9425,  "lon": -118.4081, "icao": "KLAX", "tz": "America/Los_Angeles"},
    "houston":       {"lat": 29.9902,  "lon":  -95.3368, "icao": "KIAH", "tz": "America/Chicago"},
    "dallas":        {"lat": 32.8998,  "lon":  -97.0403, "icao": "KDFW", "tz": "America/Chicago"},
    "denver":        {"lat": 39.8561,  "lon": -104.6737, "icao": "KDEN", "tz": "America/Denver"},
    "atlanta":       {"lat": 33.6407,  "lon":  -84.4277, "icao": "KATL", "tz": "America/New_York"},
    "seattle":       {"lat": 47.4502,  "lon": -122.3088, "icao": "KSEA", "tz": "America/Los_Angeles"},
    "austin":        {"lat": 30.1975,  "lon":  -97.6664, "icao": "KAUS", "tz": "America/Chicago"},
    "madrid":        {"lat": 40.4936,  "lon":   -3.5668, "icao": "LEMD", "tz": "Europe/Madrid"},
    "milan":         {"lat": 45.6306,  "lon":    8.7231, "icao": "LIMC", "tz": "Europe/Rome"},
    "tel aviv":      {"lat": 31.9965,  "lon":   34.8854, "icao": "LLBG", "tz": "Asia/Jerusalem"},
    "warsaw":        {"lat": 52.1657,  "lon":   20.9671, "icao": "EPWA", "tz": "Europe/Warsaw"},
    "ankara":        {"lat": 40.1281,  "lon":   32.9951, "icao": "LTAC", "tz": "Europe/Istanbul"},
    "taipei":        {"lat": 25.0797,  "lon":  121.2342, "icao": "RCTP", "tz": "Asia/Taipei"},
    "sao paulo":     {"lat": -23.4356, "lon":  -46.4731, "icao": "SBGR", "tz": "America/Sao_Paulo"},
    "buenos aires":  {"lat": -34.8222, "lon":  -58.5358, "icao": "SAEZ", "tz": "America/Argentina/Buenos_Aires"},
    "chengdu":       {"lat": 30.5785,  "lon":  103.9471, "icao": "ZUUU", "tz": "Asia/Shanghai"},
    "chongqing":     {"lat": 29.7192,  "lon":  106.6423, "icao": "ZUCK", "tz": "Asia/Shanghai"},
    "shenzhen":      {"lat": 22.6392,  "lon":  113.8107, "icao": "ZGSZ", "tz": "Asia/Shanghai"},
    "wuhan":         {"lat": 30.7838,  "lon":  114.2081, "icao": "ZHHH", "tz": "Asia/Shanghai"},
    "lucknow":       {"lat": 26.7606,  "lon":   80.8893, "icao": "VILK", "tz": "Asia/Kolkata"},
}


# IATA to ICAO Mapping for high-accuracy weather sensing (Modul A)
# Used to convert 3-letter codes in Polymarket titles to 4-letter codes for NOAA/API.
AIRPORT_IATA_TO_ICAO = {
    "LHR": "EGLL", "LGW": "EGKK", "STN": "EGSS", "LCY": "EGLC", "LTN": "EGGW", # London
    "CDG": "LFPG", "ORY": "LFPO", "BVA": "LFOB", # Paris
    "FRA": "EDDF", "MUC": "EDDM", # Germany
    "HND": "RJTT", "NRT": "RJAA", # Tokyo
    "ICN": "RKSI", "GMP": "RKSS", # Seoul
    "SIN": "WSSS", # Singapore
    "HKG": "VHHH", # Hong Kong
    "MAD": "LEMD", "BCN": "LEBL", # Spain
    "MXP": "LIMC", "LIN": "LIML", "BGY": "LIME", # Milan
    "YYZ": "CYYZ", "YUL": "CYUL", "YVR": "CYVR", # Canada
    "SYD": "YSSY", "MEL": "YMML", # Australia
    "GRU": "SBGR", "GIG": "SBGL", # Brazil
    "EZE": "SAEZ", "AEP": "SABE", # Argentina
    "TLV": "LLBG", # Israel
    # US cities often use IATA as suffix to K (e.g. JFK -> KJFK), 
    # but we store them explicitly here if we want absolute safety.
    "JFK": "KJFK", "LGA": "KLGA", "EWR": "KEWR", "ORD": "KORD", "MDW": "KMDW",
    "LAX": "KLAX", "SFO": "KSFO", "MIA": "KMIA", "DFW": "KDFW", "IAH": "KIAH",
}

# Semantic Station Aliases (Modul A Extension)
# Maps keywords found in rules to specific precision ICAOs.
STATION_NAME_TO_ICAO = {
    "central park": "KNYC",
    "la guardia": "KLGA",
    "laguardia": "KLGA",
    "kennedy": "KJFK",
    "john f. kennedy": "KJFK",
    "newark": "KEWR",
    "o'hare": "KORD",
    "ohare": "KORD",
    "midway": "KMDW",
    "heathrow": "EGLL",
    "gatwick": "EGKK",
    "stansted": "EGSS",
    "london city": "EGLC",
    "luton": "EGGW",
    "pearson": "CYYZ",
    "toronto city": "CYTZ", # Alternate Toronto station
}

# City-to-Station Mapping for Ambiguity Detection
# If a city has >1 station in this list, the bot will REQUIRE an explicit match.
CITY_STATIONS = {
    "new york city": ["KJFK", "KLGA", "KNYC", "KEWR"],
    "chicago": ["KORD", "KMDW"],
    "london": ["EGLL", "EGKK", "EGSS", "EGLC", "EGGW"],
    "toronto": ["CYYZ", "CYTZ"],
    # Cities with single stations are NOT considered ambiguous.
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

# Matches: "75F", "80F", "25C", "30 C", "75 degrees F", or just "15" in a weather context
THRESHOLD_PATTERN = r"(\d+(?:\.\d+)?)\s*(?:degrees?\s*)?(?:°\s*)?([FC])?\b"

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
HYBRID_STOP_LOSS_COOLDOWN_HOURS = float(os.getenv("HYBRID_STOP_LOSS_COOLDOWN_HOURS", "2.0"))
HYBRID_CONFIDENCE_EDGE_SCALE = float(os.getenv("HYBRID_CONFIDENCE_EDGE_SCALE", "0.35"))
PAPER_STATE_FILE = os.getenv("PAPER_STATE_FILE", "logs/paper_positions_5usd.json")
PAPER_MAX_OPEN_POSITIONS = int(os.getenv("PAPER_MAX_OPEN_POSITIONS", "3"))
PAPER_MAX_OPEN_PER_CITY = max(1, int(os.getenv("PAPER_MAX_OPEN_PER_CITY", "1")))
WHIPLASH_COOLDOWN_HOURS = float(os.getenv("WHIPLASH_COOLDOWN_HOURS", "24.0"))
GOLDEN_WINDOW_HOURS_MAX = float(os.getenv("GOLDEN_WINDOW_HOURS_MAX", "24.0"))
GOLDEN_WINDOW_HOURS_MIN = float(os.getenv("GOLDEN_WINDOW_HOURS_MIN", "2.0"))
PAPER_BASE_WALLET = max(
    0.01,
    float(
        os.getenv(
            "PAPER_BASE_WALLET",
            "100.0",
        )
    ),
)
PAPER_MIN_CITY_DIVERSITY = max(1, int(os.getenv("PAPER_MIN_CITY_DIVERSITY", "5")))
PAPER_ENTRY_MIN_PRICE = float(os.getenv("PAPER_ENTRY_MIN_PRICE", "0.05"))
PAPER_ENTRY_MAX_PRICE = float(os.getenv("PAPER_ENTRY_MAX_PRICE", "0.65"))
MAX_ACCEPTABLE_SLIPPAGE = float(os.getenv("MAX_ACCEPTABLE_SLIPPAGE", "0.05"))
LIQUIDITY_DEPTH_MULTIPLIER = float(os.getenv("LIQUIDITY_DEPTH_MULTIPLIER", "1.2"))
MIN_STAKE_THRESHOLD = float(os.getenv("MIN_STAKE_THRESHOLD", "1.00"))  # Hard floor — skip dust stakes below USD 1.00

# Exact-bracket market liquidity gate (uses Gamma bestAsk/spread fields)
MARKET_MAX_SPREAD_GATE = float(os.getenv("MARKET_MAX_SPREAD_GATE", "0.12"))
PAPER_BYPASS_LIQUIDITY_CHECK = _env_bool("PAPER_BYPASS_LIQUIDITY_CHECK", False)
PAPER_STAKE_INCLUSIVE = _env_bool("PAPER_STAKE_INCLUSIVE", True)

# Polymarket taker fee rate per category (formula: C × rate × p × (1-p))
# Weather = 0.05, Sports = 0.03, Crypto = 0.072 (we trade Weather only)
POLYMARKET_TAKER_FEE_RATE = float(os.getenv("POLYMARKET_TAKER_FEE_RATE", "0.05"))
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

# [MODUL N] Mobile Alerts (Telegram)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PAPER_JOURNAL_MAX_ENTRIES = int(os.getenv("PAPER_JOURNAL_MAX_ENTRIES", "300"))
PAPER_RUNTIME_ERROR_LOG = os.getenv("PAPER_RUNTIME_ERROR_LOG", "logs/paper_runtime_errors.log")
PAPER_REPORT_RETENTION_WARN_THRESHOLD = int(os.getenv("PAPER_REPORT_RETENTION_WARN_THRESHOLD", "1000"))
PAPER_REPORT_ANOMALY_STREAK_ALERT = max(1, int(os.getenv("PAPER_REPORT_ANOMALY_STREAK_ALERT", "3")))
PAPER_REPORT_REJECT_DOMINANT_RATIO = min(
    1.0,
    max(0.0, float(os.getenv("PAPER_REPORT_REJECT_DOMINANT_RATIO", "0.85"))),
)

# Discovery + entry strategy (runtime-tunable)
DISCOVERY_MAX_FETCH_PAGES = int(os.getenv("DISCOVERY_MAX_FETCH_PAGES", "10"))
DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN = _env_bool("DISCOVERY_ENABLE_AUTO_AGGRESSIVE_SCAN", True)
DISCOVERY_AGGRESSIVE_SCAN_PAGES = int(os.getenv("DISCOVERY_AGGRESSIVE_SCAN_PAGES", "15"))
DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES = int(
    os.getenv("DISCOVERY_AUTO_AGGRESSIVE_AFTER_EMPTY_CYCLES", "3")
)
DAILY_RESOLVE_ONLY = _env_bool("DAILY_RESOLVE_ONLY", True)
DAILY_MIN_HOURS_TO_RESOLVE = float(os.getenv("DAILY_MIN_HOURS_TO_RESOLVE", "0"))
DAILY_MIN_RESOLVE_DAYS_AHEAD = int(os.getenv("DAILY_MIN_RESOLVE_DAYS_AHEAD", "1"))
DAILY_MAX_RESOLVE_DAYS_AHEAD = int(os.getenv("DAILY_MAX_RESOLVE_DAYS_AHEAD", "2"))
DISCOVERY_FORECAST_PREFETCH_MIN_KEYS = max(
    0,
    int(os.getenv("DISCOVERY_FORECAST_PREFETCH_MIN_KEYS", "4")),
)
DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS = max(
    1,
    int(os.getenv("DISCOVERY_FORECAST_PREFETCH_MAX_WORKERS", "3")),
)
PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS = max(
    0,
    int(os.getenv("PAPER_POSITION_FORECAST_PREFETCH_MIN_KEYS", "3")),
)
PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS = max(
    1,
    int(os.getenv("PAPER_POSITION_FORECAST_PREFETCH_MAX_WORKERS", "3")),
)
# Discovery gate: aligned with PAPER_ENTRY_MAX_PRICE so no valid candidate is
# discarded before it reaches the entry bucket stage.
STRATEGY_MAX_YES_PRICE = float(os.getenv("STRATEGY_MAX_YES_PRICE", "0.75"))
STRATEGY_MIN_MODEL_PROB = float(os.getenv("STRATEGY_MIN_MODEL_PROB", "0.51"))
STRATEGY_MIN_EDGE = float(os.getenv("STRATEGY_MIN_EDGE", "0.04"))

# [LIVE REQUISITES] Minimum order gates for Polymarket CLOB compliance
STRATEGY_MIN_STAKE_USD = float(os.getenv("STRATEGY_MIN_STAKE_USD", "1.00"))
STRATEGY_MIN_SHARES = int(os.getenv("STRATEGY_MIN_SHARES", "5"))

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
AI_ENABLE_WEB_SEARCH = _env_bool("AI_ENABLE_WEB_SEARCH", False)

# Hard budget and usage controls (keeps monthly cost <= configured budget)
AI_MONTHLY_BUDGET_USD = max(0.0, float(os.getenv("AI_MONTHLY_BUDGET_USD", "3.0")))
AI_USAGE_LEDGER_FILE = os.getenv("AI_USAGE_LEDGER_FILE", "logs/ai_usage_ledger.json")

# Haiku entry gate
HAIKU_ENTRY_ENABLED = _env_bool("HAIKU_ENTRY_ENABLED", False)
HAIKU_FAIL_OPEN = _env_bool("HAIKU_FAIL_OPEN", False)
HAIKU_ENTRY_MIN_CONFIDENCE = float(os.getenv("HAIKU_ENTRY_MIN_CONFIDENCE", "0.80"))
HAIKU_ENTRY_MODEL = os.getenv("HAIKU_ENTRY_MODEL", "claude-haiku-4-5-20251001")
HAIKU_ENTRY_MAX_TOKENS = max(64, int(os.getenv("HAIKU_ENTRY_MAX_TOKENS", "180")))
HAIKU_ENTRY_MAX_CALLS_PER_DAY = max(0, int(os.getenv("HAIKU_ENTRY_MAX_CALLS_PER_DAY", "2")))
HAIKU_ENTRY_CACHE_FILE = os.getenv("HAIKU_ENTRY_CACHE_FILE", "logs/haiku_entry_cache.json")
HAIKU_ENTRY_CACHE_TTL_HOURS = max(1.0, float(os.getenv("HAIKU_ENTRY_CACHE_TTL_HOURS", "6")))

# Haiku monitor gate for open positions
HAIKU_MONITOR_ENABLED = _env_bool("HAIKU_MONITOR_ENABLED", False)
HAIKU_MONITOR_INTERVAL_HOURS = max(1.0, float(os.getenv("HAIKU_MONITOR_INTERVAL_HOURS", "12.0")))
HAIKU_MONITOR_MODEL = os.getenv("HAIKU_MONITOR_MODEL", "claude-haiku-4-5-20251001")
HAIKU_MONITOR_MAX_TOKENS = max(64, int(os.getenv("HAIKU_MONITOR_MAX_TOKENS", "120")))
HAIKU_MONITOR_MAX_CALLS_PER_DAY = max(0, int(os.getenv("HAIKU_MONITOR_MAX_CALLS_PER_DAY", "6")))
HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT = min(
    1.0,
    max(0.0, float(os.getenv("HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT", "0.75"))),
)
HAIKU_MONITOR_CACHE_FILE = os.getenv("HAIKU_MONITOR_CACHE_FILE", "logs/haiku_monitor_cache.json")

# [MODUL A] Economy Sensing
HAIKU_SENSING_ENABLED = _env_bool("HAIKU_SENSING_ENABLED", True)
HAIKU_SENSING_MODEL = os.getenv("HAIKU_SENSING_MODEL", "claude-haiku-4-5-20251001")
HAIKU_SENSING_MAX_TOKENS = max(64, int(os.getenv("HAIKU_SENSING_MAX_TOKENS", "100")))
HAIKU_SENSING_MAX_CALLS_PER_DAY = max(0, int(os.getenv("HAIKU_SENSING_MAX_CALLS_PER_DAY", "50")))
STATION_KNOWLEDGE_FILE = os.getenv("STATION_KNOWLEDGE_FILE", "logs/station_knowledge.json")
STATION_KNOWLEDGE_TTL_DAYS = max(1, int(os.getenv("STATION_KNOWLEDGE_TTL_DAYS", "30")))

# Weather evidence quality controls (for hold-to-resolve safety)
WEATHER_EVIDENCE_MAX_AGE_HOURS = float(os.getenv("WEATHER_EVIDENCE_MAX_AGE_HOURS", "3.0"))
WEATHER_EVIDENCE_MIN_QUALITY_SCORE = float(os.getenv("WEATHER_EVIDENCE_MIN_QUALITY_SCORE", "0.65"))

# [MODUL B & K] Zero-Flaw Logic (Consensus & Anomaly)
CONSENSUS_MAX_ERROR_C = float(os.getenv("CONSENSUS_MAX_ERROR_C", "2.5"))
HISTORICAL_DEVIATION_C = float(os.getenv("HISTORICAL_DEVIATION_C", "7.0"))
ANOMALY_LOG_FILE = os.getenv("ANOMALY_LOG_FILE", "logs/anomalies.log")

# WebSocket real-time position monitor
WS_PRICE_WATCHER_ENABLED = _env_bool("WS_PRICE_WATCHER_ENABLED", True)
WS_PRICE_WATCHER_URL = os.getenv(
    "WS_PRICE_WATCHER_URL",
    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
)
WS_RECONNECT_DELAY_SECONDS = max(1, int(os.getenv("WS_RECONNECT_DELAY_SECONDS", "10")))
WS_PING_INTERVAL_SECONDS = max(5, int(os.getenv("WS_PING_INTERVAL_SECONDS", "20")))
WS_WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WS_WATCHDOG_TIMEOUT_SECONDS", "120"))
WS_STALE_DETECTION_MINUTES = 15 # Threshold to trigger Hybrid Fallback (aggressive polling)

WS_BROADCAST_HOST = os.getenv("WS_BROADCAST_HOST", "0.0.0.0")
WS_BROADCAST_PORT = max(1, int(os.getenv("WS_BROADCAST_PORT", "8082")))
WS_BROADCAST_MAX_CLIENTS = max(1, int(os.getenv("WS_BROADCAST_MAX_CLIENTS", "10")))
WS_BROADCAST_PING_INTERVAL_SECONDS = max(
    5,
    int(os.getenv("WS_BROADCAST_PING_INTERVAL_SECONDS", "20")),
)
