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
"$python_bin" "$script_dir/market_discovery.py" "$mode" "$@" < /dev/null
