# THE BLUEPRINTS — Panduan Lengkap

**Versi:** 1.0 | **Tanggal:** 22 April 2026 | **Mode:** Paper Trading ($5)

---

## Daftar Isi

1. Apa Itu The Blueprints?
2. Cara Kerja Bot (Gambaran Besar)
3. Dari Mana Bot Dapat Data?
4. Bagaimana Bot Menemukan Peluang?
5. Bagaimana Bot Menilai Layak atau Tidak?
6. Bagaimana Bot Menentukan Berapa Uang yang Ditaruh?
7. Bagaimana Bot Mengelola Posisi yang Sudah Dibuka?
8. Execution Bridge — Eksekusi Order di Polymarket
9. Bagaimana Bot Belajar dari Kesalahan?
10. Sistem Keamanan & Perlindungan
11. Dashboard & Monitoring
12. Infrastruktur Teknis
13. Pengaturan Kunci
14. Rencana ke Depan

---

## 1. Apa Itu The Blueprints?

The Blueprints adalah bot trading otomatis yang bermain di **Polymarket** — platform prediksi berbasis blockchain. Bot ini fokus pada satu jenis pasar saja: **prediksi suhu cuaca**.

Bot mendukung **tiga jenis pasar suhu**:

> "Apakah suhu **tertinggi** di Dallas pada 22 April akan mencapai 64°F atau lebih?" *(above/below)*
> "Apakah suhu **tertinggi** di Paris pada 22 April akan tepat 17°C?" *(exact bracket)*
> "Apakah suhu **terendah** di London pada 23 April akan di bawah 5°C?" *(lowest temperature)*

Di Polymarket, kamu bisa beli "tiket" YES atau NO untuk pertanyaan seperti ini. Kalau prediksi benar, tiket bernilai $1. Kalau salah, bernilai $0.

**Ide dasarnya sederhana:** Bot membandingkan prediksi cuaca dari layanan meteorologi dengan harga tiket di pasar. Kalau pasar bilang kemungkinan cuma 58%, tapi bot hitung kemungkinan sebenarnya 95%, berarti ada selisih 37% — ini yang disebut **edge** (keunggulan). Bot beli tiket murah, tunggu sampai pasar sadar, dan jual lebih mahal atau tunggu sampai resolve.

---

## 2. Cara Kerja Bot (Gambaran Besar)

Bot berjalan non-stop 24 jam di server, dalam siklus yang berulang setiap ~1-2 menit. Setiap siklus mengikuti alur ini:

```
1. CARI PASAR CUACA
   Bot ambil semua pasar cuaca aktif dari Polymarket (~1300 pasar)

2. FILTER YANG RELEVAN
   Buang yang sudah expired, yang bukan suhu, yang di luar golden window
   Deteksi jenis: highest/lowest temperature, above/below/exact

3. AMBIL PREDIKSI CUACA
   Tanya Open-Meteo dan wttr.in: "Berapa suhu di Dallas besok?"
   Untuk highest → ambil temperature_2m_max
   Untuk lowest → ambil temperature_2m_min

4. HITUNG PELUANG
   Bandingkan prediksi cuaca vs harga pasar → ada edge atau tidak?
   Sigmoid untuk above/below, Gaussian untuk exact bracket

5. BUKA POSISI (kalau layak)
   Kelly Criterion tentukan berapa uang yang ditaruh
   Cek likuiditas orderbook sebelum masuk
   Paper mode: simulasi instan | Live mode: maker order ke CLOB

6. PANTAU POSISI TERBUKA
   WebSocket real-time pantau harga setiap detik
   Live mode: monitor pending orders (fill/timeout/retry)

7. TUTUP POSISI (kalau waktunya)
   7 kondisi exit yang dicek setiap siklus
   Urgent exit (stop loss) → taker order (FOK, langsung)
   Normal exit (take profit) → maker order (post-only, 0% fee)

8. SIMPAN & BELAJAR
   Catat hasil, update kalibrasi, bot makin pintar
```

Satu siklus penuh butuh sekitar 1-2 menit berkat sistem caching dan bulk prefetch. Setelah siklus selesai, bot istirahat 5 menit, lalu mulai lagi.

---

## 3. Dari Mana Bot Dapat Data?

Bot menggunakan **6 sumber data** yang bekerja sama dalam satu pipeline keputusan. Setiap sumber punya peran spesifik — ada yang untuk prediksi, ada yang untuk validasi, ada yang untuk probabilitas statistik.

### 3.1 Data Pasar — Polymarket (2 jalur)

- **Gamma Events API** — daftar semua pasar cuaca aktif, termasuk harga, volume, dan tanggal resolve. Bot scan sampai 15 halaman (200 event per halaman) untuk memastikan tidak ada yang terlewat.
- **WebSocket Real-Time** — koneksi langsung ke Polymarket yang memberikan update harga setiap detik. Begitu bot buka posisi, harga langsung dipantau live.

### 3.2 Prediksi Cuaca — Open-Meteo (Sumber Utama)

