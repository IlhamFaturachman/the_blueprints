# Post Paper Trade Strategy — The Blueprints
*Ditulis: 2026-04-18 | Paper trade periode: ~7 hari (Apr 18–25)*

---

## Konteks

Bot menjalankan paper trade selama 7 hari dengan konfigurasi:
- Modal: $5 USD (simulasi)
- Stake per posisi: sesuai config PAPER_STAKE_USD
- STRATEGY_MIN_MODEL_PROB: 0.60 (diturunkan dari 0.70 untuk mengumpulkan data)
- STRATEGY_MIN_EDGE: 0.20
- Target kota: 5 kota per siklus
- Golden window: 05:00–11:00 WIB (8–14 jam sebelum resolve jam 19:00 WIB)

---

## Langkah 1: Analisis Data (Hari ke-8, langsung setelah paper trade)

### 1.1 Cek Win Rate Per Kota
Query dari `trade_history` di SQLite (`logs/blueprints_master.db`):
```sql
SELECT city,
       COUNT(*) as total_trades,
       SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
       ROUND(AVG(pnl_usd), 4) as avg_pnl,
       ROUND(SUM(pnl_usd), 4) as total_pnl
FROM trade_history
GROUP BY city
ORDER BY total_pnl DESC;
```
**Yang dicari:** Kota mana yang konsisten profit. Kota dengan win rate <40% dan avg PnL negatif → kandidat blacklist.

### 1.2 Cek Model Accuracy (Calibration)
```sql
SELECT city, direction, horizon_bin,
       hits, total,
       ROUND(CAST(hits AS FLOAT)/total, 3) as actual_win_rate
FROM calibration_stats
WHERE total >= 3
ORDER BY actual_win_rate DESC;
```
**Yang dicari:** Apakah model 60% confident → actual win rate ≈ 60%? Kalau model 60% tapi actual cuma 40% → model overconfident, perlu naikkan threshold.

### 1.3 Cek Exit Reason Distribution
```sql
SELECT close_reason, COUNT(*) as count,
       ROUND(AVG(pnl_usd), 4) as avg_pnl
FROM trade_history
GROUP BY close_reason;
```
**Yang dicari:** Berapa % hit take profit vs stop loss vs expired. Kalau stop loss dominan → entry terlalu agresif.

### 1.4 Cek Timing — Apakah Golden Window Optimal
Lihat `opened_at` dari trade history. Apakah trades yang dibuka mendekati jam 05:00 WIB lebih profitable dari yang dibuka jam 10:00 WIB? Ini menentukan apakah window perlu digeser.

---

## Langkah 2: Keputusan Threshold (Berdasarkan Data)

### Skenario A: Win rate ≥ 55% dan model calibrated
→ **STRATEGY_MIN_MODEL_PROB tetap 0.60** — model sudah bagus, lanjut ke live kecil.

### Skenario B: Win rate 45–55%, model sedikit overconfident
→ **Naikkan ke 0.62–0.65** dan pastikan calibration_stats sudah punya data cukup.

### Skenario C: Win rate < 45% atau data terlalu sedikit (<15 trades total)
→ **Perpanjang paper trade 7 hari lagi** dengan threshold yang sama. Jangan terburu-buru ke live.

### Skenario D: Win rate > 65% konsisten
→ Model bagus, bisa pertimbangkan **turunkan STRATEGY_MIN_EDGE ke 0.15** untuk ambil lebih banyak opportunities.

---

## Langkah 3: Implementasi Auto-Tuner (adj_edge/adj_prob)

Saat ini `adj_edge` dan `adj_prob` dihitung tapi tidak dipakai. Setelah punya ≥3 trades per kota:

**Yang perlu di-implement:**
- Pass `auto_tuner_state` ke `build_entry_candidates()`
- Di dalam loop opportunity, lookup `auto_tuner_state.get(city, {})`
- Apply: `effective_min_edge = STRATEGY_MIN_EDGE + adj.get("adj_edge", 0.0)`
- Apply: `effective_min_prob = STRATEGY_MIN_MODEL_PROB + adj.get("adj_prob", 0.0)`

Estimasi effort: ~30 menit, 1 fungsi diubah.

---

## Langkah 4: Live Trading — Syarat Sebelum Deploy

Semua harus terpenuhi sebelum switch ke real money:

- [ ] ≥15 closed paper trades total
- [ ] Win rate ≥ 50% di paper trade
- [ ] Calibration_stats punya data di ≥3 kota
- [ ] Tidak ada bug kritis dalam 3 hari terakhir paper trade
- [ ] Auto-tuner adj_edge sudah diimplementasi
- [ ] Bot jalan stabil 24 jam tanpa restart manual

**Modal live awal yang disarankan: $10–$20 USD** — cukup untuk beberapa trade nyata tanpa risiko besar.

---

