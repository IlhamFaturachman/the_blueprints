# Next Steps Tracker — The Blueprints
*Dibuat: 19 April 2026 | Update terakhir: 19 April 2026 (rev Grok v2)*

---

## Status Saat Ini

✅ **Fix #1 SELESAI (19 Apr)** — Airport coordinates fix + wttr.in by ICAO
⏳ **Phase 1 Paper Trade** — Berjalan 19–23 April 2026 (5 hari)
🔜 **Fix #2** — Open-Meteo Ensemble, mulai 24 April setelah golden window tutup

---

## Ringkasan Besar — Apa yang Ingin Dicapai

Berdasarkan analisis Grok vs kompetitor bot temperature Polymarket, bot The Blueprints sudah unggul di **risk management** tapi masih kalah di **kualitas probabilitas** dan **presisi resolution**. Roadmap ini secara berurutan memperbaiki kelemahan itu sambil menjaga stabilitas.

Target akhir Phase 3: bot dengan probabilitas sekelas kompetitor profit $24k–$65k, tapi dengan risk management yang jauh lebih matang.

---

## Timeline Master

| Tanggal | Status | Kegiatan |
|---|---|---|
| 19 Apr | ✅ | Fix #1 deployed — airport coords + wttr.in ICAO |
| 19–23 Apr | ⏳ | Phase 1 paper trade — jangan ubah kode apapun |
| 24 Apr pagi | 🔜 | Evaluasi Phase 1 → Implement Fix #2 (shadow mode) |
| 25–26 Apr | 🔜 | Fix #2 full activation setelah shadow OK |
| 26 Apr – 2 Mei | 🔜 | Phase 2 paper trade (7 hari) dengan ensemble aktif |
| ~3 Mei | 🔜 | Evaluasi Phase 2 → Fix #3 (NOAA/METAR) |
| ~5 Mei | 🔜 | Fix #4 (Per-region sigma + Auto-tuner) |
| ~10 Mei | 🔜 | Fix #5 (Kelly Criterion) — implement + shadow live 1 minggu |
| ~17 Mei | 🔜 | Phase 3: Live trading $5 real money (setelah shadow Kelly OK) |

---

## Phase 1: Paper Trade (19–23 April) — JANGAN UBAH KODE

Pantau tiap pagi jam 05:00–11:00 WIB. Biarkan data mengalir.

**Evaluasi wajib di 24 April sebelum Fix #2:**

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
"cd /opt/the_blueprints && python3 -c \"
import sqlite3
conn = sqlite3.connect('blueprints_master.db')

print('=== WIN RATE PER KOTA ===')
rows = conn.execute('''
  SELECT city, COUNT(*) total,
         SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) wins,
         ROUND(AVG(pnl_usd), 4) avg_pnl
  FROM trade_history GROUP BY city ORDER BY avg_pnl DESC
''').fetchall()
for r in rows: print(r)

print()
print('=== EXIT REASONS ===')
rows = conn.execute('''
  SELECT close_reason, COUNT(*) count, ROUND(AVG(pnl_usd),4) avg_pnl
  FROM trade_history GROUP BY close_reason
''').fetchall()
for r in rows: print(r)

print()
print('=== TOTAL TRADES & PNL ===')
rows = conn.execute('''
  SELECT COUNT(*) total_trades,
         SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) wins,
         ROUND(SUM(pnl_usd), 4) total_pnl
  FROM trade_history
''').fetchall()
for r in rows: print(r)
conn.close()
\""
```

**Keputusan dari data:**
- Win rate ≥ 55% → lanjut ke Phase 2 normal
- Win rate 45–55% → lanjut tapi naikkan threshold ke 0.62
- Win rate < 45% atau < 10 trades → perpanjang paper 7 hari lagi, tunda Fix #2

---

## Fix #2: Open-Meteo Ensemble (24 April)

**Kenapa:** Point forecast (1 angka) tidak bisa representasikan uncertainty cuaca ±2–3°C. Ensemble 31-member memberikan distribusi probabilitas yang jauh lebih akurat — ini yang kompetitor profit besar pakai.

**Detail lengkap:** lihat `FIX#2.md`