**Endpoint:** `api.open-meteo.com/v1/forecast`

Layanan cuaca berbasis model atmosfer Eropa, sangat akurat untuk prediksi 1-3 hari ke depan. Bot mengambil:
- `temperature_2m_max` untuk pasar **suhu tertinggi** (highest temperature)
- `temperature_2m_min` untuk pasar **suhu terendah** (lowest temperature)

**Peran:** Sumber prediksi utama. Nilainya di-blend dengan wttr.in untuk menghasilkan "consensus forecast".

### 3.3 Prediksi Cuaca — wttr.in (Sumber Kedua)

**Endpoint:** `wttr.in/{kota}?format=j1`

Layanan cuaca agregat yang menggunakan sumber data berbeda dari Open-Meteo. Bot mengambil:
- `maxtempC` untuk pasar suhu tertinggi
- `mintempC` untuk pasar suhu terendah

**Peran:** Partner konsensus. Kedua sumber **harus sepakat dalam rentang 2.5°C**. Kalau Open-Meteo bilang 22°C tapi wttr.in bilang 15°C (selisih 7°C), bot anggap prediksi tidak bisa diandalkan dan **skip pasar itu sepenuhnya**.

Kalau kedua sumber sepakat, hasilnya di-blend dengan bobot: `consensus_temp = (Open-Meteo × 64% + wttr.in × 36%)`. Open-Meteo mendapat bobot lebih besar karena API-nya lebih reliable dan akurat.

### 3.4 Validasi Ground Truth — NOAA METAR (Cached)

**Endpoint:** `aviationweather.gov/api/data/metar`

Ini bukan prediksi — ini **suhu aktual saat ini** dari stasiun cuaca bandara. Setiap kota punya kode ICAO (misalnya Dallas = KDFW, London = EGLL). Hasil METAR di-cache selama 5 menit per stasiun untuk menghindari API call berulang.

**Peran — Dua fungsi penting (hanya untuk pasar suhu tertinggi):**

1. **Validasi forecast:** Kalau suhu aktual saat ini SUDAH melebihi prediksi suhu tertinggi, berarti prediksi salah → bot skip.

2. **Override probabilitas (6 jam terakhir sebelum resolve):** Data ground truth bisa langsung mengubah probabilitas:
   - Suhu aktual sudah melewati threshold → probabilitas dinaikkan ke 95%
   - Suhu aktual masih jauh di bawah threshold dengan <2 jam tersisa → probabilitas diturunkan ke 15%

> **Catatan:** NOAA override dinonaktifkan untuk pasar suhu terendah karena METAR melaporkan suhu saat ini (bukan suhu minimum harian). Suhu minimum biasanya terjadi dini hari, bukan saat pengukuran METAR.

### 3.5 Probabilitas Statistik — Multi-Model Ensemble (82 Model)

**Endpoint:** `ensemble-api.open-meteo.com/v1/ensemble`

Ini bukan satu prediksi — ini **82 prediksi berbeda** dari dua sistem model cuaca terbaik di dunia:

- **GFS Ensemble (31 member)** — Global Forecast System dari NOAA (Amerika)
- **ECMWF IFS Ensemble (51 member)** — European Centre for Medium-Range Weather Forecasts

Ensemble mendukung kedua jenis suhu:
- Untuk highest → menggunakan `temperature_2m_max` dari 82 model
- Untuk lowest → menggunakan `temperature_2m_min` dari 82 model

Probabilitas ensemble di-blend dengan probabilitas model utama:
- **45% bobot ensemble** (data statistik dari 82 model)
- **55% bobot model utama** (sigmoid/Gaussian dari consensus forecast)

### 3.6 Data Historis — Alarm Anomali

**Endpoint:** `archive-api.open-meteo.com/v1/archive`

Rata-rata suhu historis 10 tahun untuk setiap kota dan tanggal. Bot menyimpan **kedua** rata-rata (max dan min) di database lokal.

**Peran:** Alarm anomali. Kalau prediksi cuaca terlalu jauh dari rata-rata historis (lebih dari 7°C), bot curiga ada kesalahan dan skip. Untuk pasar suhu tertinggi, dibandingkan dengan rata-rata max historis. Untuk suhu terendah, dibandingkan dengan rata-rata min historis.

### 3.7 Bagaimana Semua Sumber Bekerja Sama?

```
Open-Meteo (22.5°C) ──┐
                       ├─→ Sepakat? (selisih ≤ 2.5°C)
wttr.in (24.0°C) ─────┘        │
                           YA: blend 64/36 = 23.1°C
                           TIDAK: SKIP (jangan trading)
                                │
Historical (24.4°C) ──→ Anomali? (selisih > 7°C dari 10 tahun)
                           YA: SKIP
                           TIDAK: lanjut
                                │
NOAA METAR ───────────→ Validasi (hanya untuk highest temp)
                           Sudah melebihi prediksi? → SKIP
                                │
                        Sigmoid/Gaussian → raw probability
                        Hard ceiling cap → max 75%/87%/94%/95%
                        Consensus guard → redam kalau "too good to be true"
                                │
NOAA METAR ───────────→ Override (hanya 6 jam terakhir, hanya highest)
                        Konfirmasi → boost ke 95%
                        Kontradiksi → turun ke 15%
                                │
                        Bayesian calibration (belajar dari trade sebelumnya)
                                │
GFS+ECMWF Ensemble ──→ 45% ensemble + 55% model = final probability
                                │
                        Cap absolut 95% (tidak pernah 100% yakin)
                                │
                        Edge = probability - harga pasar
                                │
                        Edge ≥ threshold? → BUKA POSISI
                        Edge < threshold? → SKIP
```

