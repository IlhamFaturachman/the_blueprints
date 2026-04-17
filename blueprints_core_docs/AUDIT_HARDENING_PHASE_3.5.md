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

---

## RE-AUDIT DELTA (LOCAL VERIFIED) - 2026-04-17

Bagian ini adalah hasil verifikasi ulang pada code lokal yang *sudah dicek langsung*, agar eksekutor tidak mengulang analisis panjang.

### Temuan Prioritas (Wajib Dikerjakan)

| Priority | Temuan | Lokasi | Dampak |
|---|---|---|---|
| CRITICAL | Variabel `now` tidak terdefinisi di jalur cycle | `market_discovery_internal/cycles.py` | Potensi crash saat close/evaluate position |
| CRITICAL | AI ledger masih JSON, belum atomic DB | `market_discovery_internal/analysis.py` | Race condition, budget over-spend |
| CRITICAL | Minimum stake floor USD 1.00 belum enforced | `market_discovery_internal/cycles.py`, `market_discovery_internal/pricing.py` | Dust stake lolos logic |
| HIGH | JSON mirror masih aktif (belum DB-only) | `market_discovery_internal/state_persistence.py` | Risiko desync SQLite vs JSON |
| HIGH | Penalti alpha masih statis 1.75% | `market_discovery_internal/pricing.py` | Edge bisa terlalu optimis/pesimis |
| HIGH | Drift runner path | `scripts/run_cycle.sh`, `setup_and_legacy/run_paper_5usd.sh` | Scheduler bisa gagal tergantung path |
| MEDIUM | Backtest import mismatch | `market_discovery_internal/backtest_runner.py` | Backtest berisiko gagal saat run |
| MEDIUM | Komponen kritis belum punya test dedicated | `tests/` | Regression rawan lolos |

### Bukti Lokasi Spesifik

1. **Undefined `now`**
- `market_discovery_internal/cycles.py` memakai `now_utc=now` pada beberapa close path.
- `market_discovery_internal/cycles.py` memakai `last_date < now.date()` di reset check.

2. **AI ledger JSON non-transactional**
- `market_discovery_internal/analysis.py`:
  - `_ensure_ai_usage_ledger()`
  - `_reserve_ai_call_slot()`
  - `_record_ai_usage_cost()`
- Semua masih mengandalkan `AI_USAGE_LEDGER_FILE` (JSON).

3. **Min stake floor belum aktif**
- `market_discovery_internal/cycles.py` hanya skip jika `dynamic_stake <= 0`.
- Belum ada konstanta floor eksplisit `1.00` sebagai hard gate.

4. **JSON mirror masih ditulis**
- `market_discovery_internal/state_persistence.py` masih melakukan `json.dump(state, ...)` pada save path.

5. **Alpha penalty statis**
- `market_discovery_internal/pricing.py` masih hardcoded `slippage_penalty = 0.0175`.

6. **Runner path drift**
- `scripts/run_cycle.sh` mengeksekusi `./run_paper_5usd.sh` dari root.
- File runner berada di `setup_and_legacy/run_paper_5usd.sh` dan menggunakan `script_dir/market_discovery.py`.

7. **Backtest import mismatch**
- `market_discovery_internal/backtest_runner.py` mengimpor `calculate_edge` dari parsing, bukan dari pricing.

---

## RE-AUDIT ROUND 2 (HIDDEN FLAWS VERIFIED) - 2026-04-17

Batch ini adalah deep-scan tambahan di luar rencana awal. Item berikut belum boleh diabaikan.

### Temuan Baru (Wajib Masuk Scope Eksekusi)

| Priority | Temuan Baru | Lokasi Bukti | Dampak Runtime |
|---|---|---|---|
| CRITICAL | Wiring callback salah: `update_paper_position_fn` diarahkan ke `evaluate_hybrid_exit` (bukan updater yang return tuple) | `market_discovery.py` | Setelah blocker `now/cycle_started` diperbaiki, cycle berisiko crash saat unpack decision/update |
| CRITICAL | Model data posisi di DB lossy: `active_positions` simpan subset field; loader tidak restore full position object | `market_discovery_internal/database_manager.py`, `market_discovery_internal/state_persistence.py` | Status/stop-loss/cost-basis bisa hilang atau salah, menyebabkan close logic dan PnL drift |
| CRITICAL | Discovery path melakukan `sys.exit(1)` saat fetch gagal; paper loop hanya catch `Exception` | `market_discovery_internal/discovery.py`, `market_discovery_internal/cli.py` | Gangguan API sementara bisa mematikan proses bot (melewati retry loop) |
| HIGH | `load_paper_state(path=...)` mengabaikan `path` dan selalu baca DB global | `market_discovery_internal/state_persistence.py` | Isolasi test/local gagal; risiko kontaminasi state antar environment |
| HIGH | Dedupe history berdasarkan `token_id` saja | `market_discovery_internal/state_persistence.py` | Trade berulang token yang sama tidak tercatat penuh, metrik historis bias |
| HIGH | Kill-switch HTTP terbuka tanpa auth + CORS wildcard + bind `0.0.0.0` | `market_discovery_internal/command_server.py` | Shutdown bot bisa dipicu pihak tidak berwenang |
| MEDIUM | Cycle metric berisiko dobel karena save awal cycle + save akhir sama-sama menambah metric terbaru | `market_discovery_internal/cycles.py`, `market_discovery_internal/state_persistence.py` | Dashboard/journal analytics bias dan noisy |
| MEDIUM | Lifecycle command server tidak ditutup pada shutdown helper | `market_discovery.py` | Risiko port conflict di mode switch/test runtime panjang |
| MEDIUM | WS watcher menonaktifkan TLS verification (`CERT_NONE`) | `market_discovery_internal/ws_price_watcher.py` | Risiko manipulasi feed jika koneksi diintervensi |

