import re

with open("market_discovery.py", "r") as f:
    src = f.read()

# 1. Add class ForecastTemp
forecast_class = """
class ForecastTemp(float):
    def __new__(cls, value, source):
        obj = super().__new__(cls, value)
        obj.source = source
        return obj

"""
if "class ForecastTemp" not in src:
    src = src.replace("def fetch_forecast(city, date):", forecast_class + "def fetch_forecast(city, date):")

# 2. Update fetch_forecast
fetch_replacement = """def fetch_forecast(city, date):
    \"\"\"Fetch the daily max temperature forecast from Open-Meteo and wttr.in.\"\"\"
    coords = TARGET_CITIES.get(city)
    if not coords:
        return None

    def _fetch_open_meteo():
        params = {"latitude": coords["lat"], "longitude": coords["lon"], "daily": "temperature_2m_max", "timezone": "auto", "forecast_days": 3}
        try:
            data = fetch_with_retry(OPEN_METEO_API, params=params)
            daily = data.get("daily", {})
            times = daily.get("time", [])
            temps = daily.get("temperature_2m_max", [])
            if date in times:
                return temps[times.index(date)]
        except Exception:
            pass
        return None

    def _fetch_wttr():
        try:
            import urllib.parse
            url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
            data = fetch_with_retry(url)
            for w in data.get("weather", []):
                if w.get("date") == date:
                    return float(w.get("maxtempC", 0))
        except Exception:
            pass
        return None

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_om = executor.submit(_fetch_open_meteo)
        f_wt = executor.submit(_fetch_wttr)
        t_om = f_om.result()
        t_wt = f_wt.result()

    if t_om is not None and t_wt is not None:
        avg = round((t_om + t_wt) / 2.0, 1)
        return ForecastTemp(avg, "dual-source")
    elif t_om is not None:
        return ForecastTemp(t_om, "open-meteo")
    elif t_wt is not None:
        return ForecastTemp(t_wt, "wttr.in")
    return None"""

import ast
try:
    ast.parse(fetch_replacement)
except SyntaxError as e:
    print("SyntaxError in fetch_replacement", e)

# Use regex to replace the old fetch_forecast until _hours_until_target_date
import re
new_src = re.sub(r'def fetch_forecast\(city, date\):.*?def _hours_until_target_date\(', fetch_replacement + '\n\n\ndef _hours_until_target_date(', src, flags=re.DOTALL)

with open("market_discovery.py", "w") as f:
    f.write(new_src)
print("fetch_forecast patched successfully!")
