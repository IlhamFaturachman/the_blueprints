with open("market_discovery.py", "r") as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if "def build_weather_evidence(city, date, forecast_temp_c, source=\"open-meteo\", now_utc=None, fetched_at=None):" in line:
        new_lines.append(line)
        new_lines.append("    source = getattr(forecast_temp_c, 'source', source)\n")
    elif "source_component = 0.85 if source == \"open-meteo\" else 0.70" in line:
        new_lines.append("    if source == \"dual-source\":\n")
        new_lines.append("        source_component = 0.95\n")
        new_lines.append("    elif source == \"open-meteo\":\n")
        new_lines.append("        source_component = 0.85\n")
        new_lines.append("    else:\n")
        new_lines.append("        source_component = 0.70\n")
    else:
        new_lines.append(line)

with open("market_discovery.py", "w") as f:
    f.writelines(new_lines)
print("build_weather_evidence patched!")
