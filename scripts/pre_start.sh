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
echo "[PRE-START] Purging ghost discovery and warmer processes..."
pkill -9 -f "market_discovery" 2>/dev/null
pkill -9 -f "warmer.py" 2>/dev/null
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

# 2. SQLite Stale Lock Cleanup (Corruption Shield)
# If the bot crashed, stale shared memory or write-ahead logs can block the new instance.
echo "[PRE-START] Clearing any stale database locks..."
rm -f "$DB_DIR"/*.db-shm 2>/dev/null
rm -f "$DB_DIR"/*.db-wal 2>/dev/null

# 3. Database Integrity Backup
if [ -f "$DB_FILE" ]; then
    mkdir -p "$BK_DIR"
    TS=$(date +'%Y%m%d_%H%M%S')
    echo "[PRE-START] Backing up database to $BK_DIR/blueprints_backup_$TS.db"
    cp "$DB_FILE" "$BK_DIR/blueprints_backup_$TS.db"
    # Keep last 7 days of backups
    find "$BK_DIR" -name "blueprints_backup_*.db" -mtime +7 -delete
fi

echo "[PRE-START $(date +'%H:%M:%S.%N')] Environment PRISTINE. Proceeding to startup."
exit 0
