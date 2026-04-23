# Tier 4 Audit — Infrastructure & Security

**Status:** COMPLETE  
**Date:** 23 April 2026, 20:15 WIB  
**Method:** ANALYSIS_WORKFLOW.md Step 1 + Step 3b  
**Lines Audited:** ~700 lines across 6 areas

---

## Grand Summary

| Area | Lines | New Bugs | Highest Severity |
|------|-------|----------|-----------------|
| T4-1: pre_start.sh | 46 | 1 HIGH + 1 MEDIUM | HIGH |
| T4-2: blueprints-ui.service | 17 | 1 CRITICAL | **CRITICAL** |
| T4-3: healthcheck.sh | 235 | 1 MEDIUM | MEDIUM |
| T4-4: reset_warehouse.py | 158 | 1 MEDIUM | MEDIUM |
| T4-5: PID lock + signals | 169 | 1 MEDIUM | MEDIUM |
| T4-6: database_manager.py | 495 | 1 MEDIUM | MEDIUM |
| **Total** | **~1120** | **8 actionable** | **1 CRITICAL** |

---

## CRITICAL Bug Found (1)

### T4-2-CRIT: UI Service Serves Entire Project Directory Including .env

| Field | Value |
|-------|-------|
| **File** | `setup_and_legacy/blueprints-ui.service:7,8,10` |
| **Severity** | CRITICAL |

**Problem:** `python3 -m http.server 8080` runs as root from `/opt/the_blueprints/`, serving EVERY file recursively with directory listing. This exposes `.env` (private keys, API tokens, wallet credentials), the SQLite database, all source code, and logs.

**Impact:** If port 8080 is reachable from the internet, this is an **active credential exposure**. Anyone can download `.env` and steal wallet funds.

**Proposed Fix:**
1. **Immediate:** Disable the service: `systemctl stop blueprints-ui && systemctl disable blueprints-ui`
2. **Permanent:** Replace with nginx serving only `web_ui/` directory, or use a dedicated static file server with restricted paths.

**Note:** The actual VPS uses a separate nginx config (port 8080) that proxies to the command server. This systemd unit in `setup_and_legacy/` may be deprecated. **VERIFY on VPS whether this service is active.**

---

## HIGH Bug Found (1)

### T4-1-HIGH: WAL File Deletion Discards Committed Transactions

| Field | Value |
|-------|-------|
| **File** | `scripts/pre_start.sh:32-33` |
| **Severity** | HIGH |

**Problem:** `rm -f "$DB_DIR"/*.db-shm "$DB_DIR"/*.db-wal` deletes SQLite WAL files unconditionally. After a crash, the WAL may contain committed transactions not yet checkpointed to the main DB. Deleting it causes silent data loss.

**Proposed Fix:**
```bash
# Replace rm -f with proper checkpoint:
sqlite3 "$DB_FILE" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
```

---

## MEDIUM Bugs Found (6)

| # | File:Line | What | Impact |
|---|----------|------|--------|
| M-T4-1 | `pre_start.sh:15` | `pkill -f "market_discovery"` matches unrelated processes | Could kill editors, grep, etc. |
| M-T4-3 | `healthcheck.sh:50` | `stat -c %Y` is Linux-only | Healthcheck broken on macOS |
| M-T4-4 | `reset_warehouse.py:37` | Bot-running check returns False on non-Linux | Reset during active trading |
| M-T4-5 | `market_discovery.py:483` | Fallback PIDLock truncates file before lock | Race window (low practical risk) |
| M-T4-6a | `database_manager.py:524` | `record_trade_history` TOCTOU race — no UNIQUE constraint | Duplicate trade entries |
| M-T4-6b | `database_manager.py` | Missing indexes on `trade_history(city)` and `(token_id, opened_at)` | Slow queries at scale |

---

## LOW Bugs Found (4)

| # | File:Line | What |
|---|----------|------|
| L-T4-1 | `pre_start.sh:40` | Backup happens after WAL deletion |
| L-T4-3 | `healthcheck.sh:68` | `source "$TRACK_FILE"` is code injection vector |
| L-T4-4 | `reset_warehouse.py:54` | No backup before reset |
| L-T4-6 | `database_manager.py:317` | Silent data loss on corrupt raw_json |

---

## Proposed Changes Summary

| Priority | Bug | Fix | Risk |
|----------|-----|-----|------|
| 1 | T4-2-CRIT | Verify + disable `blueprints-ui.service` on VPS | SAFE |
| 2 | T4-1-HIGH | Replace `rm -f *.db-wal` with `PRAGMA wal_checkpoint(TRUNCATE)` | SAFE |
| 3 | M-T4-6a | Add `CREATE UNIQUE INDEX idx_trade_dedup ON trade_history(token_id, opened_at)` | LOW RISK |
| 4 | M-T4-1 | Use more specific pkill pattern | SAFE |

---

*Audit conducted following ANALYSIS_WORKFLOW.md with Step 3b critical self-questioning. Security concerns prioritized.*