### Bukti Uji Tambahan (Test Signal)

1. `tests/test_paper_cycle.py` gagal pada `test_load_paper_state_empty_when_file_missing`:
- Ekspektasi state kosong untuk path baru.
- Aktual: data historis existing tetap terbaca (indikasi loader tidak menghormati `path`).

2. `tests/test_discovery_cycle.py` gagal pada `run_discovery_cycle()`:
- Signature drift: sekarang butuh dependency injection keyword-only arguments.
- Menandakan kontrak pemanggilan lama sudah pecah dan test harness belum sinkron.

3. `tests/test_cli_loop.py` gagal pada retry behavior:
- Menandakan contract loop behavior berubah terhadap test expectation.

### ACTION ADDENDUM (WAJIB DITUTUP SEBELUM RELEASE)

1. **Fix wiring crash path**
- Import dan wire fungsi updater posisi yang benar di entrypoint (`update_paper_position`, bukan `evaluate_hybrid_exit`).

2. **Fix DB position integrity**
- Simpan full position payload secara utuh (`raw_json`) atau pastikan seluruh field runtime kritis (`status`, `cost_basis`, `stop_loss_price`, `target_price`, `last_price`, dll) tersimpan/ter-restore.
- Loader wajib menghasilkan shape posisi yang identik dengan object runtime cycle.

3. **Fix fatal exit on transient API failure**
- Ganti `sys.exit(1)` di discovery fetch path menjadi exception terkontrol yang bisa ditangani loop retry.

4. **Fix state path contract**
- Saat `path` diberikan untuk mode test/local isolated, loader wajib hormati path tersebut (atau dokumenkan deprecation dan update seluruh test/consumers secara atomik).

5. **Fix trade-history keying**
- Jangan dedupe hanya `token_id`; gunakan key unik trade-level (`token_id + opened_at` atau UUID trade).

6. **Fix kill-switch security**
- Tambahkan auth token/header check dan origin restriction minimal.
- Jika lewat nginx publik, wajib ada guard di reverse-proxy + app layer.

7. **Fix cycle metric duplication**
- Pastikan `add_cycle_metric` hanya menambah metric baru sekali per cycle (idempotent guard by timestamp/cycle_id).

8. **Fix command server shutdown**
- Shutdown/close server instance saat stop background services.

9. **Fix WS TLS hardening**
- Hindari `ssl.CERT_NONE` untuk mode normal; jika fallback insecure diperlukan, jadikan opt-in debug mode dengan alert keras.

### Done Criteria Tambahan (Round 2)

1. Semua item `CRITICAL` di section ini selesai.
2. Targeted tests yang gagal pada batch ini sudah hijau setelah sinkronisasi test contract.
3. Tidak ada kill-switch call tanpa otorisasi yang berhasil pada simulasi.
4. Tidak ada process exit karena fetch failure transient (harus masuk retry path).

---

## PROFIT MAXIMIZATION ADDENDUM (ANALISIS MENDALAM DI LUAR BUG FIX)

Bagian ini fokus pada peningkatan performa trading setelah stabilitas runtime beres.

Catatan penting:
1. Tidak ada sistem yang bisa menjamin profit absolut.
2. Tujuan realistis: meningkatkan **expected value**, **risk-adjusted return**, dan menurunkan peluang drawdown fatal.
3. Semua improvement di bawah ini wajib tetap mematuhi guardrail risiko dari section sebelumnya.

### Gap Arsitektur Saat Ini (yang Menahan Potensi Profit)

1. **Probabilitas model belum terkalibrasi terhadap outcome real**
- Saat ini keputusan entry banyak bertumpu pada threshold statis (`model_prob`, `edge`) tanpa calibration curve berbasis hasil historis.

2. **Threshold entry masih mostly statis lintas regime market**
- Kondisi spread, depth, dan volatility tidak sepenuhnya mengubah standar masuk secara adaptif.

3. **Position sizing belum berbasis uncertainty portfolio-level**
- Sizing tier + depth-aware sudah ada, tetapi belum menggabungkan confidence uncertainty + correlation exposure secara eksplisit.

4. **Exit policy belum memaksimalkan retention profit**
- Dominan fixed TP/SL; belum ada trailing logic/partial take profit yang kuat untuk market yang bergerak cepat.

5. **Learning loop belum lengkap walau parameter tuner sudah tersedia di config**
- Ada parameter `AUTO_TUNER_*`, namun pipeline adaptasi performa city/bucket belum terlihat end-to-end sebagai source of truth runtime.

