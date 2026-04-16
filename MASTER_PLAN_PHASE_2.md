# 🏆 OPERATIONAL MANUAL: JARVIS Z-EDITION (Phase 2)

Rencana ini adalah instruksi teknis tingkat akhir untuk pembangunan sistem trading otonom. Target: Zero-Flaw Execution & Ultimate Survival.

---

## 🏗️ 4-WAVE STRATEGIC ROADMAP

### 🌊 WAVE 1: THE EYES (Akurasi & Filter)
**Tujuan:** Memastikan data input 99.9% akurat.
- **[MODUL A] ICAO Detection:** Regex parser untuk Gamma API `description`. Mapping ke koordinat sensor bandara di `config.py`.
- **[MODUL B] Multi-API Consensus:** Integrasi NOAA METAR via `api.weather.gov`. 
- **[MODUL C] Golden Window (8-14h):** Gatekeeper waktu berdasarkan `hours_to_resolve`.
- **[MODUL K] Anomaly Check:** Cek deviasi suhu (max 7°C dari rata-rata 24 jam).

### 🌊 WAVE 2: THE HANDS (Live Execution)
**Tujuan:** Eksekusi order gasless dan sniper exit.
- **[MODUL E] Liquidity Check:** Fungsi `check_depth()` memanggil `get_order_book`. Syarat: Volume di Best Ask > Stake USD.
- **[MODUL G] Exit Sniper:** Haiku Monitor memantau METAR tiap 5 menit. Trigger SELL jika:
  - Harga >= $0.90 (Take Profit).
  - METAR Temp keluar dari bracket market (Stop Loss).
- **[MODUL H] Live Bridge:** Inisialisasi `ClobClient` dengan `Signature Type: 0` (EOA - Phantom).

### 🌊 WAVE 3: THE FORTRESS (Risk Management)
**Tujuan:** Melindungi modal $5 dan compounding otomatis.
- **[MODUL D] Compounding & Slot Expansion:** Stake otomatis naik ke $2 (Tier 2/Saldo > $10) dan jumlah slot market (MAX_OPEN_POSITIONS) ekspansi otomatis dari 5 ke 15+ seiring tumbuhnya saldo.
- **[MODUL L] Circuit Breaker:** Daily Loss Limit $1.50. Jika tercapai, `ENTRY_GATE_OPEN` diubah ke `False` di `state.json`.
- **[MODUL M] Backtest Runner:** Mode simulasi offline menggunakan data history 7 hari.

### 🌊 WAVE 4: THE SYSTEM (Stability)
**Tujuan:** Monitoring jarak jauh dan pemulihan state.
- **[MODUL F] Wallet Sentry:** Monitor POL < 0.2 atau USDC < $1.
- **[MODUL I] Self-Healing:** Rekonsiliasi state lokal vs L2 order history Polymarket setiap jam.
- **[MODUL J] Emergency Kill-Switch:** Dashboard button pemicu `sys.exit(0)` & `close_all_positions`.
- **[MODUL N] Mobile Alerts:** Notifikasi Telegram via `requests.post`.

---

## 🛠️ IF-THEN FAILURE ANALYSIS (Penanganan Error)

| Trigger (Masalah) | Dampak | Aksi Bot (Respon Otomatis) |
| :--- | :--- | :--- |
| NOAA API Down (404/503) | Modul B Gagal | Downgrade ke "Safe Mode": Naikkan Threshold Edge menjadi 0.40 & Alert Telegram. |
| Polymarket Signature Error | Order Gagal | Bot dilarang entry, masuk mode re-koneksi L2 Auth. |
| Insufficient Balance (400) | Gagal Entry | Alert Telegram: "RESTOCK USDC IMMEDIATELY", bot paus 1 jam. |
| Price Tick Size Mismatch | Order Ditolak | Bot otomatis membulatkan harga ke 0.001 terdekat (Rounder Logic). |
| Server Restart | State Hilang | **Self-Healing (Modul I):** Bot membaca `state.json` & fetch history order untuk bangun kembali posisi terbuka. |

---

## 📂 NEW STATE SCHEMA (Struktur Data)
Data `state.json` akan ditambahkan field berikut untuk menjamin bot tidak "amnesia":
```json
{
  "meta": {
    "daily_base_balance": 5.0,
    "daily_loss_limit": 1.5,
    "entry_gate_open": true,
    "last_noaa_check": "ISO-TIMESTAMP",
    "bot_mode": "LIVE"
  },
  "risk": {
     "circuit_breaker_active": false,
     "anomalies_detected_today": 0
  }
}
```

---

## 🏁 DRAFT PENYIAPAN (WAJIB SEBELUM GAS)
1. [ ] **PK On .env:** Masukkan Private Key Phantom ke `env`. (Bukan di kode!).
2. [ ] **Set Allowance:** Pastikan dompet sudah memberi izin (Approve) USDC ke kontrak Polymarket.
3. [ ] **Gas Check:** Minimal 0.5 POL di dompet Polygon untuk biaya cadangan.

> [!CAUTION]
> **ATURAN EMAS:** Bot ini dilarang melakukan "Market Order" (Hajar Kanan). Semua eksekusi WAJIB menggunakan "Limit Order" untuk memastikan kamu selalu mendapatkan harga terbaik sebagai Maker.
