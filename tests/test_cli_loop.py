import pytest

from market_discovery_internal.cli import run_main_paper_loop_mode


def test_run_main_paper_loop_mode_retries_after_error_then_continues():
    calls = {"run": 0, "summary": 0}
    sleep_calls = []
    reported = []

    def run_cycle(force_aggressive_scan):
        assert force_aggressive_scan is False
        calls["run"] += 1
        if calls["run"] == 1:
            raise RuntimeError("network temporary failure")
        return {
            "opened": [],
            "closed": [],
            "open_positions": [],
            "discovery": {"opportunities": []},
            "state_path": "logs/paper_positions.json",
            "min_bound": 0.2,
            "max_bound": 0.3,
        }

    def print_summary(cycle, safe_float_fn, paper_min_city_diversity):
        assert isinstance(cycle, dict)
        calls["summary"] += 1

    def sleep_fn(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            raise KeyboardInterrupt()

    def report_error(error, consecutive_errors, retry_in_seconds):
        reported.append((type(error).__name__, consecutive_errors, retry_in_seconds))

    run_main_paper_loop_mode(
        aggressive_mode=False,
        paper_loop_interval_seconds=300,
        run_paper_trading_cycle_fn=run_cycle,
        print_paper_cycle_summary_fn=print_summary,
        sleep_fn=sleep_fn,
        continue_on_error=True,
        error_backoff_seconds=7,
        max_error_backoff_seconds=60,
        report_error_fn=report_error,
    )

    assert calls["run"] == 2
    assert calls["summary"] == 1
    assert sleep_calls == [30, 7, 300]
    assert reported == [("RuntimeError", 1, 7)]


def test_run_main_paper_loop_mode_raises_when_continue_on_error_is_false():
    def run_cycle(force_aggressive_scan):
        assert force_aggressive_scan is False
        raise RuntimeError("fatal")

    with pytest.raises(RuntimeError, match="fatal"):
        run_main_paper_loop_mode(
            aggressive_mode=False,
            paper_loop_interval_seconds=300,
            run_paper_trading_cycle_fn=run_cycle,
            print_paper_cycle_summary_fn=lambda _: None,
            sleep_fn=lambda _: None,
            continue_on_error=False,
        )