6. **Attribution profit leak belum granular**
- Belum ada laporan standar yang memisahkan leak dari spread, timing, bucket, city, dan close reason secara kuantitatif.

### High-Impact Improvement Matrix (Prioritas Eksekusi)

| Priority | Improvement | Dampak yang Ditargetkan | Lokasi Integrasi Awal |
|---|---|---|---|
| P0 | **Model Calibration Layer** (city/direction/horizon aware) | Mengurangi false confidence, edge lebih realistis | `analysis.py`, `pricing.py`, `database_manager.py` |
| P0 | **Adaptive Entry Threshold Matrix** (spread/depth/regime aware) | Entry lebih selektif saat market buruk, agresif saat market sehat | `cycles.py`, `analysis.py`, `config.py` |
| P1 | **Risk-Weighted Position Sizing** (confidence + uncertainty + correlation cap) | PnL lebih stabil, drawdown shock turun | `cycles.py`, `pricing.py`, `reporting.py` |
| P1 | **Advanced Exit Engine** (partial TP + trailing stop + thesis-decay exit) | Lock profit lebih cepat, kurangi round-trip menang jadi kalah | `cycles.py` |
| P1 | **Portfolio Correlation Guard v2** (city/weather-cluster/day bucket) | Cegah over-concentration exposure tersembunyi | `cycles.py`, `parsing.py` |
| P2 | **Auto-Tuner Runtime Activation** (gunakan `AUTO_TUNER_*` nyata) | Parameter adaptif berdasarkan performa real, bukan statis | `state_persistence.py`, `cycles.py`, `reporting.py` |
| P2 | **Champion-Challenger Shadow Mode** | Upgrade strategi bisa divalidasi tanpa merusak baseline | `backtest_runner.py`, `reporting.py`, `web_ui/index.html` |
| P2 | **Profit Leak Attribution Dashboard** | Keputusan tuning berbasis data, bukan intuisi | `database_manager.py`, `reporting.py`, `web_ui/index.html` |

### PROFIT PACKS (WAJIB BERURUTAN SETELAH BUGFIX CRITICAL)

#### PACK A - Probability Calibration & Confidence Hygiene (P0)
Action:
1. Tambah tabel/warehouse calibration per `city + direction + horizon_bin + price_bin`.
2. Hitung reliability stats (Brier-like error, hit-rate drift) dari trade history.
3. Terapkan `model_prob_calibrated` sebelum edge final dihitung.
4. Jika sampel belum cukup, fallback ke model lama + safety penalty.

Done Criteria:
1. Setiap keputusan entry menyimpan `raw_prob` vs `calibrated_prob`.
2. Ada bukti metrik calibration membaik pada rolling window.

#### PACK B - Adaptive Regime Entry Gate (P0)
Action:
1. Definisikan regime score dari spread, depth, dan source-consensus quality.
2. Mapping threshold dinamis untuk `min_prob`, `min_edge`, dan max price.
3. Saat regime buruk: auto-tighten gate; saat sehat: controlled relax.

Done Criteria:
1. Log cycle menampilkan regime class (`good/neutral/stress`) + gate yang dipakai.
2. Reject ratio di regime stress naik, tanpa mematikan semua peluang di regime good.

#### PACK C - Risk-Weighted Sizing Engine (P1)
Action:
1. Tambah multiplier sizing berbasis confidence, uncertainty, dan correlation exposure.
2. Terapkan hard cap total exposure portfolio + cap per-cluster.
3. Integrasikan floor stake (USD 1.00) sebagai constraint terakhir.

Done Criteria:
1. Tidak ada sizing yang melanggar cap portfolio/cluster.
2. Volatilitas realized PnL per cycle menurun dibanding baseline.

#### PACK D - Advanced Exit Logic (P1)
Action:
1. Tambahkan partial take-profit ladder (misal trim pertama di level profit awal).
2. Tambahkan trailing stop setelah posisi mencapai unrealized threshold tertentu.
3. Tambahkan `thesis_decay_exit` saat confidence turun bertahap mendekati expiry.

Done Criteria:
1. Close reason baru tercatat rapi dan terukur dampaknya.
2. Profit giveback dari posisi yang sempat profit berkurang signifikan.

#### PACK E - Auto-Tuner Activation (P2)
Action:
1. Aktifkan pipeline tuner per-city/per-bucket dengan parameter `AUTO_TUNER_*` yang sudah ada.
2. Simpan hasil tuner di state/meta + warehouse agar UI/telegram bisa tampilkan status real.
3. Tambahkan guard agar tuner tidak overreact saat sampel kecil.

Done Criteria:
1. `meta.auto_tuner` berisi state aktual (bukan placeholder kosong).
2. Ada jejak perubahan threshold yang explainable dan reversible.

#### PACK F - Champion-Challenger & Profit Attribution (P2)
Action:
1. Jalankan baseline vs challenger di shadow mode (no execution impact).
2. Simpan per-trade attribution: spread cost, entry bucket, exit reason, city cluster.
3. Tambah report mingguan “profit leak leaderboard”.

