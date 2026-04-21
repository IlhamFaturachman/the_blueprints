# THE BLUEPRINTS — Panduan Lengkap

**Versi:** 2.0 | **Tanggal:** 21 April 2026 | **Mode:** Paper Trading ($5)

---

## Daftar Isi

1. Apa Itu The Blueprints?
2. Cara Kerja Bot (Gambaran Besar)
3. Dari Mana Bot Dapat Data?
4. Bagaimana Bot Menemukan Peluang?
5. Bagaimana Bot Menilai Layak atau Tidak?
6. Bagaimana Bot Menentukan Berapa Uang yang Ditaruh?
7. Bagaimana Bot Mengelola Posisi yang Sudah Dibuka?
8. Bagaimana Bot Belajar dari Kesalahan?
9. Sistem Keamanan & Perlindungan
10. Dashboard & Monitoring
11. Infrastruktur Teknis
12. Status Saat Ini
13. Rencana ke Depan
14. Riwayat Masalah & Perbaikan

---

## 1. Apa Itu The Blueprints?

The Blueprints adalah bot trading otomatis yang bermain di **Polymarket** — platform prediksi berbasis blockchain. Bot ini fokus pada satu jenis pasar saja: **prediksi suhu cuaca**.

Contoh pertanyaan di Polymarket:

> "Apakah suhu tertinggi di Dallas pada 21 April akan mencapai 64°F atau lebih?"

Di Polymarket, kamu bisa beli "tiket" YES atau NO untuk pertanyaan seperti ini. Kalau prediksi benar, tiket bernilai $1. Kalau salah, bernilai $0.

**Ide dasarnya sederhana:** Bot membandingkan prediksi cuaca dari layanan meteorologi dengan harga tiket di pasar. Kalau pasar bilang kemungkinan cuma 58%, tapi bot hitung kemungkinan sebenarnya 95%, berarti ada selisih 37% — ini yang disebut **edge** (keunggulan). Bot beli tiket murah, tunggu sampai pasar sadar, dan jual lebih mahal atau tunggu sampai resolve.

---

## 2. Cara Kerja Bot (Gambaran Besar)

Bot berjalan non-stop 24 jam di server, dalam siklus yang berulang setiap ~10 menit. Setiap siklus mengikuti alur ini:

```
1. CARI PASAR CUACA
   Bot ambil semua pasar cuaca aktif dari Polymarket

2. FILTER YANG RELEVAN
   Buang yang sudah expired, yang bukan suhu, yang di luar golden window

3. AMBIL PREDIKSI CUACA
   Tanya Open-Meteo dan wttr.in: "Berapa suhu di Dallas besok?"

4. HITUNG PELUANG
   Bandingkan prediksi cuaca vs harga pasar → ada edge atau tidak?

5. BUKA POSISI (kalau layak)
   Kelly Criterion tentukan berapa uang yang ditaruh

6. PANTAU POSISI TERBUKA
   WebSocket real-time pantau harga setiap detik

7. TUTUP POSISI (kalau waktunya)
   7 kondisi exit yang dicek setiap siklus

8. SIMPAN & BELAJAR
   Catat hasil, update kalibrasi, bot makin pintar
```

Satu siklus penuh butuh sekitar 4-5 menit (sebagian besar waktu dihabiskan untuk mengambil data cuaca dari berbagai kota). Setelah siklus selesai, bot istirahat 5 menit, lalu mulai lagi.

---

## 3. Dari Mana Bot Dapat Data?

Bot menggunakan **6 sumber data** yang bekerja sama dalam satu pipeline keputusan. Setiap sumber punya peran spesifik — ada yang untuk prediksi, ada yang untuk validasi, ada yang untuk probabilitas statistik.

### 3.1 Data Pasar — Polymarket (2 jalur)

- **Gamma Events API** — daftar semua pasar cuaca aktif, termasuk harga, volume, dan tanggal resolve. Bot scan sampai 15 halaman (200 event per halaman) untuk memastikan tidak ada yang terlewat.
- **WebSocket Real-Time** — koneksi langsung ke Polymarket yang memberikan update harga setiap detik. Begitu bot buka posisi, harga langsung dipantau live.

### 3.2 Prediksi Cuaca — Open-Meteo (Sumber Utama)

**Endpoint:** `api.open-meteo.com/v1/forecast`

Layanan cuaca berbasis model atmosfer Eropa, sangat akurat untuk prediksi 1-3 hari ke depan. Bot mengambil `temperature_2m_max` (suhu tertinggi harian) untuk setiap kota target.

