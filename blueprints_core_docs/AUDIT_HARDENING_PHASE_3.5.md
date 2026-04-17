# 🕵️ DEEP AUDIT & HARDENING PLAN (Phase 3.5)

Dokumen ini berisi hasil audit mendalam pada codebase **The Blueprints** (Local & VPS) dan rencana penguatan (hardening) untuk memastikan operasional 7 hari tanpa cacat.

## ⚡ Temuan Audit & Solusi Terpilih

Berdasarkan diskusi dengan USER, berikut adalah parameter dan keputusan final yang harus diimplementasikan:

### 1. [HIGH] AI Budgeting Race Condition
**Temuan**: Sistem pencatatan budget `ai_usage_ledger.json` saat ini tidak aman terhadap multi-threading.
- **Risiko**: Over-spending budget AI jika terjadi call simultan.
- **Solusi**: Pindahkan sistem ledger ke dalam **SQLite (Gudang Data)** dengan mekanisme *Atomic Transactions*.

### 2. [HIGH] Orderbook Min-Size Trap
**Temuan**: Modul likuiditas bisa menghasilkan stake di bawah batas minimal bursa.
- **Keputusan USER**: Set **Minimum Stake Floor = USD 1.00**.
- **Logika**: Jika likuiditas memaksa stake di bawah USD 1.00, bot harus melakukan `SKIP` pada peluang tersebut untuk menghindari penolakan order oleh bursa.

### 3. [MEDIUM] JSON vs Warehouse Desync (State Migration)
**Temuan**: Data posisi terbelah antara JSON dan SQLite.
- **Keputusan USER**: **Matikan file JSON sepenuhnya**. Pindahkan 100% data posisi dan status bot ke Database Warehouse.
- **Benefit**: Stabilitas jangka panjang, integritas data terjamin, dan kemudahan monitoring via SQL.

### 4. [MEDIUM] Static Alpha Penalty
**Temuan**: Penalti slippage statis (1.75%) pada model forecasting terlalu optimis.
- **Solusi**: Gunakan spread real-time dari orderbook untuk menghitung penalti yang lebih akurat pada tahap kalkulasi Alpha.

---

## 🛠️ Rincian Perubahan (Implementation Roadmap)

### [Component: Warehouse]
#### `database_manager.py` [MODIFY]
- Implementasi tabel `ai_usage_ledger` (process_name, month_key, cost_usd).
- Implementasi modul `active_positions` yang menggantikan fungsi `PAPER_STATE_FILE`.
- Gunakan `threading.Lock` dan `conn.commit()` yang lebih ketat.

### [Component: Logic]
#### `pricing.py` [MODIFY]
- Tambahkan konstanta `MIN_STAKE_THRESHOLD = 1.0`.
- Update `calculate_depth_adjusted_stake` untuk melempar sinyal `SKIP` jika hasil di bawah threshold.
- Refactor `calculate_edge` untuk menerima parameter spread real-time.

### [Component: Orchestrator]
#### `cycles.py` [MODIFY]
- Hapus ketergantungan pada `load_paper_state_fn` (JSON).
- Integrasi logic baca/tulis posisi langsung ke `BlueprintsDB`.
- Implementasi **Memory Watchdog**: Kirim alert Telegram jika RAM VPS > 850MB.

---

## 🏁 Verification Plan
1. **Concurrency Stress Test**: Simulasi 50 AI calls simultan untuk verifikasi integritas budget di DB.
2. **Dust Filtering Test**: Simulasi market likuiditas rendah untuk memastikan filter `USD 1.00` bekerja.
3. **Recovery Test**: Mematikan paksa bot dan memastikan data posisi tetap utuh di SQLite saat restart.

**Status**: Dirangkum untuk optimasi oleh GPT 5.3 Codex Xhigh.
**Target Directory**: `/opt/the_blueprints/` (VPS) & `/Users/macairm12020/Documents/Blueprints/the_blueprints/` (Local)
