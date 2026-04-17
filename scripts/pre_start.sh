#!/bin/bash
# 🛡️ THE BLUEPRINTS: Pre-Start Hardening Script
# This script ensures that the PID file is clean before the bot starts.
# It prevents "Ghost PID" locks from blocking the service.

PF="/opt/the_blueprints/the_blueprints.pid"

echo "[PRE-START $(date +'%H:%M:%S.%N')] Starting integrity check. PF exists=$([ -f "$PF" ] && echo yes || echo no)"

if [ -f "$PF" ]; then
    sleep 0.5
    OLD=$(cat "$PF" 2>/dev/null)
    
    if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
        echo "[PRE-START] ACTIVE INSTANCE DETECTED (PID: $OLD). Keeping PID file."
    else
        echo "[PRE-START] GHOST OR STALE INSTANCE DETECTED ($OLD). Force-removing $PF."
        rm -f "$PF"
    fi
fi

echo "[PRE-START $(date +'%H:%M:%S.%N')] Done."
exit 0
