# Competitive Analysis — The Blueprints vs Bot Lain
*Dibuat: 19 April 2026 | Sumber: Grok analysis (GitHub, blog, YouTube, Reddit, X)*
*Berlaku setelah: Fix #1–#5 selesai (~17 Mei 2026)*

---

## Posisi The Blueprints Setelah Full Fix

**Top 3–4 bot weather trading Polymarket yang paling kompetitif dan sustainable.**

Bukan yang paling cepat profit, tapi yang paling **tahan banting + konsisten jangka panjang**.

---

## Perbandingan Head-to-Head

| Aspek | **The Blueprints** | **WeatherBot.finance** | **suislanchez Bot** | **NOAA Simple Bot** | **WeatherBet (GitHub)** |
|---|---|---|---|---|---|
| **Forecast** | 31-member GFS Ensemble + wttr.in + NOAA/METAR override | 4-model ensemble + Claude | 31-member GFS ensemble | NOAA point forecast | ECMWF + GFS multi-source |
| **Kota** | 31 | 67+ | 10–15 | 6–10 US + London/Seoul | 20 |
| **Stake Sizing** | Kelly Criterion (fractional 0.20) | Kelly dinamis | Fractional Kelly | Fixed kecil | Kelly + EV filter |
| **Risk Management** | **Juara** — Haiku AI monitor tiap 1 jam, 7 exit strategy, Newborn/Profit/Whiplash/Consensus Guard | Claude AI monitor (bagus) | Daily loss limit | Simple stop-loss | Basic EV + stop |
| **Resolution Match** | Exact airport + NOAA/METAR real-time (Fix #3) | Station-specific | Exact airport | Airport-focused | Good, tapi kurang NOAA |
| **Win Rate** | Target ≥55% (paper) | 60–70% (klaim) | ~65% | 55–65% ($24k–$70k tercatat) | Belum public |
| **Kelemahan** | Kota lebih sedikit dari WeatherBot | Mahal + black-box | Noise dari BTC signal | Terlalu simple, rawan drawdown streak | Kurang kota & AI guard |
| **Sustainability** | **Paling tinggi** | Tinggi | Sedang | Rendah | Sedang |

---

## Analisis Per Kompetitor

### WeatherBot.finance (hype $700 → $74k)
- **Unggul:** Volume kota (67+ vs 31), speed profit
- **Kalah:** Resolution presisi lebih rendah, risk management lebih lemah
- **Verdict:** Mereka lebih cepat kaya di good streak. Tapi kena bad streak → drawdown besar. Blueprints masih hidup saat mereka struggling.

### suislanchez Bot ($1.8k profit di repo)
- **Unggul:** Hampir setara di ensemble + Kelly
- **Kalah:** Tidak ada AI monitor, tidak ada per-region sigma, tidak ada auto-tuner
- **Verdict:** Blueprints lebih stabil, jarang kena false exit.

### NOAA Simple Bot ($24k–$70k tercatat)
- **Unggul:** Track record profit besar (cara simpel ternyata cukup efektif)
- **Kalah:** Tidak ada protection layer, satu sumber data
- **Verdict:** Blueprints setara bahkan lebih baik setelah Fix #3, tapi dengan lapisan perlindungan yang mereka tidak punya. Rugi lebih sedikit saat cuaca berubah mendadak.

### WeatherBet (GitHub)
- **Unggul:** Multi-source forecast (ECMWF + GFS)
- **Kalah:** Kota sedikit, tidak ada AI guard
- **Verdict:** Blueprints menang di sustainability dan risk management.

---

## Proyeksi Profit (Grok Estimate)

*Asumsi: Paper Phase 1–2 win rate ≥55%, ensemble jalan mulus, bankroll di-scale secara bertahap.*

| Periode | Proyeksi | Catatan |
|---|---|---|
| Bulan 1 (~17 Mei) | +$3–8 dari modal $5 | Kelly masih sering hit MIN $1, bankroll kecil |
| Bulan 3–6 | $50–200+/bulan | Kalau bankroll di-scale setelah profit konsisten |
| Long-term (6–12 bulan) | Setara/lebih baik dari bot $1k–$10k/bulan | Risk management superior = survival rate tinggi |

**Catatan penting:** Proyeksi ini bukan jaminan. Cuaca inherently unpredictable. Angka-angka ini based on asumsi model performanya sesuai paper trade.

---

## Keunggulan Unik Blueprints (Tidak Dimiliki Kompetitor)

1. **7 exit strategy + 4 guard layer** — Tidak ada bot lain yang punya ini semua
2. **Haiku AI monitor tiap 1 jam** — Real-time intelligence, bukan rule-based saja
3. **NOAA sebagai confirmation/override** (Fix #3) — Bukan sumber rata-rata, tapi final arbiter <6 jam
4. **Auto-tuner per kota** (Fix #4b) — Bot otomatis adaptasi sesuai track record kota
5. **Per-region sigma** (Fix #4a) — Calibrasi Gaussian berbeda untuk kota tropis vs 4-musim

---

## Kesimpulan

Setelah 17 Mei, Blueprints adalah **bot yang paling sulit mati dan paling reliable jangka panjang**.

- Risk management: **juara**
- Forecast presisi: **setara top kompetitor** (setelah Fix #2 & #3)
- Speed profit: **kalah dari WeatherBot.finance** (volume kota lebih sedikit)
- Survival rate saat variance jelek: **paling tinggi**

**Bottom line:** Lo tidak bakal kalah telak dari siapapun. Di beberapa aspek (risk + presisi resolution) lo lebih unggul. Trade-off-nya: mereka bisa lebih cepat kaya, tapi lo lebih konsisten dan tidak gampang bangkrut.

---

*Referensi: GROK_IMPROVEMENT_PLAN.md | NEXT_STEPS_TRACKER.md | PHASE3_LIVE_SCENARIOS.md*