**Peran:** Sumber prediksi utama. Nilainya dirata-ratakan dengan wttr.in untuk menghasilkan "consensus forecast".

### 3.3 Prediksi Cuaca — wttr.in (Sumber Kedua)

**Endpoint:** `wttr.in/{kota}?format=j1`

Layanan cuaca agregat yang menggunakan sumber data berbeda dari Open-Meteo. Bot mengambil `maxtempC` untuk tanggal yang sama.

**Peran:** Partner konsensus. Kedua sumber **harus sepakat dalam rentang 2.5°C**. Kalau Open-Meteo bilang 22°C tapi wttr.in bilang 15°C (selisih 7°C), bot anggap prediksi tidak bisa diandalkan dan **skip pasar itu sepenuhnya**. Ini adalah filter keamanan paling ketat — lebih baik melewatkan peluang daripada trading berdasarkan data yang tidak konsisten.

Kalau kedua sumber sepakat, hasilnya dirata-ratakan: `consensus_temp = (Open-Meteo + wttr.in) / 2`.

### 3.4 Validasi Ground Truth — NOAA METAR & NWS (2 endpoint)

**Endpoint 1:** `aviationweather.gov/api/data/metar` (data METAR bandara)
**Endpoint 2:** `api.weather.gov/stations/{ICAO}/observations` (observasi NWS)

Ini bukan prediksi — ini **suhu aktual saat ini** dari stasiun cuaca bandara. Setiap kota punya kode ICAO (misalnya Dallas = KDFW, London = EGLL).

**Peran — Dua fungsi penting:**

1. **Validasi forecast (sepanjang waktu):** Kalau suhu aktual saat ini SUDAH melebihi prediksi suhu tertinggi, berarti prediksi salah → bot skip pasar itu. Contoh: prediksi bilang max 22°C tapi METAR sudah menunjukkan 23°C sekarang → prediksi jelas salah.

2. **Override probabilitas (6 jam terakhir sebelum resolve):** Dalam 6 jam terakhir, data ground truth bisa langsung mengubah probabilitas:
   - Suhu aktual sudah melewati threshold → probabilitas dinaikkan ke 95% (hampir pasti menang)
   - Suhu aktual masih jauh di bawah threshold dengan <2 jam tersisa → probabilitas diturunkan ke 15% (hampir pasti kalah)

### 3.5 Probabilitas Statistik — GFS Ensemble (31 Model)

**Endpoint:** `ensemble-api.open-meteo.com/v1/ensemble`

Ini bukan satu prediksi — ini **31 prediksi berbeda** dari 31 variasi model GFS (Global Forecast System). Setiap variasi menggunakan kondisi awal yang sedikit berbeda, menghasilkan 31 kemungkinan suhu.

**Peran:** Menghitung probabilitas secara statistik. Contoh: kalau 28 dari 31 model bilang suhu akan di atas 64°F, maka ensemble probability = 28/31 = 90.3%.

Probabilitas ensemble ini di-blend dengan probabilitas model utama:
- **45% bobot ensemble** (data statistik dari 31 model)
- **55% bobot model utama** (sigmoid/Gaussian dari consensus forecast)

Ini membuat keputusan bot lebih robust — tidak bergantung pada satu model saja.

### 3.6 Data Historis — Alarm Anomali

**Endpoint:** `archive-api.open-meteo.com/v1/archive`

Rata-rata suhu historis 10 tahun untuk setiap kota dan tanggal. Data ini di-cache di database lokal (tidak perlu diambil ulang setiap siklus).

**Peran:** Alarm anomali. Kalau prediksi cuaca terlalu jauh dari rata-rata historis (lebih dari 7°C), bot curiga ada kesalahan dan skip. Contoh: rata-rata April di London = 15°C, tapi prediksi bilang 25°C → itu mencurigakan, skip.

### 3.7 Bagaimana Semua Sumber Bekerja Sama?

Ini adalah pipeline lengkap dari data mentah sampai keputusan buka posisi:

```
Open-Meteo (22.5°C) ──┐
                       ├─→ Sepakat? (selisih ≤ 2.5°C)
wttr.in (24.0°C) ─────┘        │
                           YA: rata-rata = 23.25°C
                           TIDAK: SKIP (jangan trading)
                                │
Historical (24.4°C) ──→ Anomali? (selisih > 7°C dari 10 tahun)
                           YA: SKIP
                           TIDAK: lanjut
                                │
NOAA METAR (14°C) ────→ Sudah melebihi prediksi?
                           YA: SKIP (prediksi salah)
                           TIDAK: lanjut
                                │
                        Sigmoid/Gaussian → raw probability
                        Hard ceiling cap → max 75%/87%/94%/95%
                        Consensus guard → redam kalau "too good to be true"
                                │
NOAA METAR ───────────→ Override (hanya 6 jam terakhir)
                        Konfirmasi → boost ke 95%
                        Kontradiksi → turun ke 15%
                                │
                        Bayesian calibration (belajar dari trade sebelumnya)
                                │
GFS Ensemble (31 model) → 45% ensemble + 55% model = final probability
                                │
                        Cap absolut 95% (tidak pernah 100% yakin)
                                │
                        Edge = probability - (harga + slippage)
                                │
                        Edge ≥ 10%? → BUKA POSISI
                        Edge < 10%? → SKIP
```

**Setiap sumber punya hak veto.** Kalau satu saja menunjukkan masalah (consensus gagal, anomali historis, ground truth kontradiksi), bot tidak akan trading. Ini by design — lebih baik melewatkan 10 peluang bagus daripada masuk 1 peluang buruk.

---

## 4. Bagaimana Bot Menemukan Peluang?

### 4.1 Jenis Pasar yang Dibidik

Bot hanya tertarik pada pasar **"Suhu tertinggi di kota X pada tanggal Y"**. Setiap pertanyaan seperti ini punya banyak sub-pasar (bucket), misalnya untuk Dallas:

```
"64°F atau lebih?"     → Harga YES: $0.58  ← bot tertarik
"66°F?"                → Harga YES: $0.25  ← bot tertarik
"68°F atau lebih?"     → Harga YES: $0.08  ← terlalu murah
"60°F atau di bawah?"  → Harga YES: $0.02  ← terlalu murah
"72°F atau lebih?"     → Harga YES: $0.85  ← terlalu mahal
```

Bot menganalisis setiap bucket dan hanya tertarik pada yang harganya antara **$0.05 sampai $0.75** — zona dimana masih ada ketidakpastian yang cukup untuk menghasilkan keuntungan.

### 4.2 Kota yang Dipantau

Bot memantau **31 kota** di seluruh dunia: London, New York, Seoul, Singapore, Paris, Toronto, Dallas, Austin, Hong Kong, Madrid, Chicago, Houston, Tokyo, Sydney, dan lainnya.

### 4.3 Golden Window — Kapan Bot Aktif Mencari?

Bot tidak asal masuk kapan saja. Ada jendela waktu optimal yang disebut **Golden Window**: bot hanya membuka posisi baru kalau pasar akan resolve dalam **4 sampai 18 jam ke depan**.

Kenapa rentang ini?

- **Terlalu jauh (>18 jam):** Prediksi cuaca masih bisa berubah banyak. Risikonya terlalu tinggi.
- **Terlalu dekat (<4 jam):** Pasar sudah efisien — harga sudah mencerminkan kenyataan. Edge-nya kecil.
- **Sweet spot (4-18 jam):** Prediksi cuaca sudah cukup stabil, tapi pasar belum sepenuhnya menyesuaikan. Di sinilah edge terbesar.

Dalam praktiknya, waktu terbaik untuk menemukan peluang adalah **pagi hari WIB (05:00-11:00)** ketika pasar untuk hari itu masuk golden window.

Di luar golden window, bot tetap berjalan tapi tidak membuka posisi baru — hanya memantau posisi yang sudah terbuka.

---

## 5. Bagaimana Bot Menilai Layak atau Tidak?

### 5.1 Menghitung Probabilitas

Setelah dapat prediksi suhu, bot menghitung seberapa besar kemungkinan suatu bucket akan menang. Caranya:

- **Pasar "di atas/di bawah"** — pakai kurva sigmoid. Semakin jauh prediksi dari batas threshold, semakin yakin bot. Tapi bot juga menyesuaikan tingkat keyakinan berdasarkan berapa lama lagi pasar resolve (semakin dekat = semakin yakin, tapi tetap ada batas).

- **Pasar "persis X derajat"** — pakai kurva Gaussian (lonceng). Bot paling yakin kalau prediksi tepat di angka itu, dan keyakinan menurun seiring jarak.

**Batas Keyakinan Maksimal:**

Bot tidak pernah 100% yakin. Ada batas keras berdasarkan seberapa jauh prediksi dari threshold:

| Selisih prediksi vs threshold | Keyakinan maksimal |
|---|---|
| Kurang dari 1°C | 75% |
| 1°C sampai 2°C | 87% |
| 2°C sampai 3°C | 94% |
| Lebih dari 3°C | 95% (batas absolut) |

Kenapa? Karena model cuaca sendiri punya ketidakpastian ±2-3°C. Tidak realistis kalau bot 99% yakin hanya karena selisih 1.5°C.