**Singkatnya:**
- Tambah `ENSEMBLE_CONFIG` di `config.py` (endpoint beda: `ensemble-api.open-meteo.com`)
- Tambah `_fetch_openmeteo_ensemble()` di `forecasting.py`
- Update `calculate_edge()` di `pricing.py` → hybrid weighted (ensemble 45% + point 35% + wttr 20%)
- Deploy shadow mode dulu (`enabled=False`), full activation 25–26 April

**Effort:** ~2–3 jam
**Impact:** Tinggi — probabilitas jauh lebih akurat, k-factor sigmoid tidak perlu di-tune lagi

---

## Fix #3: NOAA/METAR Integration (~3 Mei)

**Kenapa:** Polymarket resolve market pakai Wunderground dari airport station. Fix #1 sudah fix koordinat, tapi forecast masih prediksi masa depan. NOAA/METAR memberikan **observasi real-time** dari station yang sama — ini validasi ground truth terkuat.

**Yang dilakukan:**
- Sudah ada `_fetch_noaa()` di `forecasting.py` — tapi hanya dipakai sebagai anomaly check, bukan sebagai sumber probabilitas
- Upgrade: jadikan NOAA sebagai **confirmation/override** saat < 6 jam sebelum resolve (bukan di-average dengan ensemble)
- NOAA = observasi real-time, bukan forecast → logic-nya berbeda:
  - Kalau NOAA sudah baca 29°C dan threshold 28°C → prob override ke 95%+ (confirmed)
  - Kalau NOAA jauh dari semua forecast → trigger **Sniper Stop Loss** lebih awal (exit defensif)
- Jangan rata-ratakan NOAA dengan ensemble — gunakan sebagai final arbiter di jam terakhir

**File:** `market_discovery_internal/forecasting.py`
**Effort:** ~1–2 jam (fondasi sudah ada)
**Impact:** Tinggi — eliminasi uncertainty di jam-jam terakhir + exit lebih cepat kalau kondisi memburuk

---

## Fix #4: Per-Region Sigma + Auto-Tuner Adj_Edge (~5 Mei)

### Fix #4a: Per-Region Sigma (Gaussian untuk "persis X°C" bucket)

**Kenapa:** Sekarang `MODEL_EXACT_SIGMA_C = 1.5` flat untuk semua kota. Kota tropis (Singapore, HK, Lucknow) cuaca lebih stabil → sigma 1.0 lebih akurat. Kota 4 musim (London, NYC, Chicago) lebih unpredictable → sigma 2.0 lebih tepat.

**Yang dilakukan:**
```python
# Di pricing.py, ganti flat sigma dengan lookup per kota:
CITY_SIGMA = {
    # Tropis stabil → sigma kecil (cuaca konsisten)
    "singapore": 1.0, "hong kong": 1.0, "lucknow": 1.0,
    "miami": 1.0, "bangkok": 1.0,
    # 4 musim volatile → sigma besar (cuaca unpredictable)
    "london": 2.0, "new york city": 2.0, "chicago": 2.0,
    "paris": 2.0, "toronto": 2.0, "seoul": 2.0,
    # default: 1.5
}
```

**File:** `market_discovery_internal/pricing.py`
**Effort:** ~20 menit

### Fix #4b: Auto-Tuner Adj_Edge Aktif

**Kenapa:** `adj_edge` dan `adj_prob` sudah dihitung per kota tapi belum dipakai di entry filter. Setelah Phase 2 punya ≥ 3 trades per kota, auto-tuner bisa mulai memperketat/melonggarkan threshold per kota secara otomatis.

**Yang dilakukan:**
- Pass `auto_tuner_state` ke `build_entry_candidates()` di `cycles.py`
- Apply `effective_min_edge = STRATEGY_MIN_EDGE + adj.get("adj_edge", 0.0)` per opportunity

**File:** `market_discovery_internal/cycles.py`
**Effort:** ~30 menit (logika sudah ada, tinggal wire)
**Impact:** Medium-tinggi — bot otomatis lebih agresif di kota yang track record bagus

