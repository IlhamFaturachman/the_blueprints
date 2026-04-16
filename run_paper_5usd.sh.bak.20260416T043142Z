#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 0 ]]; then
  mode="$1"
  shift
else
  mode="--paper"
fi

case "$mode" in
  --paper|--paper-loop|--paper-report|--paper-report-json)
    ;;
  *)
    echo "Usage: $0 [--paper|--paper-loop|--paper-report|--paper-report-json] [extra-args]"
    exit 1
    ;;
esac

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$script_dir/.env" ]]; then
  set -a
  source "$script_dir/.env"
  set +a
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
elif [[ -x "$script_dir/.venv/bin/python" ]]; then
  python_bin="$script_dir/.venv/bin/python"
elif [[ -x "$script_dir/venv/bin/python" ]]; then
  python_bin="$script_dir/venv/bin/python"
else
  echo "ERROR: Python interpreter not found. Set PYTHON_BIN or create .venv/venv." >&2
  exit 1
fi

PAPER_STAKE_USD=1 \
PAPER_MAX_OPEN_POSITIONS=5 \
PAPER_ENTRY_MIN_PRICE=0.10 \
PAPER_ENTRY_MAX_PRICE=0.65 \
PAPER_STATE_FILE="logs/paper_positions_5usd.json" \
AI_MONTHLY_BUDGET_USD="${AI_MONTHLY_BUDGET_USD:-5.0}" \
SONNET_ENTRY_ENABLED="${SONNET_ENTRY_ENABLED:-true}" \
SONNET_ENTRY_MIN_CONFIDENCE="${SONNET_ENTRY_MIN_CONFIDENCE:-0.80}" \
SONNET_ENTRY_MODEL="${SONNET_ENTRY_MODEL:-claude-sonnet-4-6}" \
SONNET_ENTRY_MAX_CALLS_PER_DAY="${SONNET_ENTRY_MAX_CALLS_PER_DAY:-6}" \
HAIKU_MONITOR_ENABLED="${HAIKU_MONITOR_ENABLED:-true}" \
HAIKU_MONITOR_INTERVAL_HOURS="${HAIKU_MONITOR_INTERVAL_HOURS:-6.0}" \
HAIKU_MONITOR_MODEL="${HAIKU_MONITOR_MODEL:-claude-haiku-4-5-20251001}" \
HAIKU_MONITOR_MAX_CALLS_PER_DAY="${HAIKU_MONITOR_MAX_CALLS_PER_DAY:-24}" \
HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT="${HAIKU_MONITOR_MIN_CONFIDENCE_TO_EXIT:-0.75}" \
STRATEGY_EXACT_MIN_EDGE="${STRATEGY_EXACT_MIN_EDGE:-0.02}" \
STRATEGY_EXACT_MIN_MODEL_PROB="${STRATEGY_EXACT_MIN_MODEL_PROB:-0.10}" \
"$python_bin" "$script_dir/market_discovery.py" "$mode" "$@" < /dev/null
