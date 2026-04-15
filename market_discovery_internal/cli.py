"""CLI helpers for market_discovery modes."""


def parse_cli_mode_flags(argv):
    """Parse CLI mode flags used by main()."""
    argv = argv if isinstance(argv, list) else []
    return {
        "inspect_mode": "--inspect" in argv,
        "paper_mode": "--paper" in argv,
        "paper_loop_mode": "--paper-loop" in argv,
        "paper_report_mode": ("--paper-report" in argv) or ("--paper-report-json" in argv),
        "paper_report_json_mode": ("--paper-report-json" in argv)
        or (("--paper-report" in argv) and ("--json" in argv)),
        "diagnose_mode": "--diagnose" in argv,
        "aggressive_mode": ("--aggressive" in argv) or ("--aggresive" in argv),
    }


def run_main_paper_report_mode(
    paper_report_json_mode,
    load_paper_state_fn,
    print_paper_state_report_fn,
    paper_state_file,
):
    """Handle paper report mode in main()."""
    state = load_paper_state_fn(path=paper_state_file)
    output_format = "json" if paper_report_json_mode else "text"
    print_paper_state_report_fn(
        state=state,
        state_path=paper_state_file,
        recent_entries=5,
        output_format=output_format,
    )


def run_main_paper_loop_mode(
    aggressive_mode,
    paper_loop_interval_seconds,
    run_paper_trading_cycle_fn,
    print_paper_cycle_summary_fn,
    sleep_fn,
):
    """Handle paper loop mode in main()."""
    print(f"Starting paper loop every {paper_loop_interval_seconds}s. Press Ctrl+C to stop.")
    try:
        while True:
            cycle = run_paper_trading_cycle_fn(force_aggressive_scan=aggressive_mode)
            print_paper_cycle_summary_fn(cycle)
            sleep_fn(paper_loop_interval_seconds)
    except KeyboardInterrupt:
        print("\nPaper loop stopped.")


def run_main_paper_single_mode(
    aggressive_mode,
    run_paper_trading_cycle_fn,
    print_paper_cycle_summary_fn,
):
    """Handle one-shot paper mode in main()."""
    cycle = run_paper_trading_cycle_fn(force_aggressive_scan=aggressive_mode)
    print_paper_cycle_summary_fn(cycle)


def run_main_discovery_mode(
    inspect_mode,
    aggressive_mode,
    diagnose_mode,
    run_discovery_cycle_fn,
    print_discovery_diagnostics_fn,
    print_opportunities_fn,
    print_summary_fn,
    target_cities_len,
):
    """Handle discovery/default mode in main()."""
    discovery = run_discovery_cycle_fn(inspect=inspect_mode, aggressive_scan=aggressive_mode)

    if diagnose_mode:
        print_discovery_diagnostics_fn(discovery, aggressive_scan=aggressive_mode)

    print_opportunities_fn(discovery["opportunities"])
    print_summary_fn(
        total_cities=target_cities_len,
        failed_cities=discovery["failed_cities"],
        total_markets=len(discovery["markets_raw"]),
        parsed_markets=len(discovery["parsed"]),
        skipped_markets=discovery["skipped_markets"],
        exact_skipped=discovery["exact_skipped"],
        opportunities_count=len(discovery["opportunities"]),
    )
