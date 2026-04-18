#!/bin/bash
# 🛡️ THE BLUEPRINTS: Pre-Start Hardening Script
# This script ensures that the PID file is clean and database is backed up before the bot starts.

PF="/opt/the_blueprints/the_blueprints.pid"
DB_FILE="/opt/the_blueprints/logs/blueprints_master.db"
BK_DIR="/opt/the_blueprints/logs/backups"

echo "[PRE-START $(date +'%H:%M:%S.%N')] Starting pre-flight checks."

# 1. PID Cleanup (Anti-Ghost)
# Aggressive pkill to clear any detached background processes
echo "[PRE-START] Neutralizing any potential ghost processes..."
pkill -9 -f "market_discovery" 2>/dev/null
pkill -9 -f "warmer.py" 2>/dev/null

if [ -f "$PF" ]; then
    OLD=$(cat "$PF" 2>/dev/null)
    if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
        echo "[PRE-START] WARNING: ACTIVE INSTANCE STILL DETECTED (PID: $OLD) after pkill. Retrying..."
        kill -9 "$OLD" 2>/dev/null
        sleep 1
    fi
    rm -f "$PF"
fi

# 2. Database Backup (Corruption Shield)
if [ -f "$DB_FILE" ]; then
    mkdir -p "$BK_DIR"
    TS=$(date +'%Y%m%d_%H%M%S')
    echo "[PRE-START] Backing up database to $BK_DIR/blueprints_backup_$TS.db"
    cp "$DB_FILE" "$BK_DIR/blueprints_backup_$TS.db"
    # Keep only last 7 days of backups
    find "$BK_DIR" -name "blueprints_backup_*.db" -mtime +7 -delete
fi

echo "[PRE-START $(date +'%H:%M:%S.%N')] Done."
exit 0
