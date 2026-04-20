import requests
from datetime import datetime

GAMMA_API = "https://gamma-api.polymarket.com/events/pagination"
CITIES = ["new york city", "chicago", "london", "hong kong", "miami", "toronto", "paris", "seoul", "singapore", "shanghai", "beijing", "los angeles", "houston", "dallas", "denver", "atlanta", "seattle", "austin", "madrid", "milan", "tel aviv", "warsaw", "ankara", "taipei", "sao paulo", "buenos aires"]

def audit():
    print("Starting Audit...")
    stats = {"raw": 0, "weather": 0, "city": 0, "spread": 0, "vol": 0, "pass": 0}
    params = {"tag_slug": "weather", "active": "true", "limit": 100, "offset": 0}
    
    for _ in range(10):
        try:
            data = requests.get(GAMMA_API, params=params).json().get("data", [])
            if not data: break
            for ev in data:
                for m in ev.get("markets", []):
                    stats["raw"] += 1
                    q = str(m.get("question", "")).lower()
                    if not any(k in q for k in ["weather", "temperature", "temp", "degrees"]): continue
                    stats["weather"] += 1
                    if not any(c in q for c in CITIES):
                        stats["city"] += 1
                        continue
                    if float(m.get("spread", 1)) > 0.12:
                        stats["spread"] += 1
                        continue
                    if float(m.get("volume24hr", 0)) < 500:
                        stats["vol"] += 1
                        continue
                    stats["pass"] += 1
                    print(f"PASS: {q} | Vol: {m.get('volume24hr')} | Spread: {m.get('spread')}")
            params["offset"] += 100
        except Exception as e: print(f"Error: {e}"); break

    print(f"\nResults: {stats}")

if __name__ == "__main__":
    audit()
