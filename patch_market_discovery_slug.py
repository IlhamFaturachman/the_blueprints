import re

with open("market_discovery.py", "r") as f:
    code = f.read()

# Edit the return block of parse_market
old_return = '''    return _with_reason({
        "city": city,
        "date": date_str,
        "end_date": end_dt.isoformat(),
        "market_question": question,
        "threshold": threshold,
        "unit": unit,
        "direction": direction,
        "yes_price": yes_price,
        "token_id": token_id,
        "hours_until_resolve": round(hours_until_resolve, 1),
    })'''

new_return = '''    market_slug = raw.get("slug") or raw.get("event_slug") or ""
    return _with_reason({
        "city": city,
        "date": date_str,
        "end_date": end_dt.isoformat(),
        "market_question": question,
        "market_slug": market_slug,
        "threshold": threshold,
        "unit": unit,
        "direction": direction,
        "yes_price": yes_price,
        "token_id": token_id,
        "hours_until_resolve": round(hours_until_resolve, 1),
    })'''

code = code.replace(old_return, new_return)

# Edit the position construction to include it
old_pos = '''        "end_date": opportunity.get("end_date"),
        "entry_price": entry_price,'''

new_pos = '''        "end_date": opportunity.get("end_date"),
        "market_slug": opportunity.get("market_slug", ""),
        "entry_price": entry_price,'''

code = code.replace(old_pos, new_pos)

with open("market_discovery.py", "w") as f:
    f.write(code)
print("patch applied")