**Setiap sumber punya hak veto.** Kalau satu saja menunjukkan masalah, bot tidak akan trading.

---

## 4. Bagaimana Bot Menemukan Peluang?

### 4.1 Tiga Jenis Pasar yang Dibidik

Bot mendukung tiga jenis pasar suhu:

| Jenis | Contoh | Model Probabilitas | Take Profit |
|---|---|---|---|
| **Above/Below** | "Suhu tertinggi ≥ 64°F?" | Sigmoid | 2x entry |
| **Exact Bracket** | "Suhu tertinggi tepat 17°C?" | Gaussian | 8x entry |
| **Lowest Temperature** | "Suhu terendah < 5°C?" | Sigmoid | 2x entry |

Bot menganalisis setiap bucket dan hanya tertarik pada yang harganya antara **$0.05 sampai $0.75**.

### 4.2 Kota yang Dipantau

Bot memantau **31 kota** di seluruh dunia: London, New York, Seoul, Singapore, Paris, Toronto, Dallas, Austin, Hong Kong, Madrid, Chicago, Houston, Tokyo, Sydney, dan lainnya.

### 4.3 Golden Window — Kapan Bot Aktif Mencari?

Bot hanya membuka posisi baru kalau pasar akan resolve dalam **4 sampai 18 jam ke depan**.

- **Terlalu jauh (>18 jam):** Prediksi cuaca masih bisa berubah banyak.
- **Terlalu dekat (<4 jam):** Pasar sudah efisien — edge-nya kecil.
- **Sweet spot (4-18 jam):** Prediksi sudah stabil, tapi pasar belum menyesuaikan.

**Jadwal Golden Window (WIB):**

Pasar cuaca Polymarket resolve pada **12:00 UTC (19:00 WIB)**.

| Event | UTC | WIB |
|-------|-----|-----|
| Golden window buka | 18:00 (hari sebelumnya) | **01:00** |
| Peluang pagi | 22:00-02:00 | **05:00-09:00** |
| Zona puncak peluang | 02:00-06:00 | **09:00-13:00** |
| Golden window tutup | 08:00 | **15:00** |
| Pasar resolve | 12:00 | **19:00** |

### 4.4 Aturan Diversifikasi

- **Maksimal 1 posisi per kota per jenis suhu per hari** — kota yang sama bisa punya posisi highest DAN lowest (karena ini event independen)
- **Maksimal 1 posisi per event** — tidak boleh beli "64°F atau lebih" DAN "66°F" untuk Dallas sekaligus
- **Dari semua kandidat, bot pilih yang terbaik** berdasarkan: keyakinan tertinggi → edge terbesar → harga termurah

---

## 5. Bagaimana Bot Menilai Layak atau Tidak?

### 5.1 Menghitung Probabilitas

- **Pasar "di atas/di bawah"** — pakai kurva **sigmoid**. Semakin jauh prediksi dari threshold, semakin yakin bot. Tingkat keyakinan disesuaikan berdasarkan waktu sampai resolve.

- **Pasar "persis X derajat"** — pakai kurva **Gaussian** (lonceng). Bot paling yakin kalau prediksi tepat di angka itu. Sigma disesuaikan per region dan per musim:
  - Kota tropis: σ = 1.0°C (stabil sepanjang tahun)
  - Kota 4-musim: σ = 2.0°C × seasonal multiplier (Maret-April: 1.35x, Juli-Agustus: 0.90x)
  - Default: σ = 1.5°C

**Batas Keyakinan Maksimal (hanya above/below):**

| Selisih prediksi vs threshold | Keyakinan maksimal |
|---|---|
| < 1°C | 75% |
| 1-2°C | 87% |
| 2-3°C | 94% |
| > 3°C | 95% (batas absolut) |

Exact-bracket markets tidak menggunakan batas ini karena probabilitasnya sudah rendah secara alami (10-38%).

### 5.2 Syarat Masuk — Berbeda per Jenis Pasar

| Syarat | Above/Below | Exact Bracket |
|---|---|---|
| Keyakinan minimal | 60% | 10% |
| Edge minimal | 10% | 2% |
| Harga maksimal | $0.75 | $0.75 |
| Regime gates | Aktif (min_prob 72%, min_edge 22%) | Dilewati (prob exact terlalu rendah untuk regime gates) |

### 5.3 Klasifikasi Peluang

