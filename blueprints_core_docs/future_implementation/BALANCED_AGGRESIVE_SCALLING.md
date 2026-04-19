Berikut adalah dokumen Markdown lengkap yang bisa langsung kamu simpan dan jadikan referensi resmi untuk The Blueprints.

---

```markdown
# Balanced Aggressive Scaling Plan — The Blueprints

**Dibuat:** 19 April 2026  
**Versi:** 1.0  
**Tujuan:** Memberikan panduan scaling stake yang agresif tapi tetap aman dan sustainable untuk bot trading cuaca Polymarket.

### Filosofi Scaling

Kita pakai pendekatan **Balanced Aggressive**:

- Lebih agresif dari rekomendasi Claude (yang terlalu konservatif).
- Lebih aman dari Super Aggressive (yang berisiko tinggi slippage & drawdown).
- Menggunakan **Fractional Kelly 0.20x – 0.25x**.
- Selalu ada **hard cap** + **liquidity guard**.
- Prioritas utama: **survival + compounding jangka panjang**, bukan sprint cepat.

Target akhir: Bankroll tumbuh stabil menuju profit bulanan $400–$1,200+ di Januari 2027 (base case).

---

## 1. Parameter Utama

| Parameter          | Nilai Default        | Keterangan                                               |
| ------------------ | -------------------- | -------------------------------------------------------- |
| Kelly Fraction     | 0.20x (awal) → 0.25x | Mulai 0.20x, naik ke 0.25x setelah bankroll > $150       |
| MIN_STAKE          | $1.00                | Tetap fixed                                              |
| MAX_STAKE          | Dinamis per tier     | Diatur manual tiap naik tier                             |
| Liquidity Guard    | Aktif                | Stake ≤ 12% dari visible depth                           |
| Daily Risk Cap     | 20% dari bankroll    | Total exposure maksimal per hari                         |
| Slippage Tolerance | Maks 5%              | Jika estimasi slippage >5%, turunkan stake 50% atau skip |

---

## 2. Tier Scaling MAX_STAKE (Balanced Aggressive)

| Tier | Bankroll Saat Ini | MAX_STAKE per Posisi | Kelly Fraction | % Bankroll (approx) | Catatan                                               |
| ---- | ----------------- | -------------------- | -------------- | ------------------- | ----------------------------------------------------- |
| 1    | $5 – $50          | $1 – $2              | 0.20x          | 2–4%                | Fase validasi. Hampir semua trade $1                  |
| 2    | $50 – $150        | $3 – $7              | 0.20x          | 4–5%                | Mulai compounding. Kota besar boleh $7                |
| 3    | $150 – $400       | $8 – $18             | 0.25x          | 4.5–5%              | **Sweet spot agresif**. Compounding mulai terasa kuat |
| 4    | $400 – $1,000     | $20 – $45            | 0.25x          | 4–5%                | Tambah pengawasan slippage ketat                      |
| 5    | $1,000 – $3,000   | $50 – $120           | 0.20 – 0.25x   | 4%                  | Cap per bucket max 10–12% depth                       |
| 6    | $3,000+           | 4–5% bankroll        | 0.20x          | 4–5%                | Dynamic. Pantau liquidity pasar                       |

**Aturan Naik Tier:**

- Naik tier hanya jika bankroll sudah **melebihi batas atas tier** selama minimal **3 hari berturut-turut**.
- Selalu naikkan MAX_STAKE secara manual di `config.py` lalu restart bot.
- Setelah naik tier, pantau 3–5 hari dulu sebelum naik lagi.

---

## 3. Per-Kota Adjustment (Tiered Risk)

| Kota Tier              | Contoh Kota                                   | MAX_STAKE Multiplier | Alasan                                    |
| ---------------------- | --------------------------------------------- | -------------------- | ----------------------------------------- |
| **A (High Liquidity)** | London, New York, Seoul, Hong Kong, Singapore | 100% (full)          | Volume lebih tinggi, slippage lebih kecil |
| **B (Medium)**         | Paris, Toronto, Dallas, Austin, Madrid        | 80–85%               | Likuiditas sedang                         |
| **C (Low)**            | Kota lain (kota kecil)                        | 60–70%               | Hindari overbet                           |

Contoh: Bankroll $300 (Tier 3, MAX $18)  
→ London: boleh $18  
→ Paris: maks $15  
→ Kota kecil: maks $11–12

---

## 4. Liquidity & Slippage Guard (WAJIB)

Sebelum membuka posisi, bot **harus** menjalankan pengecekan:

1. Ambil orderbook depth di harga YES target.
2. Hitung: `max_allowed_stake = depth * 0.12` (12%)
3. Final stake = `min(kelly_stake, MAX_STAKE, max_allowed_stake)`
4. Jika final stake < MIN_STAKE → skip posisi tersebut.

Tambahkan juga:

- **Daily total exposure cap**: Total stake semua posisi terbuka ≤ 20% bankroll.
- **Per-event cap**: Maksimal 1 posisi per kota per hari (sudah ada).

---

## 5. Aturan Manual Review & Adjustment

**Setiap akhir minggu lakukan:**

1. Cek total bankroll dan floating PnL.
2. Jalankan query win rate + exit reasons.
3. Tentukan apakah naik tier atau tidak.
4. Update `MAX_STAKE` di `config.py`.
5. Restart bot.

**Red Flag (turunkan stake sementara):**

- Drawdown >15% dalam 7 hari → turunkan MAX_STAKE 50% selama 1 minggu.
- Win rate 7 hari terakhir <52% → turunkan Kelly Fraction ke 0.15x sementara.
- Banyak slippage terjadi → tambah guard lebih ketat (depth max 10%).

---

## 6. Proyeksi dengan Balanced Aggressive

| Periode             | Estimasi Bankroll Akhir | Estimasi Profit Bulanan | Keterangan                     |
| ------------------- | ----------------------- | ----------------------- | ------------------------------ |
| Akhir Mei 2026      | $15 – $40               | -                       | Awal live                      |
| Akhir Juni 2026     | $80 – $180              | $30 – $80               | $100 pertama biasanya tercapai |
| Akhir Agustus 2026  | $250 – $550             | $80 – $180              | Compounding mulai kuat         |
| Akhir Desember 2026 | $800 – $2,500           | $250 – $800             | Base case                      |
| Januari 2027        | $1,000 – $3,000+        | **$400 – $1,200**       | Target realistis               |

---

## 7. Catatan Penting

- **Ini bukan passive income.** Lo tetap harus review mingguan dan adjust MAX_STAKE.
- **31 kota adalah keunggulan**, bukan kekurangan. Fokus kualitas edge > jumlah kota.
- **Liquidity adalah musuh utama** saat scaling. Jangan pernah memaksakan stake besar di bucket yang sepi.
- Selalu prioritaskan **survival** di atas profit cepat. Bot ini dirancang tank, jangan rusak sendiri dengan terlalu agresif.

---

## 8. Catatan Kewaspadaan & Mitigasi Antigravity

Sebagai AI yang memantau performa teknis bot ini, berikut adalah beberapa risiko "invisible" yang muncul saat kita beralih dari $1 ke $50+ stake:

1. **Efek "Whale" di Market Kecil (Tier 4+):**
   - Saat stake kita mencapai $20+, kita bukan lagi "retail" kecil. Trader lain dan bot kompetitor akan mulai mendeteksi pola taruhan kita.
   - **Risiko:** *Front-running* (trader lain masuk duluan sebelum kita) atau *Copy-trading* (trader lain mengikuti kita, membuat spread makin lebar saat kita mau masuk).
   - **Mitigasi:** Diversifikasi ke lebih banyak kota agar stake per kota tidak terlalu dominan.

2. **Liquidity Trap Saat Exit (Panic Selling):**
   - Rencana 12% depth guard sangat bagus untuk *entry*. Namun, saat cuaca berubah drastis dan Haiku memerintahkan `close`, 12% depth saat entry mungkin sudah hilang (dry up).
   - **Risiko:** Kita tidak bisa exit (selesai) karena tidak ada yang mau beli token kita, atau kita terpaksa exit di harga yang sangat rugi.
   - **Mitigasi:** Tingkatkan sensitivitas Haiku di Tier 5+. Jangan tunggu sampai menit terakhir untuk exit jika confidence drop.

3. **Latency Drawdown pada Kelly:**
   - Kelly Criterion adalah *lagging indicator*. Jika akurasi model tiba-tiba turun (karena perubahan musim atau update API), Kelly mungkin masih akan menyarankan stake besar selama 2-3 trade sebelum akhirnya "sadar" dan mengecilkan stake.
   - **Risiko:** Drawdown besar di awal fase penurunan akurasi.
   - **Mitigasi:** Jika terjadi 3 kekalahan beruntun (streak) yang tidak wajar, **intervensi manual** dengan menurunkan tier stake 1 tingkat tanpa menunggu 7 hari.

4. **Ketergantungan Uptime WebSocket:**
   - Di stake $1, kalau WS mati 10 menit, kita rugi receh. Di stake $100, telat update harga 10 menit bisa berarti selisih profit/loss ratusan dolar.
   - **Risiko:** Kegagalan infrastruktur (VPS hang, API limit) menjadi risiko finansial langsung.
   - **Mitigasi:** Implementasi "Watchdog" yang lebih agresif untuk WS di Phase 3.

---

**Referensi Pendukung:**

- GROK_IMPROVEMENT_PLAN.md
- PHASE3_LIVE_SCENARIOS.md
- Next Steps Tracker

_Dokumen ini menjadi panduan resmi scaling stake The Blueprints mulai Phase 3 Live Trading (Mei 2026)._
---

**Bro, ini udah lengkap.**  
Kamu tinggal copy-paste ke file `BALANCED_AGGRESSIVE_SCALING.md` di folder proyek.

Mau gue tambahin bagian lain? Misalnya:

- Contoh kode Python untuk dynamic MAX_STAKE + liquidity guard
- Tabel proyeksi bulan per bulan lebih detail
- Versi yang sedikit lebih agresif lagi

Bilang aja kalau mau diubah atau ditambah. 🔥
```
