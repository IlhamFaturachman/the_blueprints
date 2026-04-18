# THE BLUEPRINTS — Panduan Lengkap Bot Trading Cuaca
**Versi:** 1.0 | **Tanggal:** 18 April 2026 | **Mode aktif:** Paper Trading

---

## Daftar Isi

1. Apa Itu The Blueprints?
2. Bagaimana Bot Ini Bekerja (Gambaran Besar)
3. Sumber Data
4. Cara Bot Menemukan Peluang
5. Cara Bot Menilai Apakah Layak Masuk
6. Cara Bot Membuka Posisi
7. Cara Bot Mengelola & Menutup Posisi
8. Sistem Pembelajaran Mandiri
9. Perlindungan & Manajemen Risiko
10. Infrastruktur & Komponen Teknis
11. Dasbor & Monitoring
12. Status Saat Ini & Roadmap

---

## 1. Apa Itu The Blueprints?

The Blueprints adalah bot trading otomatis yang beroperasi di **Polymarket** — sebuah platform prediksi pasar berbasis blockchain. Bot ini secara khusus menargetkan pasar cuaca, yaitu pertanyaan seperti:

> *"Apakah suhu tertinggi di London pada 19 April akan mencapai 16°C?"*

Di Polymarket, siapapun bisa membeli "tiket" untuk menjawab YA atau TIDAK terhadap pertanyaan semacam itu. Jika prediksi benar, tiket bernilai $1. Jika salah, bernilai $0.

Bot ini bekerja dengan cara membandingkan **prediksi cuaca dari layanan meteorologi** dengan **harga tiket di pasar**. Jika pasar menilai kemungkinan suatu kejadian hanya 25%, tapi bot menghitung kemungkinan sebenarnya adalah 65%, maka ada keuntungan potensial sebesar 40 sen per dolar — inilah yang disebut *edge* atau keunggulan.

---

## 2. Bagaimana Bot Ini Bekerja (Gambaran Besar)

Bot berjalan dalam siklus terus-menerus, seperti jam tangan yang berdetak. Setiap siklus berlangsung sekitar 2–5 menit dan mengikuti alur berikut:

```
AMBIL DATA PASAR
      ↓
FILTER PASAR YANG RELEVAN
      ↓
AMBIL PREDIKSI CUACA
      ↓
HITUNG PELUANG & KEUNTUNGAN
      ↓
BUKA POSISI (jika layak)
      ↓
PANTAU POSISI TERBUKA
      ↓
TUTUP POSISI (jika waktunya)
      ↓
SIMPAN DATA → BELAJAR
```

Bot hanya benar-benar aktif mencari peluang dalam **golden window** — jendela waktu tertentu setiap hari dimana kondisi pasar paling optimal.

---

## 3. Sumber Data

Bot menggunakan tiga kategori sumber data:

### 3.1 Data Pasar (Polymarket)
Bot mengambil data dari Polymarket melalui dua jalur:

- **Gamma API** — mengambil daftar semua pasar cuaca yang aktif, termasuk harga saat ini, volume perdagangan, dan tanggal berakhir
- **WebSocket Real-Time** — koneksi langsung ke Polymarket untuk memantau perubahan harga posisi yang sedang terbuka secara instan

### 3.2 Data Cuaca (Prediksi)
Untuk setiap pasar yang ditemukan, bot meminta prediksi suhu dari dua layanan independen:

- **Open-Meteo** — layanan cuaca berbasis model atmosfer Eropa (ERA5), sangat akurat untuk prediksi 1–3 hari ke depan
- **wttr.in** — layanan cuaca agregat yang menggunakan sumber berbeda

Bot hanya mempercayai prediksi jika kedua layanan **sepakat dalam rentang 2.5°C**. Jika keduanya berbeda lebih dari itu, prediksi dianggap tidak dapat diandalkan dan pasar tersebut dilewati.