- **Swing** — harga di zona $0.15-$0.75, ada ruang untuk profit. Ini bucket utama.
- **Hold Candidate** — edge dan probabilitas sangat tinggi, layak dipegang sampai resolve
- **Watchlist** — harga terlalu murah (<$0.15), dipantau tapi tidak dibeli
- **Reject** — tidak memenuhi syarat

Exact-bracket markets selalu masuk sebagai **Swing** (karena harganya murah $0.03-$0.15 tapi edge-nya besar).

---

## 6. Bagaimana Bot Menentukan Berapa Uang yang Ditaruh?

### 6.1 Kelly Criterion

Bot menggunakan **20% dari full Kelly** (fractional Kelly) — konservatif tapi aman.

```
Taruhan = Uang tersedia × Kelly Factor × 20%
Kelly Factor = (probabilitas × odds - (1 - probabilitas)) / odds
```

### 6.2 Batas Keamanan

| Pengaturan | Nilai |
|---|---|
| Kelly Fraction | 20% |
| Taruhan minimum | $1.00 |
| Taruhan maksimum | $10.00 |
| Edge minimum (Kelly) | 3% |
| Kas cadangan | 50% |

### 6.3 Slot Posisi

| Ukuran Wallet | Maksimal Posisi |
|---|---|
| $5 - $7 | 3 posisi |
| $8 - $11 | 5 posisi |
| $12 - $19 | 8 posisi |
| $20+ | 15 posisi |

### 6.4 Validasi Likuiditas

Sebelum membuka posisi, bot mengecek **kedalaman orderbook** via CLOB API (timeout 4 detik, 1 retry). Kalau orderbook terlalu tipis atau spread terlalu lebar (>$0.12), posisi tidak dibuka. Ini memastikan paper trading mensimulasikan kondisi pasar nyata.

---

## 7. Bagaimana Bot Mengelola Posisi yang Sudah Dibuka?

### 7.1 Pemantauan Real-Time

Begitu posisi dibuka, bot subscribe ke WebSocket Polymarket. Setiap perubahan harga langsung terdeteksi. Dashboard menampilkan harga live.

### 7.2 Validasi Forecast Berkelanjutan

Setiap siklus, bot mengecek apakah prediksi cuaca masih mendukung posisi yang terbuka. Threshold validasi berbeda per jenis pasar:

| Jenis | Forecast Valid | Forecast Lemah | Forecast Invalid |
|---|---|---|---|
| Above/Below | prob ≥ 70% → 1.0 | prob 50-70% → graduated | prob < 50% → 0.0 (tutup) |
| Exact Bracket | prob ≥ 20% → 1.0 | prob 5-20% → graduated | prob < 5% → 0.0 (tutup) |

### 7.3 Confidence Score

Bot menghitung skor kepercayaan (0-1) untuk setiap posisi terbuka:

```
Score = 70% × probabilitas + 20% × edge_component + 10% × price_component
```

Untuk exact-bracket markets, probabilitas dinormalisasi dari range 0.05-0.40 ke 0.0-1.0 agar confidence score realistis.

Di late window (2 jam terakhir), bot menggunakan **entry_edge** (edge saat masuk) bukan current_edge, untuk mencegah confidence turun saat pasar konvergen ke harga benar.

### 7.4 Tujuh Kondisi Penutupan (Hybrid Exit)

**1. Exit Sniper — Harga Tembus $0.90**
Langsung jual. Pasar sudah hampir pasti.

**2. Sniper Stop Loss — Prediksi Cuaca Berubah**
Forecast tidak lagi mendukung posisi → tutup.

**3. Trailing Stop — Kunci Keuntungan**
Aktif setelah posisi naik +20%. Kalau harga turun kembali ke entry → jual di break-even.

**4. Hard Stop Loss — Potong Rugi**
Harga turun ke 48% dari entry → potong rugi. Cooldown 2 jam setelah entry.

**5. Take Profit — Target Tercapai**

| Jenis Pasar | Target |
|---|---|
| Above/Below | 2x entry (beli $0.30 → target $0.60) |
| Exact Bracket | 8x entry (beli $0.05 → target $0.40) |

Exact bracket menggunakan target lebih tinggi karena entry murah ($0.03-$0.10) tapi resolusi membayar $1.00. Target 8x menangkap profit signifikan tanpa harus menunggu resolusi.

**6. Late Window — 2 Jam Sebelum Resolve**
- Confidence ≥ 55% → tahan sampai resolve
- Confidence < 55% → jual sekarang
- Confidence < 35% → thesis decay exit

**7. Flash Crash Shield — 4 Lapis Perlindungan**
- Layer 1: Deteksi spike (harga turun >40% dalam 1 detik)
- Layer 2: Cek konsistensi dalam beberapa tick (30 detik)
- Layer 3: Verifikasi via REST API
- Layer 4: Cek kedalaman orderbook

### 7.5 Settlement (Resolusi)

