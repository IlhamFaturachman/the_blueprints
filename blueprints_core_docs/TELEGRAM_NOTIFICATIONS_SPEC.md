# TELEGRAM NOTIFICATIONS SPECIFICATION (PREMIUM LIVE-READY)

## Filosofi Desain

1. **Money-First**: Pengaruh terhadap saldo riil harus selalu terlihat.
2. **No-Jargon**: Hilangkan istilah internal (Bucket, Ensemble, Warmer) dari pesan utama.
3. **High-Contrast**: Gunakan format Markdown yang menonjolkan angka-angka krusial.

---

## 🟢 1. TRADE ENTRY (The "New Money" Alert)

Fungsi: Mengabarkan setiap posisi baru dengan kejelasan sisa amunisi.

**Contoh Pesan:**

```text
🚀 <b>NEW POSITION OPENED</b> 🚀

<b>Market:</b> Higher Temperature in Singapore?
<b>Sentiment:</b> <pre>YES (Exact 33.0C)</pre>

🧠 <b>AI Rationale:</b>
AI mendeteksi kemungkinan target tercapai sebesar <b>99.8%</b>. Strategi dikategorikan sebagai <b>SWING RIDE</b> karena harga saat ini ($0.48) memberikan peluang profit yang sangat sehat.

📊 <b>Trade Details:</b>
├ 💵 <b>Stake:</b> $1.43
├ 🏷️ <b>Entry Price:</b> $0.48
└ 🎯 <b>Take Profit:</b> $0.96 (2x Target)

🏦 <b>Accounting:</b>
├ 🔴 <b>Cash Debited:</b> -$1.43
└ 💳 <b>Remaining Liquid Cash:</b> <b>$3.57</b>

🔗 <a href='https://polymarket.com/event/...'>Lihat di Polymarket</a>
```

---

## 🔴 2. POSITION CLOSED (Realized PnL)

Fungsi: Laporan hasil akhir trading agar tidak ada "keuntungan/kerugian gaib".

**Contoh Pesan:**

```text
🏁 <b>POSITION CLOSED</b> 🏁

<b>Market:</b> Higher Temperature in Singapore?
<b>Result:</b> 🟢 <b>PROFIT (Take Profit Hit)</b>

📈 <b>Performance:</b>
├ 💰 <b>Net Profit:</b> +$0.51
└ 📊 <b>Growth:</b> +35.6%

🏦 <b>Accounting:</b>
├ 🟢 <b>Cash Returned:</b> +$1.94 (Stake + Profit)
└ 💳 <b>New Wallet Balance:</b> <b>$5.51</b>
```

---

## 🛑 3. CIRCUIT BREAKER (Emergency Halt)

Fungsi: Penjelasan gamblang kenapa bot berhenti. Tidak boleh ada angka yang membingungkan.

**Contoh Pesan:**

```text
⚡ <b>CIRCUIT BREAKER: SYSTEM HALTED</b> ⚡

⚠️ <b>Reason:</b> Batas kerugian harian terlampaui.

📉 <b>Financial Status:</b>
├ 💸 <b>Daily Realized Loss:</b> -$2.01
├ 🛑 <b>Max Daily Limit:</b> -$1.50
└ 🏦 <b>Final Safe Balance:</b> $2.99

🛡️ <b>Protokol Keamanan:</b>
Bot telah berhenti membuka posisi baru secara total untuk melindungi sisa modal Anda.

🔄 <b>Action Needed:</b>
Audit manual diperlukan sebelum trading bisa dilanjutkan (Reset manual sisa modal ke $5.00 atau terima kondisi sekarang).
```

---

## 📊 4. DAILY SNAPSHOT (The Health Check)

Fungsi: Rekonsiliasi data setiap 24 jam.

**Contoh Pesan:**

```text
📊 <b>DAILY ACCOUNTABILITY REPORT</b> 📊
<i>Periode: 18 April 2026</i>

✅ <b>Operational Health:</b>
- Status: <b>HALTED</b> (Waiting for Reset)
- Integrity: 100% (No Code Drift)
- Log Status: Rotated & Clean

💰 <b>PnL Performance:</b>
- Total Trades: 12
- Win Rate: 75%
- Net Daily PnL: 🔴 -$0.51

🏦 <b>Final Reconciliation:</b>
- Wallet Balance: $2.99
- Sync Status: <b>IN SYNC</b> (Verified by Watchdog)
```

---

## 🛠️ Persiapan Implementasi (Tugas Claude jam 12)

1. **Modul `reporting.py`**: Perbarui fungsi `send_entry_msg` dan `send_exit_msg` untuk menggunakan template di atas.
2. **Modul `cycles.py`**: Pastikan fungsi `run_paper_trading_cycle` melempar variabel `remaining_cash` ke sistem pelaporan.
3. **Format**: Gunakan `parse_mode='HTML'` di API Telegram untuk mendukung tag `<b>`, `<i>`, dan `<pre>`.

---

## 🛰️ 5. RESOURCE & INFRA HEALTH (The "Anti-Crash" Alert)
Fungsi: Memastikan server tidak mati mendadak karena kehabisan sumber daya.

**Contoh Pesan:**
```text
🔌 <b>INFRASTRUCTURE HEALTH ALERT</b> 🔌

⚠️ <b>Warning:</b> Memory Usage is High (85%)
💾 <b>Disk Space:</b> 500MB Remaining

🛠️ <b>System Action:</b>
Bot sedang melakukan pembersihan mandiri (rotating logs) untuk mengosongkan ruang. Pengawasan manual disarankan.
```

---

## 🔍 6. DATA ANOMALY & QUALITY (The "Anti-Junk" Guard)
Fungsi: Mencegah trading berdasarkan data salah dari API eksternal.

**Contoh Pesan:**
```text
🔍 <b>ANOMALY DATA DETECTED</b> 🔍

<b>Market:</b> Higher Temperature in Singapore?
<b>Issue:</b> Polymarket price returned invalid value ($1.05)

🛡️ <b>Safety Protocol:</b>
Aksi beli untuk market ini diabaikan otomatis untuk menghindari kerugian akibat malfungsi API.
```

---

## ⚡ 7. AUDIT TRAIL (Operator Interaction)
Fungsi: Mencatat setiap perubahan manual yang dilakukan pada sistem.

**Contoh Pesan:**
```text
⚡ <b>OPERATOR OVERRIDE DETECTED</b> ⚡

👤 <b>Action:</b> Manual Circuit Breaker Reset
✅ <b>Status:</b> Trading Resumed

🏦 <b>Wallet Adjustment:</b>
Saldo awal sesi disetel ulang ke <b>$5.00</b>. Seluruh perhitungan kerugian harian dimulai dari nol.
```