### 3.3 Data Historis (Referensi)
Bot menyimpan rata-rata suhu historis 10 tahun untuk setiap kota target. Data ini digunakan sebagai "alarm" — jika prediksi cuaca terlalu jauh dari rata-rata historis (lebih dari 7°C), bot mencurigai ada kesalahan dan mengabaikan prediksi tersebut.

Data historis disimpan di database lokal dan tidak perlu diambil ulang setiap siklus — hanya diperbarui sebulan sekali.

---

## 4. Cara Bot Menemukan Peluang

### 4.1 Pasar yang Dibidik
Bot hanya tertarik pada satu jenis pasar: **"Suhu tertinggi di kota X pada tanggal Y"**.

Setiap pertanyaan semacam ini terdiri dari 11 sub-pasar (bucket), misalnya untuk London:
- "Apakah akan 13°C atau di bawahnya?" → Harga YES: $0.001
- "Apakah akan 14°C?" → Harga YES: $0.002
- "Apakah akan 15°C?" → Harga YES: $0.010
- "Apakah akan 16°C?" → Harga YES: $0.217 ← menarik
- "Apakah akan 17°C?" → Harga YES: $0.750
- "Apakah akan 18°C?" → Harga YES: $0.051 ← menarik
- dst.

Bot menganalisis setiap bucket secara terpisah dan hanya tertarik pada yang harganya antara **$0.05 sampai $0.65** — zona dimana masih ada ketidakpastian yang cukup untuk menghasilkan keuntungan.

### 4.2 Kota Target
Bot saat ini memantau **31 kota** di seluruh dunia, termasuk London, New York, Tokyo, Seoul, Singapore, Dubai, Paris, Toronto, Sydney, dan lainnya.

### 4.3 Golden Window — Jendela Waktu Terbaik
Berdasarkan analisis data aktual Polymarket, bot hanya mencari peluang pada **jam 05:00–11:00 WIB** setiap hari.

Alasannya:
- Polymarket menerbitkan pasar baru setiap hari sekitar jam 05:00 WIB
- Semua pasar berakhir (resolve) jam 19:00 WIB
- Pada rentang 05:00–11:00 WIB, pasar masih dalam kondisi **8–14 jam sebelum berakhir** — cukup waktu untuk prediksi bermakna, tapi cukup dekat untuk hasil cuaca mulai terlihat

Di luar jam tersebut, bot tetap berjalan tetapi tidak membuka posisi baru.

---

## 5. Cara Bot Menilai Apakah Layak Masuk

### 5.1 Menghitung Probabilitas
Setelah mendapat prediksi suhu dari Open-Meteo dan wttr.in, bot menggunakan rumus matematika untuk menghitung seberapa besar kemungkinan suatu bucket akan menang:

- **Pasar "di atas/di bawah"** — menggunakan kurva sigmoid. Semakin jauh prediksi dari batas threshold, semakin tinggi keyakinan bot
- **Pasar "persis X derajat"** — menggunakan kurva Gaussian (lonceng). Bot paling yakin jika prediksi tepat di angka tersebut, dan keyakinan menurun seiring jarak

### 5.2 Dua Syarat Masuk
Bot hanya membuka posisi jika **dua syarat terpenuhi bersamaan**:

**Syarat 1 — Tingkat Keyakinan Minimum**
Bot harus yakin minimal **60%** bahwa prediksinya benar (sebelumnya 70%, diturunkan 18 April 2026 untuk mengumpulkan data paper trade).

**Syarat 2 — Keuntungan Potensial Minimum**
Selisih antara keyakinan bot dan harga pasar harus minimal **20%** setelah dikurangi biaya transaksi (slippage). Artinya jika bot 65% yakin tapi harga pasar sudah $0.50, keuntungan potensial hanya 13% — tidak cukup, dilewati.

### 5.3 Regime Pasar
Bot juga mengevaluasi "kondisi pasar" (regime) berdasarkan tren harga historis dan volatilitas. Ada tiga kondisi:

- **Trending** — pasar sedang bergerak kuat ke satu arah, threshold diperketat
- **Neutral** — kondisi normal, threshold standar
- **Volatile** — pasar tidak menentu, threshold diperketat lebih jauh