### 5.2 Menghitung Edge

Edge = keyakinan bot - harga pasar.

Contoh: Bot 95% yakin Dallas akan di atas 64°F. Harga pasar $0.58 (artinya pasar cuma 58% yakin). Edge = 95% - 58% = **37%**. Ini edge yang sangat besar.

### 5.3 Tiga Syarat Masuk

Bot hanya buka posisi kalau **tiga syarat terpenuhi**:

1. **Keyakinan minimal 60%** — bot harus cukup yakin
2. **Edge minimal 10%** — selisih antara keyakinan dan harga pasar harus cukup besar
3. **Harga maksimal $0.75** — tidak beli tiket yang sudah terlalu mahal

### 5.4 Klasifikasi Peluang

Setiap peluang yang lolos filter dikategorikan:

- **Swing** — harga di zona $0.40-$0.65, ada ruang untuk profit dari pergerakan harga
- **Hold Candidate** — edge dan probabilitas sangat tinggi, layak dipegang sampai resolve
- **Watchlist** — harga terlalu murah (<$0.05), dipantau tapi tidak dibeli
- **Reject** — tidak memenuhi syarat, langsung dibuang

### 5.5 Aturan Diversifikasi

Bot tidak boleh taruh semua telur di satu keranjang:

- **Maksimal 1 posisi per kota per hari** — kalau prediksi cuaca satu kota salah, kerugian terbatas
- **Maksimal 1 posisi per event** — tidak boleh beli "64°F atau lebih" DAN "66°F" untuk Dallas sekaligus
- **Dari semua kandidat, bot pilih yang terbaik** berdasarkan: keyakinan tertinggi → edge terbesar → harga termurah

---

## 6. Bagaimana Bot Menentukan Berapa Uang yang Ditaruh?

### 6.1 Kelly Criterion — Taruhan Proporsional

Bot menggunakan **Kelly Criterion** — rumus matematika yang menentukan ukuran taruhan optimal berdasarkan edge dan probabilitas. Prinsipnya: semakin besar edge, semakin besar taruhan. Tapi tidak pernah terlalu besar.

Rumusnya (disederhanakan):

```
Taruhan = Uang tersedia x Kelly Factor x 20%

Kelly Factor = (probabilitas x odds - (1 - probabilitas)) / odds
```

Bot menggunakan **20% dari full Kelly** (disebut "fractional Kelly") — ini jauh lebih konservatif dari yang optimal secara matematis, tapi lebih aman. Lebih baik lambat tapi selamat.

### 6.2 Batas-Batas Keamanan

| Pengaturan | Nilai | Penjelasan |
|---|---|---|
| Kelly Fraction | 20% | Hanya pakai 20% dari rekomendasi Kelly |
| Taruhan minimum | $1.00 | Tidak buka posisi kalau taruhan di bawah $1 |
| Taruhan maksimum | $10.00 | Tidak pernah taruh lebih dari $10 per posisi |
| Edge minimum | 3% | Kelly tidak akan taruh kalau edge di bawah 3% |
| Kas cadangan | 50% | Selalu sisakan minimal 50% kas untuk posisi berikutnya |

### 6.3 Slot Posisi — Berapa Posisi Bisa Dibuka?

Jumlah posisi yang bisa dibuka bersamaan tergantung ukuran wallet:

| Ukuran Wallet | Maksimal Posisi |
|---|---|
| $5 - $7 | 3 posisi |
| $8 - $11 | 5 posisi |
| $12 - $19 | 8 posisi |
| $20+ | 15 posisi |

Ini bukan batasan kaku — Kelly Criterion yang menentukan apakah kas cukup untuk posisi baru. Kalau kas tinggal $1.50 dan overhead per posisi ~$1.05, bot masih bisa buka 1 posisi lagi. Tapi kalau kas tinggal $0.80, bot berhenti — tidak cukup untuk taruhan minimum $1.00.

### 6.4 Transisi dari Paper ke Live

Semua pengaturan Kelly sudah dinamis. Saat transisi dari paper ($5) ke live ($7), **tidak perlu ubah kode apapun**. Kelly otomatis menyesuaikan:

- $5 wallet → Kelly taruh ~$1.00 per posisi (minimum)
- $7 wallet → Kelly taruh ~$1.00-$1.10 per posisi
- $50 wallet → Kelly taruh ~$2-$5 per posisi (tergantung edge)

Cukup ubah satu angka di file konfigurasi: `PAPER_BASE_WALLET=7.0`.

---

