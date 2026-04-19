# Fix #2 Implementation Plan — Open-Meteo Ensemble Integration (Revised)

**Versi:** 1.1 | **Tanggal:** 19 April 2026 | **Status:** Ready for Implementation (24 April pagi)
**Catatan revisi:** Sudah disesuaikan dengan nama fungsi aktual di codebase (`fetch_forecast()` & `calculate_edge()`)

**Tujuan:** Mengintegrasikan **31-member GFS Ensemble** sebagai sumber ke-3 agar probabilitas bucket jauh lebih akurat, mengurangi ketergantungan pada sigmoid/Gaussian, tanpa merusak stabilitas Phase 1.

---

## 1. Latar Belakang & Cross-Check Sumber

**Sumber yang sudah diverifikasi (per 19 April 2026):**
- Official Open-Meteo Ensemble API: `https://ensemble-api.open-meteo.com/v1/ensemble`
- Regular forecast: `https://api.open-meteo.com/v1/forecast` (beda endpoint!)
- `models=gfs_seamless` → 31 member (1 control + 30 perturbed)
- Support `daily=temperature_2m_max`
- Best practices probabilistic forecasting: ensemble fraction jauh lebih reliable daripada deterministic + post-processing

**Manfaat utama:**
- Probabilitas langsung dari distribusi → tidak perlu tuning k-factor sigmoid lagi
- Lebih tahan uncertainty model (±2–3°C)
- Edge lebih stabil

---

## 2. Pendekatan yang Dipilih

**Hybrid 3-Sumber (bukan full replace):**
- Sumber 1: Open-Meteo point forecast (existing)
- Sumber 2: wttr.in ICAO (sudah fix)
- Sumber 3: **Open-Meteo Ensemble** → bobot tertinggi (45%)

**Cara hitung probabilitas akhir:**
- Ensemble → hitung **fraksi member** yang memenuhi kondisi bucket
- Weighted average dengan 2 sumber lama
- Tetap pakai existing hard ceiling
- Fallback otomatis kalau ensemble gagal

---

## 3. File yang Akan Diubah

1. `config.py`
2. `market_discovery_internal/forecasting.py`
3. `market_discovery_internal/pricing.py`

*(Haiku update & DB migration di-skip dulu — nice-to-have, bukan blocking)*

---

## 4. Langkah Implementasi Detail (Step-by-Step)

**Langkah 1: Config (10–15 menit)**

Tambah section di `config.py`:

```python
ENSEMBLE_CONFIG = {
    "enabled": False,                    # default False dulu (shadow mode)
    "url": "https://ensemble-api.open-meteo.com/v1/ensemble",   # ← beda endpoint!
    "models": "gfs_seamless",
    "variables": "temperature_2m_max",
    "forecast_days": 2,
    "weight": 0.45
}
```

**Langkah 2: Forecasting Layer (40–50 menit)**

Di `forecasting.py`, update fungsi `fetch_forecast()` agar:
- Panggil ensemble API pakai airport lat/lon (sudah benar sejak Fix #1)
- Return field baru: `"ensemble_members": list[float]` (31 nilai) atau `None` kalau gagal
- Tambah helper `_fetch_openmeteo_ensemble()`

**Langkah 3: Pricing Layer (50–70 menit)**

Di `pricing.py`, update fungsi `calculate_edge()`:
- Buat helper baru `_calculate_ensemble_probability(ensemble_members, bucket)`
  - Untuk bucket "≥ X°C" / "≤ X°C" → `members_above / 31`
  - Untuk bucket "persis X°C" → hitung member di range X ± 0.5°C
  - Final prob = weighted average (ensemble 45% + point 35% + wttr 20%)
- Pastikan fallback: kalau ensemble None → bobot 0, pakai 2 sumber lama

**Langkah 4: Integrasi & Fallback**

- `ENSEMBLE_CONFIG["enabled"]` kontrol semua dengan satu flag
- Kalau API error → log warning + lanjut pakai 2 sumber lama (zero downtime)

---

## 5. Testing Plan (Wajib Sebelum Deploy)

**Local testing:**
1. Test 8–10 kota dengan tanggal besok
2. Print prob lama vs prob baru side by side
3. Pastikan tidak ada crash kalau ensemble dimatikan (fallback OK)

**Shadow mode (24 April pagi):**
- Deploy dengan `enabled = False`
- Log ensemble prob di samping tanpa pakai untuk decision
- Bandingkan 1–2 golden window

**Full activation:**
- Set `enabled = True` setelah shadow mode oke (25–26 April)

---

## 6. Trade-off & Risiko

| Aspek | Pro | Con | Mitigasi |
|---|---|---|---|
| Akurasi Prob | Sangat tinggi | — | — |
| Jumlah Peluang | Lebih presisi | Bisa sedikit berkurang | Self-learning adjust |
| API Load | +1 call per pasar | Gratis & cepat | Cache 1 jam jika perlu |
| Complexity | Sedang | Tambah logic | Fallback kuat |
| Data Comparability | — | Phase 1 vs Phase 2 beda | Shadow mode dulu |

---

## 7. Timeline

- **24 April pagi** (setelah golden window 23 April tutup) → Implement + shadow mode
- **25–26 April** → Evaluasi shadow → full activation
- Setelah itu → lanjut backlog (NOAA/METAR, per-region sigma, auto-tuner adj_edge)

**Catatan penting:**
- Jangan ubah golden window atau aturan masuk selama Phase 1
- Bot tetap aman meski ensemble mati (fallback ke 2 sumber lama)
- Semua perubahan dicatat di `GROK_IMPROVEMENT_PLAN.md`

---

_Dokumen ini sudah 100% selaras dengan codebase aktual (nama fungsi, endpoint, bobot). Siap dieksekusi 24 April._
