from unittest.mock import patch, ANY
import sys

from market_discovery import main


def test_main_happy_path_calls_output_functions():
    """Discovery mode: wired_run_discovery_cycle → print_opportunities + print_summary."""
    discovery = {
        "markets_raw": [{"id": 1}],
        "parsed": [{"city": "new york"}],
        "enriched": [{"city": "new york"}],
        "opportunities": [{"city": "new york", "edge": 0.5}],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.wired_run_discovery_cycle", return_value=discovery), \
            patch("market_discovery.print_opportunities") as mock_print_opps, \
            patch("market_discovery.print_summary") as mock_print_summary:
        main()

    mock_print_opps.assert_called_once()
    mock_print_summary.assert_called_once()
    kwargs = mock_print_summary.call_args.kwargs
    assert kwargs["parsed_markets"] == 1
    assert kwargs["opportunities_count"] == 1


def test_main_skips_exact_markets_without_forecast_call():
    """Discovery mode with exact markets: exact_skipped is reported."""
    discovery = {
        "markets_raw": [{"id": 1}],
        "parsed": [],
        "enriched": [],
        "opportunities": [],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 1,
    }

    with patch("market_discovery.wired_run_discovery_cycle", return_value=discovery), \
            patch("market_discovery.print_opportunities"), \
            patch("market_discovery.print_summary") as mock_print_summary:
        main()

    kwargs = mock_print_summary.call_args.kwargs
    assert kwargs["exact_skipped"] == 1


def test_main_paper_mode_runs_paper_cycle(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_discovery.py", "--paper"])

    with patch("market_discovery.wired_run_paper_trading_cycle", return_value={
        "opened": [], "closed": [], "open_positions": [],
        "discovery": {"opportunities": []},
        "state_path": "logs/paper_positions.json",
        "min_bound": 0.0, "max_bound": 1.0,
    }) as mock_cycle, patch(
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

    with patch("market_discovery.wired_run_discovery_cycle", return_value=discovery) as mock_run, patch(
        "market_discovery.print_discovery_diagnostics"
    ) as mock_diag, patch("market_discovery.print_opportunities") as mock_opp, patch(
        "market_discovery.print_summary"
    ) as mock_summary:
        main()

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["inspect"] is False
    assert call_kwargs["aggressive_scan"] is True
    mock_opp.assert_called_once()
    mock_summary.assert_called_once()


def test_main_typo_aggressive_flag_still_enables_aggressive(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_discovery.py", "--diagnose", "--aggresive"])

    discovery = {
        "markets_raw": [],
        "parsed": [],
        "enriched": [],
        "opportunities": [],
        "failed_cities": [],
        "skipped_markets": 0,
        "exact_skipped": 0,
    }

    with patch("market_discovery.wired_run_discovery_cycle", return_value=discovery) as mock_run, patch(
        "market_discovery.print_discovery_diagnostics"
    ), patch("market_discovery.print_opportunities"), patch("market_discovery.print_summary"):
        main()

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["aggressive_scan"] is True


def test_main_paper_report_mode_prints_state(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_discovery.py", "--paper-report"])
    sample_state = {
        "positions": [],
        "history": [],
        "cycle_journal": [],
        "updated_at": None,
        "meta": {},
    }

    with patch("market_discovery.load_paper_state", return_value=sample_state) as mock_load, patch(
        "market_discovery.wired_print_paper_state_report"
    ) as mock_report:
        main()

    mock_load.assert_called_once()
    call_kwargs = mock_report.call_args.kwargs
    assert call_kwargs["state"] == sample_state
    assert call_kwargs["recent_entries"] == 5
    assert call_kwargs["output_format"] == "text"


def test_main_paper_report_json_mode_prints_state_json(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_discovery.py", "--paper-report-json"])
    sample_state = {
        "positions": [],
        "history": [],
        "cycle_journal": [],
        "updated_at": None,
        "meta": {},
    }

    with patch("market_discovery.load_paper_state", return_value=sample_state) as mock_load, patch(
        "market_discovery.wired_print_paper_state_report"
    ) as mock_report:
        main()

    mock_load.assert_called_once()
    call_kwargs = mock_report.call_args.kwargs
    assert call_kwargs["output_format"] == "json"


def test_main_paper_report_json_flag_prints_state_json(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_discovery.py", "--paper-report", "--json"])
    sample_state = {
        "positions": [],
        "history": [],
        "cycle_journal": [],
        "updated_at": None,
        "meta": {},
    }

    with patch("market_discovery.load_paper_state", return_value=sample_state) as mock_load, patch(
        "market_discovery.wired_print_paper_state_report"
    ) as mock_report:
        main()

    mock_load.assert_called_once()
    call_kwargs = mock_report.call_args.kwargs
    assert call_kwargs["output_format"] == "json"
