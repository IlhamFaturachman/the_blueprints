import concurrent.futures
from urllib.parse import quote

class ForecastTemp(float):
    """Float wrapper to store the oracle source."""
    def __new__(cls, value, source):
        obj = super().__new__(cls, value)
        obj.source = source
        return obj

def fetch_forecast_open_meteo(city, date, fetch_fn):
    from market_discovery import TARGET_CITIES
    coords = TARGET_CITIES.get(city)
    if not coords: return None
    params = {"latitude": coords["lat"], "longitude": coords["lon"], "daily": "temperature_2m_max", "timezone": "auto", "forecast_days": 3}
    try:
        data = fetch_fn("https://api.open-meteo.com/v1/forecast", params=params)
        daily = data.get("daily", {})
        times = daily.get("time", [])
        temps = daily.get("temperature_2m_max", [])
        if date in times: return temps[times.index(date)]
    except: pass
    return None

def fetch_forecast_wttr(city, date, fetch_fn):
    from market_discovery import TARGET_CITIES
    if city not in TARGET_CITIES: return None
    try:
        data = fetch_fn(f"https://wttr.in/{quote(city)}", params={"format": "j1"})
        for w in data.get("weather", []):
            if w.get("date") == date:
                return float(w.get("maxtempC", 0))
    except: pass
    return None