---

## Fix #5: Kelly Criterion — Stake Dinamis (~10 Mei, awal Phase 3)

**Kenapa:** Fixed $1 per posisi tidak optimal. Saat edge besar (40%+) harusnya bet lebih besar, saat edge tipis (20%) bet lebih kecil. Kompetitor pakai fractional Kelly (0.15–0.25) untuk scale profit lebih cepat.

**Formula:**
```python
# Kelly fraction:
# f = (edge * win_prob) / (1 - win_prob)
# Fractional Kelly (lebih conservative):
# stake = bankroll * f * 0.20  # 20% dari full Kelly

def kelly_stake(bankroll, model_prob, yes_price, fraction=0.20):
    edge = model_prob - yes_price
    if edge <= 0: return MIN_STAKE
    win_prob = model_prob
    kelly_f = (edge * win_prob) / (1 - win_prob)
    raw_stake = bankroll * kelly_f * fraction
    return max(MIN_STAKE, min(MAX_STAKE, round(raw_stake, 2)))
```

**Catatan:** Hanya aktif di Phase 3 (live trading). Di paper trade tetap fixed $1.

**Gate wajib setelah implement Kelly:**
- Jalankan **1 minggu shadow live** (Kelly aktif tapi masih paper money) sebelum masuk real money
- Monitor apakah Kelly stake sizing masuk akal di real market condition
- Baru switch ke real money kalau stake distribution normal (tidak ada spike aneh)

**File:** `market_discovery_internal/cycles.py`
**Effort:** ~1 jam + testing kritis + 1 minggu shadow observasi

---

## Phase 3: Live Trading ($5 Real Money, ~17 Mei)

**Syarat sebelum switch ke real money (semua harus terpenuhi):**
- [ ] ≥ 20 closed paper trades total
- [ ] Win rate ≥ 50% di paper trade
- [ ] Fix #2 (ensemble) sudah berjalan ≥ 5 hari tanpa masalah
- [ ] Tidak ada bug kritis dalam 3 hari terakhir
- [ ] Auto-tuner adj_edge sudah aktif
- [ ] Kelly Criterion sudah di-implement dan di-test
- [ ] **Kelly shadow live 1 minggu selesai** — stake sizing normal, tidak ada anomali

**Modal live awal:** $5 USD (sama dengan paper, untuk calibrate Kelly)

---

## Backlog Jauh — Setelah Phase 3 Stabil

Ini tidak ada deadline — dikerjakan kalau bot sudah stabil dan profit konsisten:

1. **Backtest mingguan otomatis** — jalankan `run_backtest()` setiap minggu, alert kalau model drift
2. **Perluas kota** — tambah kota baru yang sering muncul di Polymarket (cek tiap bulan)
3. **Haiku ensemble update** — tambah `ensemble_spread` ke Haiku prompt untuk deteksi drift lebih akurat
4. **DB migration** — tambah kolom `ensemble_prob`, `ensemble_members_count` di `trade_history` untuk analytics
5. **Kalshi multi-platform** — jauh ke depan, beda platform, beda compliance requirements

---

## Catatan Penting untuk Sesi Berikutnya

| Info | Value |
|---|---|
| Golden window | 05:00–11:00 WIB setiap hari |
| Resolve time | 19:00 WIB setiap hari |
| Modal paper | $5.00 (reset 19 April) |
| Stake per posisi | $1.00 exact (cost + fee) |
| Server SSH | `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158` |
| Log utama | `tail -f /opt/the_blueprints/logs/paper_loop.out` |
| Dashboard | `http://103.253.244.158:8080/web_ui/` |
| Fix #2 detail | `FIX#2.md` |
| Analisis Grok | `GROK_IMPROVEMENT_PLAN.md` |
| Strategy post-paper | `POST_PAPER_TRADE_STRATEGY.md` |

---

*Update terakhir: 19 April 2026 (rev Grok v2 — Fix #3 override logic, Fix #4a city list expanded, Phase 3 Kelly shadow gate added). Lanjutkan dari sini di sesi berikutnya.*
