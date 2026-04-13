from unittest.mock import patch
import sys

from market_discovery import main


def test_main_happy_path_calls_output_functions():
    parsed_market = {
        "city": "new york",
        "date": "2026-04-15",
        "end_date": "2026-04-15T12:00:00+00:00",
        "market_question": "Will New York exceed 75F?",
        "threshold": 75.0,
        "unit": "F",
        "direction": "above",
        "yes_price": 0.28,
        "token_id": "0xabc",
        "hours_until_resolve": 18.0,
    }

    with patch("market_discovery.fetch_markets", return_value=[{"id": 1}]), \
            patch("market_discovery.parse_market", return_value=parsed_market), \
            patch("market_discovery.fetch_forecast", return_value=25.0), \
            patch("market_discovery.print_opportunities") as mock_print_opps, \
            patch("market_discovery.print_summary") as mock_print_summary:
        main()

    mock_print_opps.assert_called_once()
    mock_print_summary.assert_called_once()
    kwargs = mock_print_summary.call_args.kwargs
    assert kwargs["parsed_markets"] == 1
    assert kwargs["opportunities_count"] == 1


def test_main_skips_exact_markets_without_forecast_call():
    parsed_market = {
        "city": "new york",
        "date": "2026-04-15",
        "end_date": "2026-04-15T12:00:00+00:00",
        "market_question": "Will New York be exactly 75F?",
        "threshold": 75.0,
        "unit": "F",
        "direction": "exact",
        "yes_price": 0.28,
        "token_id": "0xabc",
        "hours_until_resolve": 18.0,
    }

    with patch("market_discovery.fetch_markets", return_value=[{"id": 1}]), \
            patch("market_discovery.parse_market", return_value=parsed_market), \
            patch("market_discovery.fetch_forecast") as mock_fetch_forecast, \
            patch("market_discovery.print_opportunities"), \
            patch("market_discovery.print_summary") as mock_print_summary:
        main()

    mock_fetch_forecast.assert_not_called()
    kwargs = mock_print_summary.call_args.kwargs
    assert kwargs["exact_skipped"] == 1


def test_main_paper_mode_runs_paper_cycle(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_discovery.py", "--paper"])

    with patch("market_discovery.run_paper_trading_cycle", return_value={"opened": [], "closed": [], "open_positions": [], "discovery": {"opportunities": []}, "state_path": "logs/paper_positions.json", "min_bound": 0.0, "max_bound": 1.0}) as mock_cycle, patch(
        "market_discovery.print_paper_cycle_summary"
    ) as mock_print:
        main()

    mock_cycle.assert_called_once()
    mock_print.assert_called_once()


def test_main_diagnose_mode_prints_diagnostics(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_discovery.py", "--diagnose", "--aggressive"])

    discovery = {
        "markets_raw": [],
        "parsed": [],
        "enriched": [],
        "opportunities": [],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.run_discovery_cycle", return_value=discovery) as mock_run, patch(
        "market_discovery.print_discovery_diagnostics"
    ) as mock_diag, patch("market_discovery.print_opportunities") as mock_opp, patch(
        "market_discovery.print_summary"
    ) as mock_summary:
        main()

    mock_run.assert_called_once_with(inspect=False, aggressive_scan=True)
    mock_diag.assert_called_once()
    mock_opp.assert_called_once()
    mock_summary.assert_called_once()