## 7. Bagaimana Bot Mengelola Posisi yang Sudah Dibuka?

### 7.1 Pemantauan Harga Real-Time

Begitu posisi dibuka, bot langsung subscribe ke WebSocket Polymarket untuk token tersebut. Setiap ada perubahan harga (bahkan perubahan $0.01), bot langsung tahu. Dashboard juga menampilkan harga live ini.

### 7.2 Tujuh Kondisi Penutupan (Hybrid Exit)

Bot punya 7 alasan untuk menutup posisi, dicek berurutan dari yang paling penting:

**1. Exit Sniper — Harga Tembus $0.90**
Kalau harga YES tiba-tiba naik ke $0.90 atau lebih, langsung jual. Harga setinggi itu artinya pasar sudah hampir pasti — ambil untung sekarang daripada tunggu resolve.

**2. Sniper Stop Loss — Prediksi Cuaca Berubah**
Kalau prediksi cuaca tidak lagi mendukung posisi (dua sumber tidak sepakat, atau prediksi bergeser drastis), tutup posisi. Alasan masuk sudah tidak berlaku.

**3. Trailing Stop — Kunci Keuntungan**
Aktif kalau posisi pernah naik +20% atau lebih. Setelah itu, kalau harga turun kembali ke harga entry, jual di break-even. Prinsipnya: "sudah pernah untung, jangan sampai jadi rugi."

**4. Hard Stop Loss — Potong Rugi**
Kalau harga turun ke 48% dari harga entry, potong rugi. Contoh: beli di $0.50 → stop loss di $0.24.

Ada **cooldown 2 jam** — stop loss tidak aktif selama 2 jam pertama setelah posisi dibuka. Ini memberi ruang untuk spread awal settle.

**5. Take Profit — Target Tercapai**
Target standar: 2x harga entry (bisa diatur via konfigurasi). Beli $0.30 → target $0.60. Beli $0.58 → target $1.00 (resolve sebagai YES).

**6. Late Window — 2 Jam Sebelum Resolve**
Saat tinggal 2 jam sebelum pasar tutup:
- Forecast masih valid dan posisi untung → tahan sampai resolve
- Forecast tidak valid atau posisi rugi → jual sekarang

**7. Flash Crash Shield — Perlindungan dari Harga Palsu**
Kadang harga di WebSocket bisa "spike" — turun drastis sesaat lalu kembali normal. Bot punya 4 lapis perlindungan:
- Layer 1: Deteksi spike (harga turun >30% dalam 1 detik)
- Layer 2: Cek apakah spike konsisten dalam beberapa tick
- Layer 3: Verifikasi via REST API (bukan WebSocket)
- Layer 4: Cek kedalaman orderbook

Kalau spike terdeteksi sebagai palsu, bot abaikan dan tidak trigger stop loss.

### 7.3 Whiplash Shield — Jangan Masuk Lagi Setelah Keluar Paksa

Setelah posisi ditutup oleh stop loss, kota tersebut masuk **blacklist 24 jam**. Bot tidak akan buka posisi baru di kota yang sama hari itu.

Alasannya: setelah exit paksa, kondisi pasar di kota itu dianggap tidak kondusif. Masuk lagi langsung ("churning") cuma buang fee tanpa edge.

---

## 8. Bagaimana Bot Belajar dari Kesalahan?

### 8.1 Kalibrasi Probabilitas

Setiap trade yang selesai dicatat hasilnya. Bot menghitung **Brier Score** — ukuran seberapa akurat prediksi probabilitasnya. Semakin banyak data, semakin akurat kalibrasi.

Contoh: kalau bot selalu bilang "90% yakin" untuk Dallas tapi ternyata cuma menang 70% dari waktu, bot akan menurunkan keyakinannya untuk Dallas di masa depan.

Kalibrasi ini mulai efektif setelah ~30 trade terkumpul.

### 8.2 Auto-Tuner — Grading Per Kota

Berdasarkan rekam jejak per kota (minimal 3 trade), bot mengategorikan:

- **A (Aggressive)** — kota ini sering menang, bot lebih berani (threshold edge diturunkan)
- **C (Cautious)** — kota ini sering kalah, bot lebih hati-hati (threshold edge dinaikkan)
- **B (Blacklist)** — kerugian berturut-turut, kota ini dilewati sementara

Grading ini otomatis dan terus diperbarui setiap siklus.

### 8.3 Laporan Atribusi

Setiap 7 hari, bot membuat laporan yang menunjukkan:
- Kota mana yang paling menguntungkan
- Strategi mana (swing vs hold) yang lebih berhasil
- Alasan penutupan mana yang paling sering terjadi