Kalau posisi ditahan sampai resolusi (hours ≤ 0):
- Forecast validity ≥ 0.5 → settle di $1.00 (menang)
- Forecast validity < 0.5 → settle di $0.00 (kalah)

### 7.6 Whiplash Shield

Setelah stop loss, kota tersebut masuk **blacklist 24 jam**. Mencegah churning (masuk-keluar berulang tanpa edge).

---

## 8. Execution Bridge — Eksekusi Order di Polymarket

Bot beroperasi dalam **dua mode** yang dikontrol via satu flag konfigurasi:

| Mode | `LIVE_TRADING_ENABLED` | Apa yang terjadi |
|---|---|---|
| **Paper Trading** | `false` (default) | Simulasi dengan fee structure identik live (maker 0%, taker ~2%) |
| **Live Trading** | `true` | Order nyata via Polymarket CLOB API — uang sungguhan |

Kedua mode menjalankan logika trading yang **identik** — discovery, edge calculation, Kelly sizing, exit decisions. Perbedaannya hanya pada eksekusi: paper mode mensimulasikan order secara instan, live mode mengirim order nyata ke Polymarket melalui **Execution Bridge** (`BlueprintsExchange`). Fee structure di paper mode sudah mirror live: entry dan normal exit menggunakan maker fee (0%), urgent exit menggunakan taker fee (~2%).

### 8.1 Arsitektur Execution Bridge

```
BlueprintsExchange (execution.py)
├── __init__()          → L1 auth → derive API creds → L2 auth (creds=)
├── start_heartbeat()   → daemon thread, POST /heartbeat setiap 5 detik
├── stop()              → cancel semua order, stop heartbeat
├── place_maker_buy()   → GTC post-only limit BUY (0% fee)
├── place_taker_buy()   → FOK market BUY (taker fee)
├── place_maker_sell()  → GTC post-only limit SELL (0% fee)
├── place_taker_sell()  → FOK market SELL (taker fee)
├── cancel_order()      → cancel satu order
├── cancel_all_orders() → cancel semua order (orphan cleanup)
├── get_order_status()  → cek fill status order
├── get_open_orders()   → daftar semua order aktif
├── get_orderbook_info()→ best bid/ask, spread, tick size, min order size
├── compute_maker_buy_price()  → best_bid + 1 tick
└── compute_maker_sell_price() → best_ask - 1 tick
```

Semua method thread-safe (dilindungi `threading.Lock`). Semua error ditangkap — tidak pernah crash. Kalau ada kegagalan, method mengembalikan `{"success": False, "reason": "..."}`.

### 8.2 Dua Jenis Order

Bot menggunakan **dua jenis order** tergantung urgensi:

| Jenis | Kapan Dipakai | Fee | Kecepatan |
|---|---|---|---|
| **Maker (post-only)** | Entry, take profit, normal exit | **0%** | Lambat (menunggu fill) |
| **Taker (FOK)** | Stop loss, flash crash, urgent exit | **~2%** | Instan |

**Mengapa maker order?** Di Polymarket, maker order (limit order yang menambah likuiditas) tidak dikenakan fee. Taker order (yang mengambil likuiditas) dikenakan fee ~2%. Dengan menggunakan maker order untuk entry dan normal exit, bot menghemat fee secara signifikan.

**Mengapa taker untuk urgent exit?** Saat stop loss terpicu, kecepatan lebih penting dari fee. Menunggu maker order terisi bisa berarti kerugian lebih besar dari fee yang dihemat.

### 8.3 Alur Entry (Live Mode)

```
Kandidat lolos filter → Kelly sizing → cek min order size
    │
    ├─ shares < minimum? → SKIP (terlalu kecil)
    │
    ├─ Ambil orderbook → hitung maker price (best_bid + 1 tick)
    │
    ├─ maker price >= best_ask? → SKIP (spread terlalu sempit)
    │
    ├─ estimated cost > cash? → SKIP (uang tidak cukup)
    │
    └─ Place maker buy (GTC, post-only=True)
        │
        ├─ Berhasil → posisi status = "pending_entry"
        │              order dipantau setiap siklus
        │
        └─ Gagal → SKIP (log error, lanjut ke kandidat berikut)
```

Posisi `pending_entry` dipantau oleh **Order Monitor** di awal setiap siklus:
- **Terisi penuh** → status berubah ke `open`, entry_fee = $0 (maker)
- **Terisi sebagian** → kalau cukup besar (≥ min shares), terima; kalau terlalu kecil, cancel
- **Timeout (5 menit)** → cancel order, hapus posisi
- **Dibatalkan exchange** → hapus posisi

### 8.4 Alur Exit (Live Mode)

```
Hybrid Exit Decision = "sell"
    │
    ├─ Alasan URGENT? (stop_loss, hard_stop_loss, trailing_stop,
    │                   trailing_stop_breakeven, flash_crash_exit,
    │                   sniper_stop_loss_thesis_broken)
    │   │
    │   └─ Place taker sell (FOK) → langsung terisi → posisi ditutup
    │
    └─ Alasan NORMAL? (take_profit, late_window, dll)
        │
        └─ Place maker sell (GTC, post-only=True)
            │
            ├─ Berhasil → posisi status = "pending_exit"
            │              order dipantau setiap siklus
            │
            └─ Gagal → fallback ke taker sell
```