Done Criteria:
1. Keputusan promote strategy hanya jika challenger menang di KPI gate.
2. Ada daftar leak tertinggi yang bisa ditindak di sprint berikutnya.

### KPI GATE (HARUS LULUS SEBELUM PROMOTE)

Gunakan minimal rolling 7-14 hari paper data:

1. **Primary KPI**
- Higher net realized PnL vs baseline.
- Higher or equal close win-rate dengan drawdown tidak memburuk.

2. **Risk KPI**
- Max drawdown harian tidak naik.
- Exposure concentration per city/cluster tetap di bawah cap.

3. **Quality KPI**
- Forecast reject karena anomaly tetap terkontrol (tidak spike liar).
- Tidak ada lonjakan error runtime/cycle failure.

4. **Promotion Rule**
- Jika Primary naik tapi Risk/Quality fail: **reject promotion**.
- Promote hanya jika semua gate lulus.

### Simulasi Tambahan Khusus Profit Engine

1. **Regime Shock Simulation**
- Simulasikan spread melebar + depth tipis.
- Ekspektasi: gate mengetat, sizing turun, entry count turun terkontrol.

2. **False Confidence Simulation**
- Inject skenario probabilitas tinggi tapi outcome buruk beruntun.
- Ekspektasi: calibration+tuner menurunkan agresivitas city/bucket terkait.

3. **Profit Retention Simulation**
- Posisi sempat profit lalu retrace tajam.
- Ekspektasi: partial TP/trailing stop menyelamatkan sebagian profit.

4. **Correlation Shock Simulation**
- Banyak peluang dari cluster cuaca sama muncul bersamaan.
- Ekspektasi: correlation guard mencegah over-stack posisi searah.

### Scope Guard untuk Addendum Profit

1. Improvement profit **tidak boleh** mengorbankan hardening reliability/security.
2. Semua perubahan tetap tunduk pada `ATURAN KERAS: JANGAN SAMPAI NYENGGOL YANG LAIN`.
3. Jika butuh lintas file di luar STEP aktif, wajib lewat `SER`.

---

## PRIORITAS EKSEKUSI HEMAT LIMIT (CRITICAL-FIRST)

Section ini adalah override praktis saat budget token terbatas.

Prinsip:
1. Selesaikan yang mencegah bot crash/stop dulu.
2. Lanjut ke yang mencegah kerusakan risiko/keamanan tinggi.
3. Tunda pekerjaan besar yang butuh banyak file sampai runtime stabil.

### WAVE 1 - Runtime Survival (WAJIB PERTAMA)

Target: bot tidak crash, tidak mati karena fatal exit, dan cycle bisa berjalan normal.

Task:
1. Fix `cycle_started` / `now` undefined di `market_discovery_internal/cycles.py`.
2. Fix wiring callback `update_paper_position_fn` di `market_discovery.py`.
3. Ganti `sys.exit(1)` pada fetch failure ke exception terkontrol di `market_discovery_internal/discovery.py`.

Acceptance Gate:
1. Minimal 3 cycle paper berjalan tanpa NameError/crash.
2. Fetch failure transient masuk retry/backoff path, bukan mematikan proses.

### WAVE 2 - Safety & Integrity Core (SEGERA SETELAH WAVE 1)

Target: cegah loss tersembunyi dari security hole dan data integrity drift.

Task:
1. Hardening `/api/kill` (token/auth minimal + guard origin) di `market_discovery_internal/command_server.py`.
2. Enforce minimum stake USD 1.00 (hard skip) di `market_discovery_internal/cycles.py` + `market_discovery_internal/pricing.py`.
3. Perbaiki integritas payload posisi DB (full field restore) di `market_discovery_internal/database_manager.py` + `market_discovery_internal/state_persistence.py`.

Acceptance Gate:
1. Request kill tanpa otorisasi ditolak.
2. Tidak ada entry dengan stake < 1.00.
3. Load/save state mempertahankan shape posisi runtime kritis.

### WAVE 3 - Reliability Cleanup (JIKA LIMIT MASIH CUKUP)

Task:
1. Perbaiki `load_paper_state(path=...)` agar path test/local dihormati.
2. Perbaiki dedupe history agar key trade-level, bukan `token_id` saja.
3. Perbaiki cycle metric duplication (idempotent append).
4. Perbaiki shutdown lifecycle command server.
5. Hardening TLS watcher (`CERT_NONE` hanya untuk debug opt-in).

### WAVE 4 - Deferred Heavy Work (TUNDA SAMPAI STABIL)

Task:
1. Migrasi AI ledger ke SQLite atomic.
2. DB-only state + endpoint UI `/api/state`.
3. Spread-aware alpha penalty + test matrix lengkap.
4. Profit packs A-F (calibration, adaptive gate, sizing advanced, exit advanced, tuner penuh, champion-challenger).

### Prompt Ringkas untuk Eksekusi Hemat Token

Gunakan instruksi ini saat meminta Claude eksekusi:

1. "Kerjakan WAVE 1 saja. Jangan sentuh wave lain."
2. "Setelah patch, jalankan hanya targeted tests terkait file yang diubah."
3. "Laporkan: changed files, alasan, hasil test, dan risk residual dalam format 8 poin template."
4. "Jika butuh file di luar allowlist wave aktif, stop dan keluarkan SER."

