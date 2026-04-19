# Phase 3 Live Trading — Skenario & Rules
*Dibuat: 19 April 2026 | Berlaku mulai: ~17 Mei 2026*

---

## Setup Awal

| Parameter | Value |
|---|---|
| Modal awal | $5.00 USD |
| Stake per posisi | Kelly Criterion (MIN $1, MAX $3) |
| Top-up rule | $5 setiap kali bankroll habis (lihat di bawah) |
| Platform | Polymarket |
| Target bulan pertama | Validasi Kelly + ensemble, bukan profit maksimal |

---

## Top-Up Rule

**Trigger:** Bankroll < $1.00 (tidak cukup untuk 1 posisi minimum)

**Aksi:**
1. Stop bot sementara
2. Analisis exit reasons dari trade terakhir (lihat query di bawah)
3. Top up $5 ke akun Polymarket
4. Restart bot

**Penting:** Jangan top up blindly. Sebelum top up, jalankan query ini:

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
"cd /opt/the_blueprints && python3 -c \"
import sqlite3
conn = sqlite3.connect('blueprints_master.db')

print('=== EXIT REASONS (30 trade terakhir) ===')
rows = conn.execute('''
  SELECT close_reason, COUNT(*) count, ROUND(AVG(pnl_usd),4) avg_pnl
  FROM trade_history
  ORDER BY closed_at DESC LIMIT 30
  GROUP BY close_reason
''').fetchall()
for r in rows: print(r)

print()
print('=== KOTA PALING RUGI ===')
rows = conn.execute('''
  SELECT city, COUNT(*) total,
         SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END) losses,
         ROUND(AVG(pnl_usd), 4) avg_pnl
  FROM trade_history
  WHERE closed_at > datetime('now', '-14 days')
  GROUP BY city ORDER BY avg_pnl ASC LIMIT 5
''').fetchall()
for r in rows: print(r)
conn.close()
\""
```

**Keputusan dari data:**
- Loss dominan dari `stop_loss` normal → cuaca memang miss, wajar → top up, lanjut
- Loss dominan dari 1–2 kota spesifik → pertimbangkan blacklist sementara kota itu
- Loss dari `profit_guard` / `whiplash_shield` → risk management kerja dengan benar, bukan bug
- Loss > 80% dari 1 bucket type (misal semua "persis X°C") → review sigma per kota

---

## Skenario A: Smooth Growth (Win Rate ≥ 55%)

**Indikator:** 3+ hari pertama tidak habis, win streak kecil.

**Proyeksi:**
```
Hari  1–3:  $5.00 → ~$5.50
Hari  4–7:  ~$5.50 → ~$6.50   ← Kelly mulai sedikit di atas MIN $1
Hari  8–14: ~$6.50 → ~$8–9    ← Compound mulai terasa
Hari 15+:   Kelly stake naik ke $1.5–2 per posisi di kota confidence tinggi
```

**Yang perlu diperhatikan:**
- Cek apakah stake distribution masuk akal (tidak semua $1, tidak ada spike $3 di kota lemah)
- Auto-tuner akan mulai agresif di kota win rate tinggi — itu normal

**Catatan Kelly:** Di bankroll < $10, hampir semua stake hit MIN_STAKE $1. Kelly baru meaningful di bankroll ≥ $10–15.

---

## Skenario B: Early Drawdown / Loss Streak

**Indikator:** 3–4 loss berturutan di hari pertama.

**Yang terjadi:**
```
$5.00 → loss → $4.00 → loss → $3.00 → loss → $2.00
         ↑ normal          ↑ waspada       ↑ hampir trigger top-up
```

**Rules:**
- Bankroll $3–4: lanjut, belum perlu intervensi
- Bankroll $2: pantau lebih ketat, cek exit reasons
- Bankroll < $1: **trigger top-up rule** (lihat atas)

**Jangan panik di loss streak 3–4.** Bahkan bot dengan probabilitas akurat bisa kena variance ini. Cuaca tidak 100% predictable.

---

## Skenario C: NOAA Override Menyelamatkan Posisi

**Kapan terjadi:** Ada posisi yang masuk pagi, tapi siang hari observasi real-time (NOAA/METAR) jauh dari forecast.

**Contoh:**
```
06:00 — Bot masuk YES "Seoul ≥ 28°C" @ harga 0.55
         Ensemble prob: 0.68 → edge bagus

13:00 — NOAA RKSI baca: 24°C (jauh di bawah threshold 28°C)
         Fix #3 trigger → override prob ke ~20%
         Bot eksekusi Sniper Stop Loss → exit @ 0.35

19:00 — Resolve: Seoul actual 25.8°C → NO
```

**Hasil:**
- Tanpa Fix #3: loss penuh $1 (beli @ 0.55, resolve @ 0)
- Dengan Fix #3: loss ~$0.20 (exit early @ 0.35)
- **Selamat $0.80 dari $1**

**Log yang perlu dicek:** Setiap `close_reason = "noaa_override"` di trade history adalah Fix #3 bekerja.

---

## Skenario D: Bot Terus Rugi Setelah 3× Top-Up

**Trigger:** Sudah top up 3 kali ($15 total dihabiskan) dan win rate masih < 45%.

**Ini bukan bug** — ini sinyal bahwa ada yang perlu dievaluasi di model.

**Aksi:**
1. Stop bot
2. Run full analysis (win rate per kota, per bucket type, per exit reason)
3. Cek apakah ada perubahan di Polymarket market structure (threshold berubah? resolusi berubah?)
4. Cek apakah ensemble API masih return data valid
5. Jangan top up lagi sebelum tahu root cause

**Batas maksimal:** $20 total (modal awal $5 + 3× top up $5). Lewat dari itu → pause dan evaluasi dulu.

---

## Kelly Behavior di Berbagai Bankroll

| Bankroll | Contoh stake (edge 25%, prob 0.65) | Actual stake |
|---|---|---|
| $5 | $0.46 | $1.00 (MIN) |
| $10 | $0.93 | $1.00 (MIN) |
| $15 | $1.39 | $1.39 |
| $25 | $2.32 | $2.32 |
| $50 | $4.64 | $3.00 (MAX) |

**Takeaway:** Kelly mulai "berasa" di ~$15. Di $5 awal, bot masih efektif fixed $1.

---

## Stop Rule (Kapan Evaluasi Ulang Seluruh Model)

Jika setelah **3 bulan live trading** kondisi ini terpenuhi:
- Total trades ≥ 50
- Win rate masih < 48%
- Total PnL negatif

→ **Pause live trading.** Kembali ke paper mode. Evaluasi apakah ada structural change di Polymarket atau model sudah drift.

Ini bukan kegagalan — ini due diligence.

---

## Quick Reference

| Kondisi | Aksi |
|---|---|
| Bankroll < $1 | Top up $5, analisis exit reasons dulu |
| 3× top up, masih rugi | Stop, evaluasi model |
| Loss dari 1 kota terus | Blacklist sementara kota itu |
| `noaa_override` di log | Fix #3 kerja, normal |
| Stake semua $1 | Normal di bankroll < $10, Kelly baru efektif di ≥ $15 |
| Win rate ≥ 52% bulan pertama | On track, lanjut |

---

*Dokumen ini dibaca saat Phase 3 live trading dimulai (~17 Mei 2026).*