## Langkah 5: Optimasi Lanjutan (Setelah Live Trading Stabil)

Prioritas berurutan setelah live trading berjalan:

1. **Airport coordinate fix** — verifikasi Open-Meteo dipanggil dengan koordinat airport exact, bukan kota center (lihat `GROK_IMPROVEMENT_PLAN.md` Fix #1) ← PALING PENTING
2. **Forecast ensemble** — tambah Open-Meteo Ensemble 31-member GFS sebagai sumber ke-3 (lihat `GROK_IMPROVEMENT_PLAN.md` Fix #2)
3. **NOAA/METAR integration** — data observasi airport yang sama dengan yang Polymarket pakai untuk resolve (lihat `GROK_IMPROVEMENT_PLAN.md` Fix #3)
4. **Dynamic stake sizing (Kelly Criterion)** — stake lebih besar saat edge tinggi, lebih kecil saat tipis
5. **Haiku position monitor review** — apakah AI exit decision membantu atau malah cut profit terlalu awal
6. **Perluas kota** — tambah kota baru yang sering muncul di Polymarket tapi belum di TARGET_CITIES
7. **Backtest otomatis** — jalankan `run_backtest()` setiap minggu untuk validasi model terus-menerus

> Detail lengkap analisis kompetitor, pseudocode, dan prioritas: lihat `GROK_IMPROVEMENT_PLAN.md`

---

## Checklist Ringkas (Print & Tempel)

```
SETELAH 7 HARI PAPER TRADE:
[ ] Query win rate per kota (Langkah 1.1)
[ ] Query calibration accuracy (Langkah 1.2)
[ ] Query exit reasons (Langkah 1.3)
[ ] Tentukan skenario (A/B/C/D) dari Langkah 2
[ ] Implement adj_edge/adj_prob (Langkah 3)
[ ] Checklist live trading (Langkah 4)
[ ] Deploy live jika semua syarat terpenuhi
```

---

## Skenario Darurat: Masih 0 Opps Besok Pagi (05:00–11:00 WIB)

Kalau setelah golden window besok bot masih 0 opportunities, lakukan investigasi berurutan:

### Cek 1: Apakah forecast berhasil?
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
"tail -50 /opt/the_blueprints/logs/paper_loop.out | grep -E 'MODUL K|forecast|Prefetch|slots'"
```
Yang dicari: `Prefetching forecasts for X unique slots` — kalau X = 0 terus, berarti parsing market gagal atau semua market di-skip (too_early/too_close).

### Cek 2: Apakah ada market yang di-parse?
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
"tail -20 /opt/the_blueprints/logs/unmatched_markets.log-20260419"
```
Kalau log penuh → market tidak match kota target. Kalau kosong → parsing OK tapi filter lain yang kill.

### Cek 3: Apakah edge cukup?
Tambahkan log sementara atau jalankan inspect mode:
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
"cd /opt/the_blueprints && venv/bin/python3 market_discovery.py --inspect"
```
Lihat edge dan model_prob yang dihasilkan per market. Kalau semua edge < 0.20 → threshold perlu diturunkan lagi.

### Jika Cek 1–3 semua OK tapi tetap 0 opps → Phase 1 Improvements

Urutan implementasi (dari paling cepat dan aman):

**A. Turunkan STRATEGY_MIN_EDGE dari 0.20 → 0.15**
Di `config.py` line ~300. Estimasi effort: 2 menit. Langsung push + pull server.
Tradeoff: bot masuk ke trade yang tipis edge-nya, win rate mungkin lebih rendah.

**B. Per-region sigma untuk model Gaussian (exact markets)**
Sekarang `MODEL_EXACT_SIGMA_C = 1.5` flat semua kota.
Kota tropis (Singapore, KL, Jakarta, Bangkok) → cuaca lebih stabil → sigma 1.0
Kota 4 musim (London, NYC, Toronto, Seoul) → lebih unpredictable → sigma 2.0
Estimasi effort: ~20 menit, ubah 1 fungsi di `pricing.py`.

**C. Time-of-day confidence weighting**
Jam 05:00–08:00 WIB (14–11h sebelum resolve) → model masih uncertain → edge threshold lebih longgar
Jam 08:00–11:00 WIB (11–8h sebelum resolve) → forecast lebih akurat → bisa pakai threshold normal
Estimasi effort: ~30 menit, tambah parameter ke `calculate_edge()`.

**D. Tambah sumber forecast independen (Tomorrow.io)**
Butuh API key gratis di tomorrow.io, lalu tambah sebagai sumber ketiga.
Kalau 3 sumber sepakat → confidence naik, lebih banyak opportunities lolos.
Estimasi effort: ~1 jam, butuh testing.

---

*Dokumen ini dibuat berdasarkan analisis arsitektur bot, data Polymarket aktif, dan pattern market discovery per 2026-04-18.*