---

## 6. Cara Bot Membuka Posisi

### 6.1 Sistem Bucket/Kategori
Setiap peluang yang lolos filter dikategorikan ke dalam dua bucket:

- **Swing** — peluang jangka pendek, harga sedang bergerak cepat, target profit lebih cepat
- **Hold Candidate** — peluang untuk dipegang lebih lama, harga lebih stabil

### 6.2 Aturan Diversifikasi
Bot menerapkan aturan ketat untuk menghindari terlalu terkonsentrasi di satu tempat:

- **Maksimal 1 posisi per kota per hari** — mencegah kehilangan besar jika prediksi cuaca satu kota salah
- **Target minimal 5 kota berbeda** per siklus aktif
- **Maksimal 1 posisi per event** — tidak boleh beli 16°C DAN 17°C London sekaligus karena itu saling mengkanibal

### 6.3 Ukuran Posisi
Saat ini dalam mode paper trading, setiap posisi menggunakan **$5 USD** (simulasi). Dalam live trading nanti, ukuran posisi bisa disesuaikan berdasarkan besarnya edge — semakin besar keunggulan, semakin besar taruhan (Kelly Criterion).

### 6.4 Bagaimana Bot Memilih 5 dari 90 Peluang?

Bayangkan bot menemukan 90 peluang yang sudah lolos filter edge dan probabilitas. Bukan berarti semua langsung dibeli — ada proses seleksi ketat berikutnya:

**Tahap 1 — Klasifikasi Bucket**
Setiap peluang diberi label:
- `reject` — harga di luar rentang valid (terlalu mahal atau terlalu murah) → **langsung dibuang**
- `watchlist` — harga terlalu murah (<$0.05), potensi profit terlalu kecil → **dibuang, hanya dipantau**
- `enter_swing` — harga OK, edge OK → **kandidat**
- `enter_hold_candidate` — edge DAN probabilitas keduanya sangat tinggi → **kandidat prioritas utama**

Sebagian besar dari 90 peluang gugur di tahap ini.

**Tahap 2 — Anti-Korelasi: Satu Kota, Satu Hari, Satu Posisi**
Dari kandidat yang tersisa, berlaku aturan: satu kota + satu tanggal hanya boleh menghasilkan **1 posisi**. Jika London 19 April punya 3 bucket yang lolos, hanya yang terbaik yang diambil. Sisanya dibuang.

Ini mencegah kerugian ganda jika prediksi cuaca satu kota meleset.

**Tahap 3 — Ranking**
Kandidat yang lolos diurutkan berdasarkan tiga kriteria secara berurutan:
1. **Confidence** (probabilitas model tertinggi) — prioritas utama
2. **Edge terbesar** — selisih keyakinan vs harga pasar
3. **Harga YES lebih murah** — harga lebih rendah = ruang keuntungan lebih besar

**Tahap 4 — Slot Tersedia**
Bot hanya membuka posisi sebanyak slot yang tersedia (`PAPER_MAX_OPEN_POSITIONS`, default: 3). Jika sudah ada 1 posisi terbuka, hanya 2 slot tersisa yang diisi dari ranking terbaik.

**Tahap 5 — Review Claude Haiku**
Setiap kandidat final dikirim ke Claude Haiku untuk "second opinion". Haiku bisa memveto keputusan bot jika ada sesuatu yang mencurigakan. Jika diveto, slot tersebut tidak terisi.

**Contoh nyata:**
```
Bucket 17°C ($0.750) → reject (terlalu mahal, >batas maksimum)
Bucket 18°C ($0.051) → watchlist (terlalu murah, hanya dipantau)
Bucket 16°C ($0.217) → enter_swing ✓ → masuk ranking → Haiku approve → BELI
```
Meskipun pasar "mayoritas" bilang 17°C adalah jawaban benar (harga $0.750), bot justru menemukan nilai di bucket lain yang dianggap pasar kurang mungkin — itulah sumber edge-nya.