---

## EXECUTION CONTRACT FOR CLAUDE (NO RE-ANALYSIS MODE)

Tujuan section ini: menghemat token. Eksekutor langsung implement sesuai urutan di bawah, tanpa audit ulang menyeluruh.

### Scope Wajib
1. Fix semua item `CRITICAL`.
2. Implement semua item `HIGH`.
3. Selesaikan item `MEDIUM` yang berdampak langsung ke runtime (`backtest import` + minimal test coverage untuk bug yang difix).

### Scope Dilarang
1. Jangan buka live trading (`execution.py` tetap dormant).
2. Jangan ubah strategi bisnis di luar poin audit ini.
3. Jangan sentuh konfigurasi secret/token di dokumen publik.

### ATURAN KERAS: JANGAN SAMPAI NYENGGOL YANG LAIN
1. Eksekusi hanya boleh mengubah file yang ada di allowlist STEP aktif.
2. Jika butuh ubah file di luar STEP aktif, wajib stop dan catat alasan; lanjut hanya jika sudah di-approve USER.
3. Dilarang refactor kosmetik (rename massal, format ulang besar, pindah struktur) yang tidak diperlukan untuk fix.
4. Dilarang ubah konfigurasi `.env`, service production, atau endpoint publik kecuali task itu memang eksplisit memintanya.
5. Dilarang mengubah perilaku strategi/threshold bisnis selain yang tercantum di dokumen ini.
6. Setiap STEP wajib menyertakan `Changed Files` dan `Why Needed`; file di luar scope otomatis status STEP = `FAIL`.

### EXPLICIT CROSS-FILE EXCEPTION PROTOCOL (JIKA MEMANG WAJIB)
1. Jika root cause terbukti butuh perubahan di file lain, buat tiket `Scoped Exception Request (SER)` sebelum edit.
2. SER wajib berisi: file tambahan yang dibutuhkan, alasan teknis, dampak, dan batas perubahan.
3. SER wajib disetujui USER sebelum file tambahan disentuh.
4. Setelah disetujui, file tambahan masuk allowlist sementara hanya untuk STEP aktif.
5. Semua perubahan exception harus minimal, reversible, dan disertai test/evidence yang relevan.
6. Setelah STEP selesai, allowlist exception ditutup lagi (kembali strict).

### CHANGE ISOLATION GATE (WAJIB LULUS PER STEP)
1. `git diff --name-only` hanya berisi file target STEP + file exception yang sudah disetujui via SER.
2. Tidak ada perubahan pada modul non-target.
3. Tidak ada API contract drift yang tidak disebutkan di task.
4. Jika gate gagal: rollback perubahan STEP tersebut, lalu ulang implementasi lebih sempit.

---

## IMPLEMENTATION ORDER (WAJIB BERURUTAN)

### STEP 0 - Safety Baseline
1. Buat branch kerja.
2. Jalankan smoke check awal (import + test baseline singkat).
3. Catat hash commit awal.

### STEP 1 - Crash Blocker (`now` Undefined)
**File**: `market_discovery_internal/cycles.py`

Action:
1. Gunakan satu variabel waktu konsisten, contoh: `now_utc_cycle`.
2. Ganti semua `now_utc=now` menjadi `now_utc=now_utc_cycle`.
3. Ganti `last_date < now.date()` menjadi `last_date < now_utc_cycle.date()`.
4. Pastikan `last_cycle_at` dan `updated_at` memakai variabel yang sama.

Done Criteria:
1. Tidak ada referensi variabel `now` yang undefined pada file ini.
2. Paper cycle bisa jalan tanpa NameError.

### STEP 2 - AI Ledger Migration ke SQLite (Atomic)
**Files**: `market_discovery_internal/database_manager.py`, `market_discovery_internal/analysis.py`

Action:
1. Tambahkan tabel ledger AI di SQLite.
2. Tambahkan API DB atomic untuk:
	- reserve slot call per day + call_kind
	- add cost per month
3. Refactor analysis agar tidak lagi read/write `ai_usage_ledger.json` untuk guard utama.
4. File JSON bisa dijadikan optional debug mirror, bukan source of truth.

Done Criteria:
1. Guard budget dan daily call limit dibaca dari DB.
2. Tidak ada race window read-modify-write berbasis JSON.

### STEP 3 - Minimum Stake Floor USD 1.00
**Files**: `market_discovery_internal/config.py`, `market_discovery_internal/cycles.py`, `market_discovery_internal/pricing.py`

Action:
1. Tambah konstanta `MIN_STAKE_THRESHOLD = 1.0`.
2. Setelah `calculate_depth_adjusted_stake`, lakukan hard check:
	- jika `< 1.0`, `SKIP` dengan log jelas.
3. Pastikan stake final yang dipakai `build_paper_position` tidak bisa di bawah floor.

Done Criteria:
1. Tidak ada order simulation/entry dengan stake `< 1.0`.
2. Dust filtering test lulus.

### STEP 4 - Alpha Penalty Jadi Spread-Aware
**File**: `market_discovery_internal/pricing.py`

