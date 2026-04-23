#!/bin/bash
# 🛡️ THE BLUEPRINTS: Pre-Start Hardening Script (V4.10 Hardened)
# This script ensures that the environment is pristine before the bot starts.
# Aggressively kills ghost processes and clears stale SQLite locks.

PF="/opt/the_blueprints/the_blueprints.pid"
DB_DIR="/opt/the_blueprints/logs"
DB_FILE="$DB_DIR/blueprints_master.db"
BK_DIR="$DB_DIR/backups"

echo "[PRE-START $(date +'%H:%M:%S.%N')] Starting high-resilience pre-flight checks."

# 1. Neutralize Ghost Processes (Anti-Stall)
# [FIX-M-T4-1] Use more specific patterns to avoid killing unrelated processes
echo "[PRE-START] Purging ghost discovery and warmer processes..."
pkill -9 -f "python.*market_discovery" 2>/dev/null
pkill -9 -f "python.*warmer\.py" 2>/dev/null
pkill -9 -f "PriceWatcherProcess" 2>/dev/null

if [ -f "$PF" ]; then
    OLD=$(cat "$PF" 2>/dev/null)
    if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
        echo "[PRE-START] WARNING: ACTIVE INSTANCE STILL DETECTED (PID: $OLD). Killing..."
        kill -9 "$OLD" 2>/dev/null
        sleep 1
    fi
    rm -f "$PF"
fi

# 2. Database Integrity Backup (BEFORE any WAL operations)
# [FIX-T4-1-HIGH] Backup FIRST, then checkpoint WAL safely.
# Previously: rm -f *.db-wal deleted committed transactions after crash.
if [ -f "$DB_FILE" ]; then
    mkdir -p "$BK_DIR"
    TS=$(date +'%Y%m%d_%H%M%S')
    echo "[PRE-START] Backing up database to $BK_DIR/blueprints_backup_$TS.db"
    cp "$DB_FILE" "$BK_DIR/blueprints_backup_$TS.db"
    # Also backup WAL if it exists (contains committed data)
    [ -f "$DB_FILE-wal" ] && cp "$DB_FILE-wal" "$BK_DIR/blueprints_backup_${TS}.db-wal"
    # Keep last 7 days of backups
    find "$BK_DIR" -name "blueprints_backup_*" -mtime +7 -delete
fi

# 3. SQLite WAL Recovery (Corruption Shield)
# [FIX-T4-1-HIGH] Checkpoint WAL safely instead of deleting it.
# WAL may contain committed transactions not yet merged into main DB.
echo "[PRE-START] Checkpointing SQLite WAL..."
sqlite3 "$DB_FILE" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
# Only remove shm (shared memory) — it's recreated on connect
rm -f "$DB_DIR"/*.db-shm 2>/dev/null

echo "[PRE-START $(date +'%H:%M:%S.%N')] Environment PRISTINE. Proceeding to startup."
exit 0