---

## 7. Cara Bot Mengelola & Menutup Posisi

### 7.1 Pemantauan Harga Real-Time
Setelah posisi terbuka, bot memantau harga melalui WebSocket — koneksi langsung yang mendapat update harga dalam hitungan detik, bukan menunggu siklus berikutnya.

### 7.2 Kondisi Penutupan Posisi
Bot menutup posisi jika salah satu kondisi terpenuhi:

- **Take Profit** — harga naik signifikan dari harga beli, bot "ambil untung" lebih awal
- **Stop Loss** — harga turun di bawah batas tertentu, bot "potong kerugian" sebelum makin parah
- **Waktu Habis** — pasar akan segera berakhir (resolve), bot keluar dan menunggu hasil resmi
- **Rekomendasi AI** — bot menggunakan Claude Haiku (AI) untuk menganalisis kondisi pasar dan memberikan rekomendasi apakah sebaiknya keluar lebih awal

### 7.3 Penutupan Berbasis AI (Haiku Monitor)
Secara berkala, Claude Haiku menganalisis setiap posisi terbuka dan memberikan skor kepercayaan. Jika skor turun di bawah ambang tertentu, bot mempertimbangkan untuk keluar meskipun belum mencapai target profit.

---

## 8. Sistem Pembelajaran Mandiri

Bot dirancang untuk semakin pintar seiring berjalannya waktu melalui dua mekanisme:

### 8.1 Kalibrasi Probabilitas
Setiap trade yang selesai dicatat: bot yakin berapa persen, dan apakah hasilnya benar? Data ini dikumpulkan per kota, per arah (atas/bawah/tepat), dan per jarak waktu ke resolve.

Setelah minimal 5 data per kategori terkumpul, bot mulai "mengoreksi dirinya sendiri" — jika selama ini bot 70% yakin tapi kenyataannya hanya menang 50%, bot akan menyesuaikan perhitungannya ke depan.

### 8.2 Auto-Tuner Per Kota (A/C/B)
Berdasarkan rekam jejak trading per kota, bot mengategorikan setiap kota ke dalam tiga status:

- **A (Aggressive)** — kota dengan win rate tinggi, bot lebih agresif mencari peluang di kota ini
- **C (Cautious)** — kota dengan win rate rendah, bot lebih selektif
- **B (Blacklist)** — kota dengan terlalu banyak kerugian berturut-turut, dilewati sepenuhnya untuk siklus tersebut

Sistem ini baru aktif setelah minimal **3 trade per kota** terkumpul.

---

## 9. Perlindungan & Manajemen Risiko

### 9.1 Circuit Breaker
Jika total kerugian dalam satu hari melampaui batas tertentu, bot otomatis berhenti membuka posisi baru untuk hari tersebut. Seperti pemutus arus listrik — melindungi dari kerugian berantai.

### 9.2 Daily Target Gate
Bot melacak performa harian. Jika target harian sudah tercapai, bot bisa berhenti membuka posisi baru untuk mengamankan keuntungan.

### 9.3 Anti-Korelasi
Bot tidak boleh memiliki dua posisi yang "berlawanan" dalam event yang sama — misalnya tidak bisa membeli "London 16°C" dan "London 17°C" sekaligus, karena jika satu menang, yang lain pasti kalah.

### 9.4 Validasi Prediksi Ganda
Setiap prediksi cuaca divalidasi dari dua sisi:
- **Konsensus dua sumber** — Open-Meteo dan wttr.in harus sepakat
- **Anomaly check** — prediksi tidak boleh terlalu jauh dari rata-rata historis 10 tahun

### 9.5 Perlindungan Data
Semua data posisi disimpan dengan backup otomatis. Jika bot restart, posisi yang sedang terbuka tetap terlacak dan tidak hilang.

---

## 10. Infrastruktur & Komponen Teknis