Action:
1. Refactor `calculate_edge` untuk menerima spread context jika tersedia.
2. Gunakan penalti adaptif dari spread real-time (dengan floor 1.75% agar tetap konservatif).
3. Dokumentasikan formula singkat di komentar fungsi.

Done Criteria:
1. `calculate_edge` tidak hanya bergantung ke angka statis.
2. Unit test untuk kasus spread sempit vs lebar tersedia.

### STEP 5 - DB-Only State (Hapus Dependensi JSON sebagai Truth)
**Files**: `market_discovery_internal/state_persistence.py`, `market_discovery_internal/command_server.py`, `web_ui/index.html`

Action:
1. Jadikan SQLite satu-satunya source of truth.
2. Tambahkan endpoint state read dari DB untuk UI (`/api/state`) agar UI tidak bergantung file JSON.
3. Update UI fetch path ke endpoint state baru.
4. Matikan write JSON mirror pada path utama.

Done Criteria:
1. Bot tetap jalan walau file JSON lama tidak ada.
2. UI tetap menampilkan data terbaru dari DB.

### STEP 6 - Script Path Hardening
**Files**: `scripts/run_cycle.sh`, `setup_and_legacy/run_paper_5usd.sh`

Action:
1. Sinkronkan path runner agar valid di struktur repo saat ini.
2. Hindari asumsi `script_dir/market_discovery.py` jika file ada di root project.
3. Tambah guard error message yang eksplisit jika path tidak valid.

Done Criteria:
1. `run_cycle.sh` bisa memanggil runner tanpa path error.
2. Runner bisa menemukan `market_discovery.py` secara deterministik.

### STEP 7 - Backtest Import Fix
**File**: `market_discovery_internal/backtest_runner.py`

Action:
1. Perbaiki import `calculate_edge` ke modul yang benar.
2. Jalankan smoke test backtest minimal 1 run.

Done Criteria:
1. Backtest command tidak gagal karena import error.

---

## MEMORY WATCHDOG REQUIREMENT (Dari Plan Awal)

Tambahkan watchdog memory di cycle orchestrator:
1. Cek RSS memory proses tiap cycle.
2. Jika > 850MB, kirim alert Telegram (one-shot per periode agar tidak spam).
3. Simpan flag alert di state meta untuk anti-spam.

---

## MINIMAL TEST PLAN (WAJIB)

1. Test crash-path cycle setelah fix `now`.
2. Test AI ledger atomic reserve/add-cost (simulasi concurrent calls).
3. Test dust filtering (`< 1.0` harus skip).
4. Test spread-aware edge.
5. Test state load/save DB-only + endpoint UI state.
6. Test runner path resolved.
7. Test backtest import smoke.

---

## SERVER VERIFICATION PACK (Untuk Eksekusi via SSH)

Jalankan setelah implementasi selesai:

1. `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "hostname; date -u; cd /opt/the_blueprints && git rev-parse --short HEAD"`
2. `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "systemctl status blueprints.service blueprints-bot.service blueprints-paper-loop.service --no-pager --lines=0 || true"`
3. `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "ps -ef | egrep 'market_discovery.py|PriceWatcherProcess|http.server' | grep -v grep"`
4. `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "ss -tlnp | egrep ':8080|:8082|:8083'"`
5. `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "nginx -T 2>/dev/null | egrep -n 'location /ws|location /api/kill|proxy_pass|listen 8080'"`
6. `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "cd /opt/the_blueprints && egrep '^(PAPER_LOOP_INTERVAL_SECONDS|DAILY_RESOLVE_ONLY|PAPER_ENTRY_MAX_PRICE|STRATEGY_MAX_YES_PRICE|WS_BROADCAST_PORT|COMMAND_SERVER_PORT|HAIKU_MONITOR_ENABLED|HAIKU_SENSING_ENABLED)=' .env"`
7. `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "cd /opt/the_blueprints && stat -c '%y %n' logs/paper_positions_5usd.json; tail -n 120 logs/paper_loop.out"`
8. `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "cd /opt/the_blueprints && sha256sum market_discovery.py market_discovery_internal/cycles.py market_discovery_internal/parsing.py market_discovery_internal/pricing.py market_discovery_internal/state_persistence.py"`

---

## FINAL DEFINITION OF DONE

1. Semua item `CRITICAL` dan `HIGH` selesai.
2. Test plan minimal lulus.
3. Verifikasi SSH pack menunjukkan service sehat dan parity sesuai.
4. Tidak ada regressions pada paper loop, UI, kill-switch, dan persistence.

---

## FLOW-DECOMPOSED TASK BOARD (ANTI-DRIFT, EXECUTOR READY)

Gunakan task board ini sebagai sumber tracking tunggal saat eksekusi. Jangan lompat urutan.

### F0 - Governance & Baseline Lock
**Goal**: mencegah drift scope dan mencegah regresi tersembunyi.

Tasks:
1. Freeze scope ke item di dokumen ini.
2. Catat baseline commit, branch kerja, dan hasil smoke test awal.
3. Aktifkan checklist pass/fail per task sebelum pindah task berikutnya.