Posisi `pending_exit` dipantau oleh Order Monitor:
- **Terisi** → posisi ditutup, exit_fee = $0 (maker)
- **Tidak terisi setelah 60 detik** → retry dengan harga lebih rendah (max 3 retry)
- **Max retry tercapai** → fallback ke taker sell (bayar fee, tapi pasti terisi)

### 8.5 Heartbeat & Keamanan

Polymarket CLOB API memerlukan **heartbeat** aktif. Kalau heartbeat berhenti, semua open order bisa dibatalkan oleh exchange.

- Bot mengirim heartbeat setiap **5 detik** via daemon thread
- Kalau **3 heartbeat berturut-turut gagal**, bot menandai `heartbeat_healthy = False`
- Saat shutdown (SIGTERM), bot otomatis cancel semua open order

**Orphan Cleanup:** Saat startup, bot langsung menjalankan `cancel_all_orders()` untuk membersihkan order sisa dari sesi sebelumnya (misalnya setelah crash). Posisi `pending_entry` dihapus, posisi `pending_exit` direset ke `open`.

### 8.6 Double-Sell Prevention

Karena exit bisa dipicu dari **dua jalur** (main cycle dan WebSocket callback), ada risiko double-sell — dua order sell untuk posisi yang sama.

Pencegahan:
- Flag `_exit_in_progress` di-set saat exit dimulai
- Kedua jalur mengecek flag ini sebelum menempatkan order
- Kalau flag sudah `True`, exit di-skip (posisi sudah dalam proses penutupan)

### 8.7 Paper Mode vs Live Mode

Paper trading mensimulasikan fee structure yang identik dengan live trading, sehingga hasil paper trading bisa langsung dijadikan benchmark akurat untuk live.

| Aspek | Paper Mode | Live Mode |
|---|---|---|
| Entry | Simulasi instan, harga = best_ask | Maker order, harga = best_bid + 1 tick |
| Entry fee | **$0** (maker, 0% fee) | **$0** (maker, 0% fee) |
| Normal exit | Simulasi instan, harga = best_bid | Maker order (post-only) |
| Normal exit fee | **$0** (maker, 0% fee) | **$0** (maker, 0% fee) |
| Urgent exit | Simulasi instan, harga = best_bid | Taker order (FOK, instan) |
| Urgent exit fee | **~2%** (taker fee) | **~2%** (taker fee) |
| Pending status | Tidak ada (simulasi instan) | `pending_entry`, `pending_exit` |
| Order monitoring | Tidak ada | Setiap siklus (fill/timeout/retry) |
| Heartbeat | Tidak ada | Setiap 5 detik |
| Orphan cleanup | Tidak ada | Saat startup |

> **Catatan:** Satu-satunya perbedaan kecil adalah entry price — paper menggunakan `best_ask`, live menggunakan `best_bid + 1 tick` (sedikit lebih murah). Ini membuat paper trading sedikit lebih konservatif dari live, yang merupakan safety margin yang baik.

### 8.8 Catatan Teknis: py_clob_client API

Execution bridge menggunakan library `py_clob_client` (v0.34.6) dengan beberapa keputusan desain penting:

1. **Two-step order flow untuk maker orders** — Bot menggunakan `create_order()` → `post_order(signed, post_only=True)`, bukan `create_and_post_order()`. Method `create_and_post_order()` tidak mendukung `post_only` parameter, sehingga semua order akan menjadi taker (bayar fee). Two-step flow memastikan order benar-benar post-only (0% fee).

2. **`amount` untuk MarketOrderArgs, `size` untuk OrderArgs** — Limit order (`OrderArgs`) menggunakan field `size` (jumlah shares). Market order (`MarketOrderArgs`) menggunakan field `amount` (BUY = dollar amount, SELL = jumlah shares).

3. **Two-step flow untuk taker orders juga** — `create_market_order()` hanya menandatangani order, tidak mengirimnya. Harus panggil `post_order()` terpisah untuk mengirim ke exchange.

4. **Level 2 auth wajib** — Semua operasi trading memerlukan API credentials. Bot melakukan `create_or_derive_api_creds()` saat startup, lalu pass `creds=` ke `ClobClient` constructor.

---

## 9. Bagaimana Bot Belajar dari Kesalahan?

### 9.1 Kalibrasi Probabilitas (Bayesian)

Setiap trade yang selesai dicatat hasilnya. Bot menghitung **Brier Score** per kota, per direction, per horizon. Kalibrasi mulai efektif setelah ~30 trade.

### 9.2 Auto-Tuner — Grading Per Kota

| Grade | Arti | Efek |
|---|---|---|
| A (Aggressive) | Kota sering menang | Threshold edge diturunkan |
| C (Cautious) | Kota sering kalah | Threshold edge dinaikkan |
| B (Blacklist) | Kerugian berturut-turut | Kota dilewati sementara |