Laporan ini dikirim via Telegram.

---

## 9. Sistem Keamanan & Perlindungan

### 9.1 Circuit Breaker

Kalau total kerugian hari ini melampaui batas (15% dari wallet), bot berhenti membuka posisi baru untuk hari itu. Posisi yang sudah terbuka tetap dipantau dan bisa ditutup, tapi tidak ada posisi baru.

### 9.2 Leverage Cap — Jangan Habiskan Semua Uang

Bot selalu cek: "apakah kas yang tersisa cukup untuk minimal 1 posisi lagi?" Kalau tidak, gate ditutup. Ini mencegah bot menghabiskan semua kas dan tidak punya cadangan.

### 9.3 Validasi Prediksi Ganda

- **Konsensus dua sumber** — Open-Meteo dan wttr.in harus sepakat dalam ±2.5°C
- **Anomaly check** — prediksi tidak boleh >7°C dari rata-rata historis 10 tahun
- **NOAA METAR** — untuk kota yang punya stasiun cuaca, data ground-truth digunakan sebagai validasi tambahan

### 9.4 Consensus Guard

Kalau bot sangat yakin (>90%) tapi harga pasar sangat rendah (<30%), bot meredam keyakinannya. Ini mencegah bot ngotot melawan konsensus pasar tanpa alasan yang sangat kuat.

### 9.5 Perlindungan Data

- Semua data disimpan di **SQLite** (`blueprints_master.db`) dengan mode WAL (Write-Ahead Logging) untuk keamanan
- Mirror JSON (`paper_positions_5usd.json`) sebagai backup
- Kalau bot restart, semua posisi terbuka tetap terlacak
- PID lock mencegah dua instance bot berjalan bersamaan
- `base_wallet` hanya bisa diubah via reset script — tidak bisa terkorupsi oleh bug

---

## 10. Dashboard & Monitoring

Dashboard web tersedia di `http://103.253.244.158:8080/web_ui/`

### 10.1 Tiga Kartu Utama

| Kartu | Apa yang Ditampilkan |
|---|---|
| **Portfolio** | Total nilai portofolio (kas + nilai posisi terbuka) |
| **Today's PnL** | Keuntungan/kerugian hari ini (floating + realized) |
| **Win Rate** | Persentase trade yang menang, rata-rata PnL per trade |

### 10.2 Status Bar

Satu baris yang menampilkan:
- **Gate status** — ACTIVE (bot boleh buka posisi) atau TRIPPED (bot berhenti sementara)
- **Bot health** — Healthy (hijau), In cycle (kuning), atau STALE (merah, ada masalah)
- **Jam WIB** — waktu Jakarta saat ini
- **Next cycle** — estimasi kapan siklus berikutnya dimulai, dengan progress bar

### 10.3 Kartu Posisi

Setiap posisi terbuka ditampilkan sebagai kartu besar dengan:
- **Nama kota dan pertanyaan pasar**
- **Countdown resolve** — "Resolves in 8h 30m"
- **Price progress bar** — visual yang menunjukkan posisi harga saat ini antara Stop Loss dan Take Profit, dengan marker di harga entry
- **Metrik** — PnL, probabilitas, edge, cost
- **Tombol** — link ke Polymarket dan tombol "Entry Logic" untuk melihat alasan bot masuk

Posisi diurutkan berdasarkan **urgency** (yang paling dekat resolve di atas), bukan berdasarkan PnL.

### 10.4 Sumber Data Dashboard

Dashboard mengambil data dari dua sumber:
1. **Primary: `/api/state`** — data live langsung dari database bot (selalu fresh)
2. **Fallback: JSON file** — kalau bot sedang mati, dashboard tampilkan data terakhir yang tersimpan

Harga posisi diupdate real-time via WebSocket — tidak perlu refresh halaman.

---

## 11. Infrastruktur Teknis

### 11.1 Server

- VPS di Jakarta, IP `103.253.244.158`
- Aktif 24 jam sebagai systemd service
- Otomatis restart kalau crash
- Nginx sebagai reverse proxy (port 8080 → dashboard, `/api/` → bot command server)

### 11.2 Komponen yang Berjalan

| Komponen | Port | Fungsi |
|---|---|---|
| Bot utama | - | Siklus trading (discovery → entry → exit → learn) |
| Command Server | 8083 | API untuk dashboard dan kill switch |
| WS Broadcaster | 8081 | Relay harga live ke dashboard browser |
| WS Price Watcher | - | Koneksi ke Polymarket WebSocket untuk pantau harga |
| Data Warmer | - | Background thread yang pre-fetch data cuaca historis |
| Nginx | 8080 | Serve dashboard HTML + proxy API requests |