Pass Criteria:
1. Ada log eksekusi: baseline commit + branch + test awal.
2. Tidak ada perubahan di luar file target task.

### F1 - Bootstrap, Lock, Loop Start
**Area**: startup guard, PID lock, loop initialization.

Tasks:
1. Fix bug `cycle_started` di `run_paper_trading_cycle`.
2. Fix seluruh referensi waktu yang memakai variabel undefined (`now`).
3. Pastikan `last_cycle_at` dan `updated_at` memakai variabel waktu konsisten.

Pass Criteria:
1. Tidak ada NameError pada startup/cycle.
2. Paper loop menyelesaikan minimal 1 cycle tanpa exception.

### F2 - Discovery Pipeline (Fetch -> Parse -> Enrich -> Filter)
**Area**: market discovery dan gating awal.

Tasks:
1. Verifikasi parse skip reasons tetap terlapor setelah fix.
2. Pastikan daily gate dan Golden Window tetap deterministic.
3. Pastikan aggressive fallback tidak mem-bypass filter inti.

Pass Criteria:
1. Counter discovery tetap terisi normal (raw/parsed/enriched/opportunities).
2. Tidak ada perubahan perilaku bisnis yang tidak diminta.

### F3 - Forecast/Evidence Integrity
**Area**: forecast cache, evidence validity, anomaly gate.

Tasks:
1. Pastikan evidence validation tidak broken oleh refactor lain.
2. Pastikan warmer heartbeat tetap terbaca di cycle.
3. Pastikan fail path forecast tetap graceful (skip, bukan crash).

Pass Criteria:
1. Forecast miss/fail menghasilkan skip aman.
2. Tidak ada crash saat cache kosong.

### F4 - Pricing, Edge, Liquidity
**Area**: stake sizing, edge calc, slippage realism.

Tasks:
1. Tambahkan `MIN_STAKE_THRESHOLD = 1.0` dan enforce hard skip.
2. Ubah slippage penalty jadi spread-aware (dengan floor konservatif 1.75%).
3. Pastikan kalkulasi stake tidak bisa menghasilkan entry < floor.

Pass Criteria:
1. Semua candidate dengan stake < 1.0 di-skip.
2. Edge berubah sesuai spread input, bukan konstanta statis saja.

### F5 - Position Management & Exit
**Area**: sniper exit, hybrid exit, close flow.

Tasks:
1. Verifikasi semua jalur close memakai timestamp valid.
2. Verifikasi close reason tetap terbentuk benar.
3. Verifikasi forced-close path tidak menyebabkan crash.

Pass Criteria:
1. Position close berjalan normal di semua branch keputusan.
2. History update konsisten.

### F6 - Persistence (DB-Only Migration)
**Area**: state read/write, source of truth.

Tasks:
1. Migrasikan ledger AI ke SQLite atomic.
2. Hapus ketergantungan JSON sebagai source of truth state.
3. Jika JSON tetap dipakai sementara, jadikan explicit `mirror-only` non-authoritative.

Pass Criteria:
1. Budget guard AI dibaca dari DB.
2. State utama berasal dari DB.

### F7 - Realtime Services (WS + Command Server)
**Area**: ws watcher, ws broadcaster, kill endpoint.

Tasks:
1. Pastikan broadcaster tidak spam exception handshake.
2. Tambah hardening kill endpoint (auth/token) bila belum ada.
3. Pastikan stale ws fallback tidak melanggar cadence discipline.

Pass Criteria:
1. WS errors tidak mematikan loop.
2. Kill endpoint tidak bisa dipanggil sembarang origin tanpa proteksi.

### F8 - UI & API Contract
**Area**: dashboard data source, ws rendering, kill action.

Tasks:
1. Migrasi UI ke endpoint state DB (bila DB-only diaktifkan).
2. Verifikasi card summary, table posisi, cycle journal sinkron.
3. Verifikasi kill button tetap bekerja setelah perubahan API.

Pass Criteria:
1. UI load normal dengan data real.
2. Tidak ada drift antara angka summary dan journal.

### F9 - Telegram & Alert Reliability
**Area**: alert anti-spam, memory watchdog.

Tasks:
1. Implement memory watchdog > 850MB (one-shot per periode).
2. Pastikan alert close/entry/circuit-breaker tetap keluar.
3. Pastikan error alert tidak membuat deadlock loop.

Pass Criteria:
1. Alert terkirim sesuai trigger.
2. Tidak terjadi spam berulang tanpa cooldown.

### F10 - Scheduler/Service/Deploy Path
**Area**: systemd, cron script, runner path.

Tasks:
1. Sinkronkan path `run_cycle.sh` dengan lokasi runner nyata.
2. Validasi service name tunggal yang benar (`blueprints.service` vs lainnya).
3. Pastikan pre_start dan healthcheck konsisten dengan struktur final.

Pass Criteria:
1. Tidak ada path-not-found pada scheduler.
2. Satu service utama berjalan stabil.

### F11 - Security & Secret Hygiene
**Area**: endpoint exposure, config hygiene.

Tasks:
1. Pastikan `.env` tidak pernah diekspos ke UI/static route.
2. Pastikan kill endpoint tidak open-by-default tanpa guard.
3. Catat jika perlu rotasi token setelah hardening.