### 10.1 Database Terpusat
Semua data bot tersimpan dalam satu database SQLite (`blueprints_master.db`), mencakup:
- Posisi aktif dan historis
- Rekam jejak trade (untuk pembelajaran)
- Data cuaca historis 31 kota
- Statistik kalibrasi per kota
- Biaya AI yang digunakan
- Heartbeat proses

### 10.2 Data Warmer
Setiap 2 jam, komponen terpisah (Warmer) mengambil data cuaca historis untuk tanggal-tanggal yang akan datang dan menyimpannya ke database. Ini memastikan saat bot perlu data historis untuk validasi, data sudah tersedia tanpa perlu menunggu.

### 10.3 WebSocket Price Watcher
Komponen terpisah yang menjaga koneksi langsung ke Polymarket untuk mendapat update harga real-time posisi yang sedang terbuka. Jika koneksi terputus, sistem otomatis mencoba menyambung kembali.

### 10.4 Telegram Notifikasi
Bot mengirim notifikasi ke Telegram untuk kejadian penting:
- Posisi baru dibuka
- Posisi ditutup (beserta hasil profit/rugi)
- Peringatan sistem (misalnya koneksi bermasalah)
- Laporan harian

### 10.5 Server
Bot berjalan di VPS (Virtual Private Server) di Jakarta dengan alamat `103.253.244.158`, aktif 24 jam sehari. Dikelola sebagai layanan sistem (systemd) yang otomatis restart jika terjadi crash.

---

## 11. Dasbor & Monitoring

Bot memiliki antarmuka web yang dapat diakses di browser (`http://103.253.244.158:8080/web_ui/`), menampilkan:

| Panel | Informasi |
|-------|-----------|
| Portfolio Value | Total nilai portofolio saat ini |
| Floating PnL | Keuntungan/kerugian posisi yang masih terbuka |
| Realized PnL | Keuntungan/kerugian yang sudah direalisasi |
| Win Rate | Persentase trade yang menang |
| Circuit Breaker | Status apakah bot masih aktif membuka posisi |
| Bot Health | Kapan siklus terakhir berjalan |
| Self-Learning | Status A/C/B per kota (Aggressive/Cautious/Blacklist) |
| Last Cycle Stats | Statistik siklus terakhir |
| Live Open Positions | Semua posisi yang sedang terbuka dengan harga real-time |

---

## 12. Status Saat Ini & Roadmap

### Status Per 18 April 2026

| Komponen | Status |
|----------|--------|
| Bot | ✅ Aktif berjalan di server |
| Mode | 📄 Paper Trading (simulasi, bukan uang nyata) |
| Modal | $5.00 USD (simulasi) |
| Threshold keyakinan | 60% (baru diturunkan dari 70%) |
| Data historis | ✅ Lengkap — 31 kota siap |
| Golden window | 05:00–11:00 WIB setiap hari |
| Trade pertama | Menunggu golden window besok pagi |

### Roadmap 7 Hari Paper Trade (Apr 18–25)

**Tujuan:** Mengumpulkan data trade nyata untuk kalibrasi dan evaluasi.

**Target:** Minimal 15–20 closed trades untuk analisis bermakna.

**Setelah 7 hari:**
1. Analisis win rate per kota
2. Evaluasi akurasi model prediksi
3. Keputusan threshold berdasarkan data
4. Implementasi auto-tuner yang aktif
5. Pertimbangkan switch ke live trading ($10–20 USD)

### Roadmap Jangka Panjang

1. **Tambah sumber prediksi ketiga** — forecast lebih akurat dengan 3 model independen
2. **Stake dinamis** — taruhan lebih besar saat edge tinggi, lebih kecil saat tipis
3. **Perluas kota** — tambah kota yang sering muncul di Polymarket
4. **Backtest mingguan otomatis** — validasi model terus-menerus dengan data historis

---

*Dokumen ini disiapkan sebagai referensi lengkap non-teknis untuk memahami cara kerja The Blueprints Trading Bot. Dibuat 18 April 2026 berdasarkan analisis mendalam seluruh komponen sistem.*
