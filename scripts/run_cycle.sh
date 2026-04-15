#!/bin/bash
set -euo pipefail

cd /opt/the_blueprints
mkdir -p logs

lock_file="/tmp/the_blueprints_paper_cycle.lock"
exec 9>"$lock_file"
if ! flock -n 9; then
	echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] skip: previous cycle still running" >> logs/cron.log
	exit 0
fi

source venv/bin/activate

# Keep runtime below cron interval to avoid cascading overlaps.
if ! timeout 14m ./run_paper_5usd.sh --paper --aggressive >> logs/cron.log 2>&1; then
	exit_code=$?
	echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] cycle failed with exit=${exit_code}" >> logs/cron.log
	exit "$exit_code"
fi
