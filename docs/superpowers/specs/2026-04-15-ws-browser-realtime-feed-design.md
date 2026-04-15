# WebSocket Browser Real-Time Price Feed — Design Spec

**Date:** 2026-04-15
**Status:** Approved

---

## Goal

Push real-time YES token price updates from the backend (PriceWatcher) directly to the browser via WebSocket, so the "Harga Pasar (Bid)" column and Unrealized PnL in the web UI update live — visible in Chrome DevTools Network → WS tab.

---

## Context

- VPS: Debian 12, `103.253.244.158`
- Current HTTP server: `python3 -m http.server 8080 --directory /opt/the_blueprints` (no nginx)
- Bot project: `/opt/the_blueprints/`
- Paper state JSON: `logs/paper_positions_5usd.json`
- Web UI: `web_ui/index.html` — single HTML file, fetches JSON every 10s
- Backend already has `PriceWatcher` (connects to Polymarket CLOB WS, fires `on_price_update` callback)

---

## Architecture

```
Polymarket CLOB WS ──► PriceWatcher (daemon thread)
                              │
               ┌──────────────┴──────────────┐
               │                             │
        exit_callback                 broadcast_callback
     (close SL/TP positions)       (push price to browsers)
                                             │
                                  WsBroadcaster (asyncio event loop, thread)
                                  binds: 127.0.0.1:8081 (internal only)
                                             │
                                  nginx reverse proxy
                                  GET /ws → proxy_pass ws://127.0.0.1:8081
                                  port 8080 (external, replaces http.server)
                                             │
                                    Browser WebSocket
                               ws://103.253.244.158:8080/ws
```

---

## Components

### 1. nginx (replaces `python3 -m http.server`)

- Installed on VPS via `apt install nginx`
- Listens on port 8080
- Serves static files from `/opt/the_blueprints` (same as before)
- Proxies `/ws` path to `ws://127.0.0.1:8081` (internal WS server)
- Key headers: `Upgrade: websocket`, `Connection: upgrade`, `proxy_read_timeout: 86400`

### 2. `market_discovery_internal/ws_broadcaster.py` (new file)

Runs an `asyncio` WebSocket server on `127.0.0.1:8081` inside a dedicated daemon thread.

**Public API:**
```python
class WsBroadcaster:
    def start(self) -> None          # start asyncio loop in daemon thread
    def stop(self) -> None           # signal shutdown
    def broadcast_price(token_id: str, bid_price: float) -> None   # thread-safe
    def broadcast_closed(token_id: str, city: str, reason: str, exit_price: float) -> None  # thread-safe
```

**Message types (JSON, server → browser):**
```json
{"type": "price_update",    "token_id": "abc", "bid_price": 0.25, "ts": 1713200000}
{"type": "position_closed", "token_id": "abc", "city": "TEL AVIV", "reason": "stop_loss", "exit_price": 0.11}
{"type": "ping"}
```

**Behavior:**
- Maintains a set of connected browser clients
- `broadcast_price` / `broadcast_closed`: thread-safe call from PriceWatcher thread → schedules coroutine on asyncio loop via `loop.call_soon_threadsafe`
- Sends ping every 20s to keep connections alive
- Handles client disconnect gracefully (removes from set)
- Max clients: 10 (prevents unbounded memory growth on VPS)

### 3. `market_discovery.py` — `_run_main_paper_loop_mode` (modified)

Fan-out callback: PriceWatcher fires one `on_price_update` → calls both:
1. `exit_callback(token_id, bid_price)` — existing stop-loss/take-profit logic
2. `broadcaster.broadcast_price(token_id, bid_price)` — push to browsers

Also: when `make_ws_exit_callback` closes a position → call `broadcaster.broadcast_closed(...)`.

### 4. `web_ui/index.html` (modified)

**WS client behavior:**
- Connect to `ws://${location.hostname}:8080/ws` on page load
- Auto-reconnect with exponential backoff: 3s → 6s → 12s → 30s (max)
- On `price_update`: find table row by `token_id` (add `data-token-id` attribute), update bid price cell, recalculate unrealized PnL, flash cell green or red for 1s
- On `position_closed`: flash entire row with close reason color, wait 2s, trigger `fetchData()` to reload full state
- On `ping`: no-op (keepalive ack)
- On disconnect: show yellow dot, keep 10s JSON polling as fallback
- 10s JSON polling continues regardless (source of truth for full state)

**Header WS status indicator:**
- Green dot `●` + "Live" = connected
- Yellow dot `●` + "Reconnecting..." = disconnected, retrying
- Added next to the "Refresh Data" button

---

## Data Flow

```
1. Polymarket sends price tick → PriceWatcher._handle_message()
2. PriceWatcher calls on_price_update(token_id, bid_price)
3. Fan-out:
   a. exit_callback checks SL/TP → closes position if triggered
      → if closed: broadcaster.broadcast_closed(...)
   b. broadcaster.broadcast_price(token_id, bid_price)
4. WsBroadcaster pushes JSON to all connected browsers
5. Browser JS finds row by token_id, updates cell + PnL live
```

---

## VPS Setup Steps

1. `apt install -y nginx`
2. Write nginx config to `/etc/nginx/sites-available/blueprints`
3. Enable site, disable default, reload nginx
4. Stop old `python3 -m http.server` process (kill PID 22923)
5. Start nginx (or it starts automatically)
6. `pip install "websockets>=12.0"` in venv
7. Deploy updated `market_discovery_internal/ws_broadcaster.py`
8. Deploy updated `market_discovery.py`
9. Deploy updated `web_ui/index.html`
10. Restart paper loop (`run_paper_5usd.sh --paper-loop`)

---

## Error Handling

- WsBroadcaster start failure (port in use): log warning, continue paper loop without WS push — no crash
- Client send failure (disconnected mid-send): catch exception, remove client silently
- asyncio loop crash: log error, broadcaster marks itself stopped — paper loop continues
- Browser WS error: fall back to 10s JSON polling (already running), show yellow dot

---

## Testing

- Unit: `WsBroadcaster.broadcast_price` with a mock asyncio loop (no real network)
- Smoke: start broadcaster, connect with `wscat` or browser, verify `price_update` messages appear
- Integration: run `--paper-loop`, open `web_ui/index.html` in browser, check Network → WS tab

---

## Out of Scope

- HTTPS / WSS (can be added later by updating nginx with SSL cert)
- Browser → server messages (UI is read-only display)
- Persistent message queue (no replay for late-joining clients)
- Authentication on WS endpoint
