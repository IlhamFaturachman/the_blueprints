#!/bin/bash
# Enterprise Hardening: Total Resurrection Script
set -e

echo "[WAVE 4] Stopping all blueprints services..."
systemctl stop blueprints || true
systemctl disable blueprints || true

echo "[WAVE 4] Killing orphan processes..."
# Kill any process that has the log or the script open
fuser -k /tmp/the_blueprints.pid || true
fuser -k /opt/the_blueprints/market_discovery.py || true
pkill -9 -f "market_discovery.py" || true
pkill -9 -f "http.server" || true

# Clearing Locks
rm -f /tmp/the_blueprints.pid
rm -f /opt/the_blueprints/market_discovery.py.lock || true

echo "[WAVE 4] Cleaning logs and syncing state..."
# Wipe the stale log so we can start fresh
> /opt/the_blueprints/logs/paper_loop.out

# The state file /opt/the_blueprints/logs/paper_positions_5usd.json 
# should already have been updated by my previous SCP. 
# We verify NYC is OPEN and HOLD.
if grep -q "new york city" /opt/the_blueprints/logs/paper_positions_5usd.json; then
    echo "[WAVE 4] NYC detected in state. Strategy check..."
    grep -A 15 "new york city" /opt/the_blueprints/logs/paper_positions_5usd.json | grep -E "status|target_strategy"
else
    echo "[ERROR] NYC NOT FOUND IN STATE FILE!"
    exit 1
fi

echo "[WAVE 4] Starting UI Server..."
cd /opt/the_blueprints && (nohup /usr/bin/python3 -m http.server 8080 > /opt/the_blueprints/logs/ui_server.log 2>&1 &)

echo "[WAVE 4] Starting Market Discovery Bot..."
cd /opt/the_blueprints && (nohup /opt/the_blueprints/venv/bin/python3 /opt/the_blueprints/market_discovery.py --paper-loop >> /opt/the_blueprints/logs/paper_loop.out 2>&1 &)

echo "[WAVE 4] Waiting for stabilization (20s)..."
sleep 20

echo "[WAVE 4] Final Audit..."
ps aux | grep market_discovery.py | grep -v grep
tail -n 20 /opt/the_blueprints/logs/paper_loop.out