### 9.3 Laporan Atribusi (Mingguan)

Setiap 7 hari, bot mengirim laporan via Telegram: kota terbaik/terburuk, strategi terbaik, alasan penutupan paling sering.

---

## 10. Sistem Keamanan & Perlindungan

### 10.1 Circuit Breaker
Kerugian harian > 15% dari wallet → bot berhenti membuka posisi baru untuk hari itu.

### 10.2 Leverage Cap
Selalu cek: "apakah kas cukup untuk minimal 1 posisi lagi?" Kalau tidak, gate ditutup.

### 10.3 Validasi Prediksi Ganda
- Konsensus dua sumber (±2.5°C)
- Anomaly check (±7°C dari historis 10 tahun)
- NOAA METAR ground-truth (untuk highest temp)

### 10.4 Consensus Guard
Kalau bot >90% yakin tapi harga pasar <30%, bot meredam keyakinannya. Hanya untuk above/below markets.

### 10.5 Perlindungan Data
- SQLite WAL mode + JSON mirror backup
- PID lock (mencegah dua instance)
- `base_wallet` hanya bisa diubah via reset script
- Test isolation: pytest tidak bisa menulis ke database produksi

### 10.6 METAR Cache
Hasil NOAA METAR di-cache 5 menit per stasiun ICAO. Thread-safe dengan lock. Mencegah 300+ API call berulang per siklus.

---

## 11. Dashboard & Monitoring

Dashboard web: `http://103.253.244.158:8080/web_ui/`

### 11.1 Tiga Kartu Utama

| Kartu | Isi |
|---|---|
| **Portfolio** | Total nilai (kas + posisi terbuka) |
| **Today's PnL** | Keuntungan/kerugian hari ini |
| **Win Rate** | Persentase menang, rata-rata PnL per trade |

### 11.2 Status Bar
Gate status, bot health, jam WIB, next cycle countdown.

### 11.3 Kartu Posisi
Nama kota, countdown resolve, price progress bar (SL → Entry → TP), metrik (PnL, prob, edge, cost), link ke Polymarket.

### 11.4 Data Source
Primary: `/api/state` (live dari DB). Fallback: JSON file. Harga real-time via WebSocket.

---

## 12. Infrastruktur Teknis

### 12.1 Server
- VPS Jakarta (Depa Cloud), IP `103.253.244.158`
- Debian 12, 1 CPU, 1GB RAM, 20GB storage
- systemd service dengan auto-restart

### 12.2 Komponen

| Komponen | Fungsi |
|---|---|
| Bot utama | Siklus trading (discovery → entry → exit → learn) |
| Execution Bridge | `BlueprintsExchange` — koneksi ke Polymarket CLOB API untuk order nyata |
| Command Server (8083) | API untuk dashboard dan kill switch |
| WS Broadcaster (8081) | Relay harga live ke browser |
| WS Price Watcher | Koneksi ke Polymarket WebSocket |
| Data Warmer | Background pre-fetch data historis |
| Nginx (8080) | Serve dashboard + proxy API |

### 12.3 Database
SQLite (`logs/blueprints_master.db`): posisi, trade history, kalibrasi, cycle metrics, portfolio, weather archive (max + min temp), discovery cache.

### 12.4 AI — Dinonaktifkan
Claude Haiku (entry review, position monitor, market sensing) dinonaktifkan. Bot sepenuhnya deterministik.

---

## 13. Pengaturan Kunci

### 13.1 Trading Parameters

| Setting | Nilai | Keterangan |
|---|---|---|
| `PAPER_BASE_WALLET` | $5.00 | Modal paper trading |
| `GOLDEN_WINDOW_HOURS_MIN` | 4 jam | Batas bawah golden window |
| `GOLDEN_WINDOW_HOURS_MAX` | 18 jam | Batas atas golden window |
| `STRATEGY_MIN_MODEL_PROB` | 60% | Keyakinan minimum (above/below) |
| `STRATEGY_EXACT_MIN_MODEL_PROB` | 10% | Keyakinan minimum (exact bracket) |
| `STRATEGY_MIN_EDGE` | 10% | Edge minimum (above/below) |
| `STRATEGY_EXACT_MIN_EDGE` | 2% | Edge minimum (exact bracket) |
| `STRATEGY_MAX_YES_PRICE` | $0.75 | Harga maksimal untuk beli |
| `ENTRY_BUCKET_WATCH_MAX_PRICE` | $0.15 | Batas atas watchlist |

### 13.2 Risk Management