### 11.3 Database

SQLite (`logs/blueprints_master.db`) menyimpan:
- Posisi aktif dan historis
- Rekam jejak semua trade (untuk pembelajaran)
- Statistik kalibrasi per kota
- Cycle metrics (log setiap siklus)
- Portfolio summary (wallet, kas, PnL)

### 11.4 AI — Dinonaktifkan

Bot sebelumnya menggunakan Claude Haiku untuk tiga fungsi (entry review, position monitor, market sensing). Semua fitur AI telah **dinonaktifkan** per 21 April 2026.

Alasan:
- Model deterministik (sigmoid + ensemble + Kelly) sudah cukup akurat
- AI Monitor pernah menyebabkan kerugian dengan menutup posisi yang sedang profit (insiden Seoul)
- Menghilangkan biaya API ($4.80/bulan) dan failure mode (API error, rate limit)
- Bot menjadi sepenuhnya deterministik dan reproducible

Kode AI masih ada di codebase (dormant) — bisa diaktifkan kembali via konfigurasi kalau diperlukan di masa depan.

---

## 12. Status Saat Ini

### Per 21 April 2026

| Komponen | Status |
|---|---|
| Bot | Aktif berjalan di server |
| Mode | Paper Trading (simulasi) |
| Modal awal | $5.00 USD |
| Stake per posisi | Ditentukan Kelly Criterion (minimum $1.00) |
| Maks posisi terbuka | 3 (untuk wallet $5) |
| Golden Window | 4-18 jam sebelum resolve |
| AI/Haiku | Dinonaktifkan (semua 3 fitur) |
| Kelly Criterion | Aktif (20% fractional Kelly) |
| WebSocket | Aktif dengan IPv4 force |
| Dashboard | Redesign baru — kartu posisi, progress bar, mobile responsive |
| Nginx | Aktif — proxy API + serve dashboard |

### Pengaturan Kunci

| Setting | Nilai | Keterangan |
|---|---|---|
| `PAPER_BASE_WALLET` | $5.00 | Modal paper trading |
| `PAPER_STAKE_USD` | $1.00 | Fallback kalau Kelly disabled |
| `PAPER_MAX_OPEN_POSITIONS` | 5 | Batas konfigurasi (tier system bisa lebih rendah) |
| `GOLDEN_WINDOW_HOURS_MIN` | 4 jam | Batas bawah golden window |
| `GOLDEN_WINDOW_HOURS_MAX` | 18 jam | Batas atas golden window |
| `STRATEGY_MIN_MODEL_PROB` | 60% | Keyakinan minimum untuk masuk |
| `STRATEGY_MIN_EDGE` | 10% | Edge minimum untuk masuk |
| `STRATEGY_MAX_YES_PRICE` | $0.75 | Harga maksimal untuk beli |
| `KELLY_FRACTION` | 0.20 | 20% dari full Kelly |
| `KELLY_MIN_STAKE` | $1.00 | Taruhan minimum |
| `KELLY_MAX_STAKE` | $10.00 | Taruhan maksimum |
| `HYBRID_STOP_LOSS_MULTIPLIER` | 0.48 | Stop loss di 48% dari entry |
| `TAKE_PROFIT_PRICE_CAP` | $1.00 | Batas atas target profit |

---

## 13. Rencana ke Depan

### Phase A — Data Collection (7 hari: 21-27 April)

**Tujuan:** Kumpulkan data trading sebanyak mungkin untuk melatih sistem pembelajaran.

- Bot berjalan dengan $5 paper money
- Target: 20-30 closed trades dalam 7 hari
- Sistem kalibrasi dan auto-tuner mulai mengumpulkan data
- Kalau wallet habis sebelum 7 hari, **biarkan saja** — data kerugian sama berharganya dengan data keuntungan untuk pembelajaran

**Indikator sukses:** Bot berjalan stabil tanpa crash, data terkumpul, kalibrasi mulai terbentuk.

### Transisi ke Live (Hari ke-8)

Prosesnya sangat sederhana:

```bash
systemctl stop blueprints
python scripts/reset_warehouse.py --wallet 7.0 --keep-learning
# Edit .env: PAPER_BASE_WALLET=7.0
systemctl start blueprints
```

Flag `--keep-learning` mempertahankan semua data kalibrasi (Brier scores, auto-tuner city grades) sambil mereset wallet dan posisi. Bot mulai live dengan $7 DAN kecerdasan dari 7 hari paper trading.

