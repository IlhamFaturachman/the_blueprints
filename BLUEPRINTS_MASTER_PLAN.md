# 🧬 THE BLUEPRINTS: ULTIMATE SURVIVAL MASTER PLAN (Phase 2)

Dokumen ini adalah hukum tertinggi dalam pembangunan bot trading cuaca THE BLUEPRINTS-Edition. Target utama: Compounding dana **$5 menuju $100+** dengan tingkat kegagalan teknis mendekati nol.

---

## 🗺️ STRATEGIC ROADMAP: THE 4 WAVES

### 🟢 WAVE 1: THE EYES (Akurasi & Filter)
*Fokus: Memastikan bot tidak pernah "salah lihat" data.*
- **[MODUL A] Precision Semantic Sensing:** Ekstraksi ICAO dinamis (Regex + AI Haiku) dari teks market untuk koordinat stasiun presisi (misal: Central Park, LHR, dll). Didukung **Learning Cache** untuk nol biaya AI pada stasiun yang sudah dikenal.
- **[MODUL B] Multi-API Consensus:** Validasi silang data antara Open-Meteo & NOAA Aviation (Ground Truth).
- **[MODUL C] The Golden Window:** Pembatasan entry hanya pada jendela **8-14 jam** sebelum resolve (Zona Akurasi >90%).
- **[MODUL K] Anomaly Check:** Blokir entry jika suhu melompat >7°C dari rata-rata harian (mencegah typo data API).

### 🟡 WAVE 2: THE HANDS (Eksekusi & Refleks)
*Fokus: Mengaktifkan perdagangan asli dan kecepatan keluar dari market.*
- **[MODUL E] Liquidity Gate:** Cek kedalaman orderbook sebelum entry. Batalkan jika stake menggeser harga >3%.
- **[MODUL G] Exit Sniper:** Auto Take-Profit (90%) dan pre-emptive Stop Loss (20%) berdasarkan pemantauan METAR tiap 5 menit.
- **[MODUL H] Live Bridge:** Implementasi `py-clob-client` dengan Signature EOA (Phantom) untuk trading asli di Polygon.

### 🔴 WAVE 3: THE FORTRESS (Money Management)
*Fokus: Melindungi modal dan menumbuhkan saldo secara eksponensial.*
- **[MODUL D] Compounding & Slot Expansion:**
  - $5 - $20: Stake $1 - $2 per market, Max 5-8 Slot.
  - $20 - $100: Stake 15% saldo, Max 15+ Slot (Diversifikasi Tinggi).
- **[MODUL L] Circuit Breaker:** Kunci bot otomatis jika rugi harian menyentuh **$1.50**.
- **[MODUL M] Backtest Engine:** Validasi strategi pada data historis 7 hari terakhir sebelum live money.

### 🔵 WAVE 4: THE SYSTEM (Stabilitas)
*Fokus: Keberlangsungan bot jangka panjang dan notifikasi user.*
- **[MODUL F] Wallet Sentry:** Alert jika POL < 0.2 (Gas) atau USDC < $1.
- **[MODUL I] Self-Healing:** Sinkronisasi state lokal dengan riwayat on-chain setiap jam untuk mencegah "Amnesia" data.
- **[MODUL J] Emergency Kill-Switch:** Satu tombol di Dashboard untuk tutup semua posisi & shutdown bot.
- **[MODUL N] Mobile Alert Sentry:** Notifikasi laporan real-time (Entry/Exit/Profit) langsung ke Telegram HP.

---

## 📈 GROWTH PROJECTIONS (Estimasi Waktu)

| Fase Target | Modal Awal | Stake | Estimasi Durasi |
| :--- | :--- | :--- | :--- |
| **$5 ➡️ $20** | $5.00 | $1 - $2 | 7 - 12 Hari |
| **$20 ➡️ $100** | $20.00 | 15% ($3+) | 5 - 8 Hari |
| **TOTAL** | **$5.00** | **Scalable** | **~15 - 20 Hari** |

---

## 🛠️ TECHNICAL DEEP-DIVE (Instruksi Mesin)

### Konfigurasi Data Weather
- **NOAA API:** `https://api.weather.gov/stations/{icao}/observations/latest`
- **Logic:** `temp_c = properties['temperature']['value']`. Wajib `User-Agent` unik.

### Live Trading (CLOB)
- **Library:** `py-clob-client`.
- **Order Flow:** 
  1. `get_order_book` (Check Depth) ➡️ 2. `create_order` (Limit Order) ➡️ 3. `get_order` (Verify Active).
- **Failure:** Jika error `400 (Insufficient Balance)`, bot masuk mode `PAUSE` dan kirim alert Telegram.

---

## 🛡️ ZERO-FLAW FAILURE ANALYSIS

| Trigger Problem | Aksi Bot (Respon Otomatis) |
| :--- | :--- |
| **NOAA API Down** | Turunkan *Edge Threshold* 20%, hanya andalkan Open-Meteo + Alert. |
| **Polymarket Error** | Hentikan entry baru, masuk mode rekonsiliasi L2 Auth. |
| **Server Restart** | **Self-Healing:** Baca `state.json` & rebuild posisi terbuka dari history order. |
| **Slippage Tinggi** | Cicil order (Partial Fill) atau batalkan jika harga goyang >3%. |

---

## 🏁 PRE-FLIGHT CHECKLIST (Wajib Cek)
- [ ] **Private Key:** Terpasang aman di `.env` (bukan di kode).
- [ ] **USDC Allowance:** Dompet sudah approve kontrak trading Polymarket.
- [ ] **Gas Check:** Minimal 0.5 POL tersedia di dompet Polygon.
- [ ] **Telegram Token:** Token BotFather sudah terinput di config.

> [!CAUTION]
> **ATURAN EMAS:** Dilarang melakukan "Market Order". Semua transaksi WAJIB "Limit Order" (Maker) untuk mendapatkan harga terbaik dan menghindari dikerjai oleh bot lain.