| Setting | Nilai | Keterangan |
|---|---|---|
| `KELLY_FRACTION` | 0.20 | 20% dari full Kelly |
| `KELLY_MIN_STAKE` | $1.00 | Taruhan minimum |
| `KELLY_MAX_STAKE` | $10.00 | Taruhan maksimum |
| `HYBRID_STOP_LOSS_MULTIPLIER` | 0.48 | Stop loss di 48% dari entry |
| `HYBRID_TAKE_PROFIT_MULTIPLIER` | 2.0 | Take profit 2x (above/below) |
| `EXACT_BRACKET_TP_MULTIPLIER` | 8.0 | Take profit 8x (exact bracket) |
| `HYBRID_MIN_CONFIDENCE_TO_HOLD` | 0.55 | Confidence minimum untuk hold |
| `THESIS_DECAY_THRESHOLD` | 0.35 | Confidence di bawah ini = exit |
| `CIRCUIT_BREAKER_DAILY_LOSS_PCT` | 15% | Batas kerugian harian |

### 13.3 Forecast & Calibration

| Setting | Nilai | Keterangan |
|---|---|---|
| `CONSENSUS_MAX_ERROR_C` | 2.5°C | Batas selisih Open-Meteo vs wttr.in |
| `HISTORICAL_DEVIATION_C` | 7°C | Batas anomali dari historis 10 tahun |
| `ENSEMBLE_WEIGHT` | 0.45 | Bobot ensemble (vs 0.55 model) |
| `POINT_FORECAST_WEIGHT` | 0.35 | Bobot Open-Meteo dalam blend |
| `WTRIN_WEIGHT` | 0.20 | Bobot wttr.in dalam blend |
| `SIGMA_TROPICAL` | 1.0°C | Gaussian sigma kota tropis |
| `SIGMA_FOUR_SEASON` | 2.0°C | Gaussian sigma kota 4-musim |
| `SIGMA_DEFAULT` | 1.5°C | Gaussian sigma default |

### 13.4 Feature Flags

| Flag | Default | Keterangan |
|---|---|---|
| `LIVE_TRADING_ENABLED` | false | Gate utama live trading (true = order nyata) |
| `LOWEST_TEMP_MARKETS_ENABLED` | true | Trading pasar suhu terendah |
| `TIME_DECAY_EDGE_ENABLED` | false | Scaling edge berdasarkan waktu (dormant) |
| `NOAA_OVERRIDE_ENABLED` | true | Override probabilitas dari METAR |
| `ENSEMBLE_ENABLED` | true | Gunakan 82-model ensemble |

### 13.5 Execution Bridge

| Setting | Default | Keterangan |
|---|---|---|
| `LIVE_TRADING_ENABLED` | `false` | Gate utama — `true` untuk order nyata |
| `PREFER_MAKER_ORDERS` | `true` | Prioritaskan maker order (0% fee) |
| `MAKER_ORDER_TIMEOUT_S` | `300` | Timeout pending order (5 menit) |
| `MAKER_SELL_MAX_RETRIES` | `3` | Maksimal retry maker sell sebelum taker fallback |
| `MAKER_SELL_RETRY_INTERVAL_S` | `60` | Interval antar retry (60 detik) |
| `SIGNATURE_TYPE` | `1` | 1 = POLY_PROXY (Magic Link wallet) |
| `PRIVATE_KEY` | (wajib isi) | Private key wallet Polygon |
| `FUNDER_ADDRESS` | (wajib isi) | Funder address untuk POLY_PROXY |

---

## 14. Rencana ke Depan

### Phase A — Paper Trading (Sekarang)

Bot berjalan dengan $5 paper money dalam mode `LIVE_TRADING_ENABLED=false`. Fee structure sudah identik dengan live (maker 0%, taker ~2% untuk urgent exit). Target: 20-30 closed trades untuk validasi kalibrasi dan auto-tuner.

### Transisi ke Live (v2.0)

Setelah paper trading menunjukkan hasil stabil:

```bash
# 1. Pastikan paper trading sudah stabil (win rate, PnL, no errors)
# 2. Update .env: LIVE_TRADING_ENABLED=true
# 3. Restart
systemctl restart blueprints
# 4. Monitor 2-3 siklus pertama — pastikan order terisi
# 5. Cek Telegram alerts untuk konfirmasi entry/exit
```

Saat `LIVE_TRADING_ENABLED=true`, bot beralih ke eksekusi nyata:
- Entry menggunakan **maker buy** (0% fee, harga = best_bid + 1 tick)
- Normal exit menggunakan **maker sell** (0% fee)
- Urgent exit menggunakan **taker sell** (FOK, ~2% fee)
- Heartbeat aktif setiap 5 detik
- Orphan cleanup otomatis saat startup

### Phase B — Live Trading ($7)

Kelly otomatis menyesuaikan. Target: win rate > 55%, profit konsisten.

### Jangka Panjang

1. **NO-side trading** — Trading sisi NO (10 dari 11 bucket kalah setiap hari)
2. **Full backtesting suite** — Replay engine dengan equity curves
3. **Per-city golden window** — Optimasi jam terbaik per kota
4. **Perluas kota** — Tambah kota berdasarkan data yang terkumpul
5. **Multi-exchange support** — Ekspansi ke prediction market lain

---

*Dokumen ini adalah referensi lengkap The Blueprints Trading Bot v1.0. Terakhir diperbarui 22 April 2026.*
