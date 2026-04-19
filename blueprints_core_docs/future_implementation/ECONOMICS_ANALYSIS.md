# Economics & Scaling Analysis — The Blueprints

**Dibuat:** 19 April 2026 | **Tujuan:** Roadmap pertumbuhan bankroll dan mitigasi risiko ekonomi.

---

## ⚠️ Analisis Risiko Ekonomi

| Risiko | Level | Dampak & Mitigasi |
|---|---|---|
| **Polymarket liquidity limit** | Medium | Volume di market temperatur terbatas. Stake yang terlalu besar (> $50/bucket) bisa terkena slippage tinggi atau tidak terisi sepenuhnya. |
| **Win rate drop ke < 50%** | Tinggi | Drawdown berkelanjutan akan menghabiskan bankroll. Perlu mekanisme pause otomatis atau review data warmer jika ini terjadi. |
| **Cuaca musiman anomali** | Medium | El Niño / La Niña bisa merusak akurasi forecast GFS/ECMWF. Perlu adjust `adj_edge` secara manual jika anomali berkepanjangan. |
| **Polymarket market structure** | Medium | Jika resolusi target berubah (misal dari airport ke regional), bot perlu update `station_id` secara masal. |

---

## 📈 Strategi Scaling Bankroll (MAX_STAKE)

Bot tidak akan memberikan income signifikan jika `MAX_STAKE` tetap di $1. Seiring tumbuhnya saldo (Bankroll), kita harus melakukan adjustment manual pada `config.py`.

| Threshold Bankroll | Target MAX_STAKE | Catatan |
|---|---|---|
| **$5 – $40** | $1 (Tier 1) | Fase validasi model (Paper/Low Stake). |
| **$40 – $100** | $5 | Mulai transisi ke income harian kecil. |
| **$100 – $300** | $8 – $10 | Target bankroll awal yang sehat. |
| **$300+** | 2% – 3% Bankroll | Gunakan persentase tetap, namun pantau likuiditas market. |

> [!IMPORTANT]
> Tanpa melakukan scale pada `MAX_STAKE`, proyeksi profit akan selalu converge di angka ~$15–30/bulan karena keterbatasan Kelly Criterion pada saldo kecil.

---

## 💰 Proyeksi Income (Januari 2027)

Berdasarkan performa model dan efisiensi market, berikut adalah skenario proyeksi saat bot mencapai kematangan di awal tahun depan:

| Skenario | Win Rate Target | Bankroll (Jan 2027) | Income/Bulan |
|---|---|---|---|
| **Konservatif** | 52% – 55% | ~$85 – $100 | $15 – $25 |
| **Base Case** | 58% – 62% | ~$430 – $500 | $150 – $200 |
| **Optimistis** | 63% – 67% | ~$3,000 – $5,000 | $300 – $800 |

*Catatan: Skenario **Base Case** adalah yang paling berpeluang terjadi jika Fix #1–#5 di-apply dan `MAX_STAKE` di-scale secara aktif.*

---

## 🛠️ Syarat Operasional (Bottom Line)

Angka di atas bukanlah **passive income** yang bisa ditinggal tidur begitu saja. Untuk mencapai target tersebut, owner harus:

1. **Monitoring Mingguan**: Cek status `active_positions` dan `haiku_monitor` log untuk memastikan taktik exit tetap relevan.
2. **Adjustment Stake**: Perbarui `MAX_STAKE` di `config.py` setiap kali bankroll naik level.
3. **No Drawdown Panic**: Tetap disiplin pada Kelly Criterion saat menghadapi varians negatif di bulan pertama.

---

*Referensi: COMPETITIVE_ANALYSIS.md | GROK_IMPROVEMENT_PLAN.md | PHASE3_LIVE_SCENARIOS.md*
