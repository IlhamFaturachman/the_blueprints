# 🚀 The Blueprints: Future Improvement Roadmap
**Goal**: Transition from Wave 2 (Execution) to Wave 3 (Reliability) and Wave 4 (Scaling).
**Last Updated**: 2026-04-17 09:46 WIB

---

## 🛡️ LEVEL 1: Risk & Stability (Immediate Focus)

### 1. Correlation Safeguard (Anti "Double-Dip")
*   **Concept**: Prevent the bot from opening multiple positions on the same event (e.g., Above 15°C and Above 16°C).
*   **Fix**: Implement `event_slug` deduplication in `build_entry_candidates`.
*   **Value**: Prevents losing 3x the stake on a single forecast error.

### 2. Smart Timezone Awareness
*   **Concept**: Use precise timezones per city instead of longitude heuristics.
*   **Fix**: Map `pytz` timezones in `TARGET_CITIES`.
*   **Value**: Restores the **Peak Heat Consensus** guard without false positives.

### 3. Orderbook Depth Simulation (Real-Slippage)
*   **Concept**: Calculate the average fill price based on available liquidity.
*   **Fix**: Modify `fetch_orderbook_quote` to return depth maps and use them in `paper_positions` cost basis.
*   **Value**: Ensures Paper Trading results match the reality of "thin" markets.

---

## 🧠 LEVEL 2: Intelligence & Optimization (Wave 3)

### 4. Persistent Historical Cache (Rate Limit Mitigation)
*   **Concept**: Store historical weather data (2016-2024) in a permanent local disk cache (`logs/cache/`).
*   **Current Issue**: Bot is getting **429 Too Many Requests** from Open-Meteo because it fetches historical data redundantly every cycle.
*   **Value**: Drastically reduces API calls, speeds up cycle from 15 mins to < 2 mins, and ensures anomaly guards always have data.

### 5. Multi-Source Forecast Weighted Average
*   **Concept**: Instead of "first successful source", use a weighted average (e.g., 60% NOAA, 30% Open-Meteo, 10% Wttr.in).
*   **Value**: Smoother predictions that are less sensitive to a single API's outlier.

### 6. AI Contextual Sensing (Wave 3 Full)
*   **Concept**: Fully implement the **Haiku Monitoring** bridge to read local news snippets or "Special Rules" on Polymarket automatically.
*   **Value**: Avoids entering markets with "weird" resolution rules that the code doesn't understand.

---

## 📈 LEVEL 3: Infrastructure & Scaling (Wave 4)

### 7. WebSocket Handshake Hardening
*   **Concept**: Fix the `InvalidUpgrade` error in `broadcast.py` by supporting flexible connection headers (handling `Connection: close` vs `Connection: upgrade`).
*   **Value**: Ensures the UI Dashboard always stays connected and updates in real-time.

### 8. Gasless Trading Implementation (PolyProxy)
*   **Concept**: Switch from `signature_type=0` (EOA) to `signature_type=1` (PolyProxy).
*   **Fix**: Update `BlueprintsClobClient` in `execution.py` to support Polymarket's gasless relayer.
*   **Value**: Zero gas cost for Buy/Sell operations. Wallet only needs USDC.e.

### 9. Service Reliability (Systemd Refinement)
*   **Concept**: Optimize `blueprints-bot.service` with `KillSignal=SIGTERM` and `TimeoutStopSec=10`.
*   **Value**: Ensures clean shutdowns without corrupting position JSON files.

---

## 🧪 BACKLOG / IDEAS
- [ ] **Sequential Fetch for History**: If cache miss, fetch historical data one-by-one with 1s delay to avoid 429s.
- [ ] **Midnight Resolution Shield**: Avoid entering markets that resolve at exactly 00:00 UTC due to reporting lag.
- [ ] **Manual Override "Kill Switch"**: A Telegram command to stop all entries instantly (`/panic`).
- [ ] **Secret Management**: Move all tokens from `.env` to a secure vault or encrypted store.
