"""
[MODUL M] BACKTEST RUNNER
Replays recently-resolved Polymarket weather markets against the Blueprints
strategy to validate edge calculation, win rate, and simulated PnL.

Strategy:
  1. Fetch resolved weather markets from last N days via Gamma API.
  2. For each market, reconstruct what our model would have predicted at entry
     (using historical Open-Meteo data for the target date).
  3. Compare model prediction vs actual outcome (outcomePrices).
  4. Report: markets_scanned, entered, wins, losses, win_rate, simulated_pnl.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from market_discovery_internal.config import (
    GAMMA_EVENTS_API,
    STRATEGY_MIN_MODEL_PROB,
    STRATEGY_MIN_EDGE,
    PAPER_ENTRY_MIN_PRICE,
    PAPER_ENTRY_MAX_PRICE,
    PAPER_STAKE_USD,
)
from market_discovery_internal.utils import fetch_with_retry
from market_discovery_internal.parsing import parse_market

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BACKTEST_REPORT_FILE = os.getenv("BACKTEST_REPORT_FILE", "logs/backtest_report.json")

def _fetch_resolved_markets(days_back=7, limit=100):
    """Fetch recently-closed weather markets from Gamma API."""
    params = {
        "closed": "true",
        "tag_slug": "weather",
        "limit": limit,
        "order": "endDate",
        "ascending": "false",
    }
    try:
        data = fetch_with_retry(GAMMA_EVENTS_API, params=params)
        # /events returns a list directly or wrapped in a key
        if isinstance(data, list):
            events = data
        elif isinstance(data, dict):
            events = data.get("data", data.get("events", []))
        else:
            events = []
    except Exception as e:
        print(f"[BACKTEST] Failed to fetch resolved markets: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    markets = []
    for event in events:
        # Each event may contain multiple markets
        raw_markets = event.get("markets", [event]) if isinstance(event, dict) else []
        for raw in raw_markets:
            end_date_str = raw.get("endDate") or raw.get("end_date_iso") or ""
            try:
                end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if end_dt >= cutoff:
                markets.append(raw)

    return markets


def _resolve_outcome(raw_market):
    """Return True if YES resolved (outcomePrices[0] == '1'), False if NO, None if unknown."""
    outcome_prices = raw_market.get("outcomePrices", [])
    if not outcome_prices:
        return None
    try:
        yes_outcome = float(outcome_prices[0])
        return yes_outcome >= 0.99
    except (ValueError, TypeError, IndexError):
        return None


def _fetch_historical_forecast(city, date):
    """Fetch historical max temperature from Open-Meteo archive for a specific date."""
    from market_discovery_internal.config import TARGET_CITIES, OPEN_METEO_HISTORICAL_API
    coords = TARGET_CITIES.get(city)
    if not coords:
        return None
    try:
        params = {
            "latitude": coords["lat"],
            "longitude": coords["lon"],
            "start_date": date,
            "end_date": date,
            "daily": "temperature_2m_max",
            "timezone": "auto",
        }
        data = fetch_with_retry(OPEN_METEO_HISTORICAL_API, params=params)
        daily = data.get("daily", {})
        temps = daily.get("temperature_2m_max", [])
        return float(temps[0]) if temps and temps[0] is not None else None
    except Exception:
        return None


def _model_would_enter(parsed_market, forecast_temp):
    """Run calculate_edge and check if we would have entered this market."""
    from market_discovery_internal.parsing import calculate_edge
    if forecast_temp is None:
        return None, None
    try:
        edge_result = calculate_edge(parsed_market, forecast_temp)
    except Exception:
        return None, None

    if not edge_result:
        return None, None

    model_prob = edge_result.get("model_prob", 0.0)
    edge = edge_result.get("edge", 0.0)
    yes_price = float(parsed_market.get("yes_price", 0.0))

    if (
        model_prob >= STRATEGY_MIN_MODEL_PROB
        and edge >= STRATEGY_MIN_EDGE
        and PAPER_ENTRY_MIN_PRICE <= yes_price <= PAPER_ENTRY_MAX_PRICE
    ):
        return True, edge_result

    return False, edge_result


# ---------------------------------------------------------------------------
# Main backtest entry point
# ---------------------------------------------------------------------------

def run_backtest(days_back=7, limit=100, verbose=True):
    """
    Replay last N days of resolved weather markets.
    Returns a report dict and saves it to BACKTEST_REPORT_FILE.
    """
    if verbose:
        print(f"\n{'='*60}")
        print(f"[MODUL M] BACKTEST ENGINE — last {days_back} days")
        print(f"{'='*60}")

    raw_markets = _fetch_resolved_markets(days_back=days_back, limit=limit)
    if verbose:
        print(f"Fetched {len(raw_markets)} resolved market candidates")

    scanned = 0
    parse_failed = 0
    no_outcome = 0
    no_forecast = 0
    would_skip = 0
    entered = []
    wins = []
    losses = []

    for raw in raw_markets:
        scanned += 1

        # Parse market using the standard pipeline
        parsed = parse_market(raw)
        if not parsed:
            parse_failed += 1
            continue

        city = parsed.get("city")
        date = parsed.get("date")
        yes_price = float(parsed.get("yes_price", 0.0))

        # Get actual outcome
        outcome_yes = _resolve_outcome(raw)
        if outcome_yes is None:
            no_outcome += 1
            continue

        # Fetch historical weather for target date (what model would have seen)
        forecast_temp = _fetch_historical_forecast(city, date)
        if forecast_temp is None:
            no_forecast += 1
            continue

        would_enter, edge_result = _model_would_enter(parsed, forecast_temp)
        if not would_enter:
            would_skip += 1
            continue

        # Simulate trade
        entry_price = yes_price
        simulated_exit_price = 1.0 if outcome_yes else 0.0
        stake = PAPER_STAKE_USD
        quantity = round(stake / entry_price, 6) if entry_price > 0 else 0
        cost_basis = round(quantity * entry_price, 4)
        exit_value = round(quantity * simulated_exit_price, 4)
        pnl = round(exit_value - cost_basis, 4)

        trade = {
            "city": city,
            "date": date,
            "market_question": parsed.get("market_question"),
            "direction": parsed.get("direction"),
            "threshold": parsed.get("threshold"),
            "unit": parsed.get("unit"),
            "entry_price": entry_price,
            "forecast_temp_c": forecast_temp,
            "model_prob": edge_result.get("model_prob") if edge_result else None,
            "edge": edge_result.get("edge") if edge_result else None,
            "outcome_yes": outcome_yes,
            "simulated_pnl": pnl,
        }

        entered.append(trade)
        if pnl > 0:
            wins.append(trade)
        else:
            losses.append(trade)

    n_entered = len(entered)
    n_wins = len(wins)
    n_losses = len(losses)
    win_rate = round(n_wins / n_entered, 4) if n_entered > 0 else 0.0
    total_pnl = round(sum(t["simulated_pnl"] for t in entered), 4)
    avg_win_pnl = round(sum(t["simulated_pnl"] for t in wins) / n_wins, 4) if wins else 0.0
    avg_loss_pnl = round(sum(t["simulated_pnl"] for t in losses) / n_losses, 4) if losses else 0.0

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "days_back": days_back,
            "limit": limit,
            "strategy_min_model_prob": STRATEGY_MIN_MODEL_PROB,
            "strategy_min_edge": STRATEGY_MIN_EDGE,
            "paper_entry_min_price": PAPER_ENTRY_MIN_PRICE,
            "paper_entry_max_price": PAPER_ENTRY_MAX_PRICE,
            "paper_stake_usd": PAPER_STAKE_USD,
        },
        "summary": {
            "scanned": scanned,
            "parse_failed": parse_failed,
            "no_outcome": no_outcome,
            "no_forecast": no_forecast,
            "would_skip": would_skip,
            "entered": n_entered,
            "wins": n_wins,
            "losses": n_losses,
            "win_rate_pct": round(win_rate * 100, 1),
            "total_simulated_pnl_usd": total_pnl,
            "avg_win_pnl_usd": avg_win_pnl,
            "avg_loss_pnl_usd": avg_loss_pnl,
        },
        "trades": entered,
    }

    # Persist report
    os.makedirs("logs", exist_ok=True)
    with open(_BACKTEST_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    if verbose:
        s = report["summary"]
        print(f"\n--- RESULTS ---")
        print(f"Markets scanned   : {s['scanned']}")
        print(f"Parse failed      : {s['parse_failed']}")
        print(f"No outcome data   : {s['no_outcome']}")
        print(f"No forecast data  : {s['no_forecast']}")
        print(f"Strategy skipped  : {s['would_skip']}")
        print(f"Entered           : {s['entered']}")
        print(f"Wins / Losses     : {s['wins']} / {s['losses']}")
        print(f"Win rate          : {s['win_rate_pct']}%")
        print(f"Total PnL (sim)   : USD {s['total_simulated_pnl_usd']:+.4f}")
        print(f"Avg win PnL       : USD {s['avg_win_pnl_usd']:+.4f}")
        print(f"Avg loss PnL      : USD {s['avg_loss_pnl_usd']:+.4f}")
        print(f"\nReport saved → {_BACKTEST_REPORT_FILE}")
        print(f"{'='*60}\n")

    return report


# Keep backward-compat stub callable
def run_fuzz_simulation():
    """Legacy stub — redirects to real backtest."""
    run_backtest(days_back=7)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="[MODUL M] Blueprints Backtest Runner")
    parser.add_argument("--days", type=int, default=7, help="Days of history to replay (default: 7)")
    parser.add_argument("--limit", type=int, default=100, help="Max markets to fetch (default: 100)")
    args = parser.parse_args()
    run_backtest(days_back=args.days, limit=args.limit)