Pass Criteria:
1. Tidak ada endpoint sensitif terbuka publik tanpa proteksi.

### F12 - Test Harness & Release Gate
**Area**: regression proof.

Tasks:
1. Tambah tests untuk bug yang difix (bukan hanya happy path).
2. Jalankan targeted tests lalu full suite.
3. Jalankan smoke run paper cycle + report + UI check.

Pass Criteria:
1. Semua test gate lulus.
2. Tidak ada regresi pada alur utama.

---

## END-TO-END SIMULATION MATRIX (WAJIB DIJALANKAN)

1. **S1 Boot Clean**
- Kondisi: state kosong, tanpa posisi open.
- Ekspektasi: loop start normal, no crash, discovery metrics terisi.

2. **S2 Open Position Lifecycle**
- Kondisi: minimal 1 posisi open.
- Ekspektasi: ws update masuk, evaluasi exit jalan, close tercatat ke history.

3. **S3 Dust Liquidity**
- Kondisi: depth menghasilkan stake < 1.0.
- Ekspektasi: candidate di-skip, tidak ada entry dust.

4. **S4 AI Budget Saturation**
- Kondisi: cap AI tercapai.
- Ekspektasi: reserve call ditolak aman, bot tetap jalan tanpa over-spend.

5. **S5 Kill Switch**
- Kondisi: trigger `/api/kill`.
- Ekspektasi: loop berhenti bersih, state aman.

6. **S6 Restart Recovery**
- Kondisi: service restart paksa.
- Ekspektasi: state tetap konsisten, tidak ada kehilangan posisi/history.

7. **S7 WS Degraded**
- Kondisi: ws stale/handshake error.
- Ekspektasi: fallback interval aktif, cycle tetap berjalan.

---

## 10X RECHECK PROTOCOL (MANDATORY)

Aturan keras:
1. Jalankan 10 putaran verifikasi berurutan.
2. Jika 1 putaran gagal, lakukan fix lalu ulang lagi dari putaran 1.
3. Release hanya boleh dilakukan jika 10/10 PASS tanpa exception.

Putaran:
1. Static check (import, lint dasar, syntax check).
2. Unit tests untuk bug fix utama (`cycle_started/now`, min stake, edge spread).
3. Concurrency test ledger AI (simulasi call paralel).
4. Persistence test (restart recovery, DB-only state integrity).
5. Discovery-to-entry simulation (happy path).
6. Exit/close simulation (sniper + hybrid + forced-close).
7. WS degraded simulation (stale/handshake fail fallback).
8. UI contract verification (`/ws`, `/api/state`, `/api/kill`).
9. Telegram alert reliability + anti-spam check.
10. VPS parity + service health + log cleanliness check.

Evidence per putaran:
1. Command/test yang dijalankan.
2. Output ringkas PASS/FAIL.
3. Jika FAIL: root cause + fix reference + rerun result.

---

## VPS MIRROR SNAPSHOT (UPDATED 2026-04-17)

Snapshot ini hasil cek read-only terbaru untuk sinkronisasi lokal-vps.

### Mirror Status
1. **Code parity: MATCH**
- Local git: `bd0a08a`
- VPS git: `bd0a08a`
- Hash file kritikal sama (`market_discovery.py`, `cycles.py`, `parsing.py`, `pricing.py`, `state_persistence.py`).

2. **Runtime health: NOT OK (degraded)**
- Service aktif: `blueprints.service`.
- Service `blueprints-paper-loop.service`: not-found.
- Error loop berulang di log: `name 'cycle_started' is not defined`.

3. **Network plumbing: OK**
- Nginx listen `8080`.
- Proxy `/ws -> 127.0.0.1:8082`.
- Proxy `/api/kill -> 127.0.0.1:8083`.

4. **Config parity (sample): MATCH**
- `STRATEGY_MAX_YES_PRICE=0.33`
- `DAILY_RESOLVE_ONLY=true`
- `HAIKU_MONITOR_ENABLED=true`
- `HAIKU_SENSING_ENABLED=true`

5. **Audit doc mirror: MATCH**
- File tersinkron ke VPS: `/opt/the_blueprints/blueprints_core_docs/AUDIT_HARDENING_PHASE_3.5.md`
- Verifikasi checksum local vs VPS: **MATCH** (dijalankan via `sha256sum` pada kedua path).
- Catatan: nilai hash tidak di-hardcode di file ini untuk menghindari drift self-referential.

### Immediate Decision
Sebelum tuning lain, **wajib** bereskan blocker runtime (`cycle_started`/`now`) karena VPS saat ini sedang retry loop dan tidak sehat untuk validasi strategi lanjutan.

---

## EXECUTION LOG TEMPLATE (ISI SAAT EKSEKUSI)

Gunakan format ini agar tracking rapi dan hemat token:

1. Task ID:
2. File yang diubah:
3. Problem statement (1-2 kalimat):
4. Perubahan inti (maks 5 poin):
5. Test yang dijalankan:
6. Hasil test:
7. Dampak ke flow lain:
8. Status: PASS/FAIL/BLOCKED