**Tidak perlu ubah kode apapun.** Kelly Criterion otomatis menyesuaikan ukuran taruhan untuk wallet $7.

### Phase B — Live Trading ($7)

- Modal awal: $7.00 USD (uang asli)
- Kelly menentukan ukuran taruhan per posisi
- Sistem pembelajaran terus berjalan dan makin akurat
- Target: win rate > 55%, profit konsisten

### Jangka Panjang

1. **Execution bridge** — Saat ini bot hanya simulasi (paper trading). Untuk live, perlu ~70 baris kode tambahan di `execution.py` untuk menghubungkan ke Polymarket CLOB API dan benar-benar menempatkan order. Infrastruktur sudah siap (py-clob-client terinstall, wrapper class sudah ada).

2. **Tambah sumber prediksi ketiga** — Saat ini bot pakai Open-Meteo + wttr.in sebagai dua sumber prediksi (NOAA METAR sudah aktif tapi sebagai validasi ground-truth, bukan prediksi). Menambah sumber prediksi independen ketiga (misalnya Tomorrow.io atau ECMWF langsung) akan membuat consensus lebih robust.

3. **Perluas kota** — Tambah kota yang sering muncul di Polymarket berdasarkan data yang terkumpul.

4. **Optimasi Golden Window per kota** — Setelah cukup data, analisis jam berapa edge paling besar per kota dan sesuaikan window secara dinamis (bukan satu window global untuk semua kota).

5. **Multi-timeframe ensemble** — Saat ini hanya pakai GFS ensemble. Menambah ECMWF ensemble (51 member) akan memberikan probabilitas statistik yang lebih akurat.

---

## 14. Riwayat Masalah & Perbaikan

### 14.1 Insiden Seoul — 19 April 2026

**Apa yang terjadi:** Bot masuk posisi Seoul di $0.53. Harga naik ke $0.68 (+28% profit). Tapi WebSocket freeze karena masalah IPv6 → harga di database stuck di $0.52 → AI Monitor (Haiku) hitung PnL = -1.9% → Haiku panic close → posisi ditutup rugi padahal sedang profit.

**Perbaikan:**
- WebSocket dipaksa IPv4 (VPS tidak punya routing IPv6)
- AI Monitor dinonaktifkan (penyebab utama kerugian)
- Newborn Guard dan Profit Guard ditambahkan sebagai pengaman tambahan

### 14.2 State Reset Bug — 21 April 2026

**Apa yang terjadi:** Setelah reset wallet ke $5, bot langsung mengembalikan wallet ke $100 pada siklus berikutnya.

**Penyebab:** Fungsi `load_paper_state()` menggabungkan semua data dari cycle_metrics terakhir ke dalam state, termasuk `base_wallet` lama ($100). Ini menimpa nilai $5 yang baru di-reset.

**Perbaikan:**
- Hanya data operasional (bukan finansial) yang boleh digabungkan dari cycle_metrics
- `base_wallet` sekarang dibaca dari database sebagai satu-satunya sumber kebenaran
- Default konfigurasi diubah dari $100 ke $5

### 14.3 Range Regex Bug — 21 April 2026

**Apa yang terjadi:** Bot masuk posisi Dallas dengan threshold 21°F (salah) padahal pertanyaan pasar adalah "64°F atau lebih?"

**Penyebab:** Slug pasar mengandung tanggal "april-21-2026". Regex range matcher mencocokkan "21-2026" sebagai rentang suhu "21°F sampai 2026°F".

**Perbaikan:**
- Range regex sekarang hanya mencari di teks pertanyaan, bukan di slug
- Ditambahkan sanity check: tolak kalau threshold > 200 (tidak mungkin suhu segitu)

### 14.4 Insufficient Cash False Positive — 21 April 2026

**Apa yang terjadi:** Dengan wallet $5, bot menolak membuka posisi apapun karena "insufficient_cash" — padahal kas masih cukup.

**Penyebab:** Tier system mengalokasikan 5 slot x $1 x 1.575 overhead = $7.87, yang melebihi $5 wallet. Formula leverage cap membagi kas dengan SEMUA slot, bukan slot yang tersisa.

**Perbaikan:**
- Tier system sekarang hanya mengatur jumlah slot (bukan ukuran taruhan)
- Kelly Criterion menentukan ukuran taruhan per posisi
- Leverage cap disederhanakan: "apakah kas cukup untuk minimal 1 posisi?"

---

*Dokumen ini adalah referensi lengkap The Blueprints Trading Bot. Terakhir diperbarui 21 April 2026 setelah redesign arsitektur Kelly-first, perbaikan regex, dan redesign dashboard.*
