# THE BLUEPRINTS — Panduan Lengkap Bot Trading Cuaca

**Versi:** 1.2 | **Tanggal:** 19 April 2026 | **Mode aktif:** Paper Trading

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
13. Riwayat Bug & Perbaikan

---

## 1. Apa Itu The Blueprints?

The Blueprints adalah bot trading otomatis yang beroperasi di **Polymarket** — sebuah platform prediksi pasar berbasis blockchain. Bot ini secara khusus menargetkan pasar cuaca, yaitu pertanyaan seperti:

> _"Apakah suhu tertinggi di London pada 19 April akan mencapai 16°C?"_

Di Polymarket, siapapun bisa membeli "tiket" untuk menjawab YA atau TIDAK terhadap pertanyaan semacam itu. Jika prediksi benar, tiket bernilai $1. Jika salah, bernilai $0.

Bot ini bekerja dengan cara membandingkan **prediksi cuaca dari layanan meteorologi** dengan **harga tiket di pasar**. Jika pasar menilai kemungkinan suatu kejadian hanya 25%, tapi bot menghitung kemungkinan sebenarnya adalah 65%, maka ada keuntungan potensial sebesar 40 sen per dolar — inilah yang disebut _edge_ atau keunggulan.

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

Setiap pertanyaan semacam ini terdiri dari ~11 sub-pasar (bucket), misalnya untuk London:

- "Apakah akan 13°C atau di bawahnya?" → Harga YES: $0.001
- "Apakah akan 14°C?" → Harga YES: $0.002
- "Apakah akan 15°C?" → Harga YES: $0.010
- "Apakah akan 16°C?" → Harga YES: $0.217 ← menarik
- "Apakah akan 17°C?" → Harga YES: $0.750
- "Apakah akan 18°C?" → Harga YES: $0.051 ← menarik
- dst.

Bot menganalisis setiap bucket secara terpisah dan hanya tertarik pada yang harganya antara **$0.05 sampai $0.65** — zona dimana masih ada ketidakpastian yang cukup untuk menghasilkan keuntungan.

### 4.2 Kota Target

Bot saat ini memantau **31 kota** di seluruh dunia, termasuk London, New York, Seoul, Singapore, Paris, Toronto, Dallas, Austin, Hong Kong, Madrid, dan lainnya.

### 4.3 Golden Window — Jendela Waktu Terbaik

Semua temperature market Polymarket **resolve setiap hari jam 19:00 WIB (12:00 UTC)**. Bot hanya mencari peluang pada **jam 05:00–11:00 WIB** setiap hari.

Alasannya:
- 14 jam sebelum resolve = 05:00 WIB → pasar baru buka, harga belum efficient
- 8 jam sebelum resolve = 11:00 WIB → forecast sudah cukup stabil
- Rentang 05:00–11:00 WIB = **sweet spot** antara ketidakpastian pasar dan kepastian forecast

Di luar jam tersebut, bot tetap berjalan tetapi tidak membuka posisi baru.

**Jumlah peluang yang ditemukan:** Dengan filter probabilitas dan edge aktif, biasanya ditemukan **sekitar 11 peluang** per siklus dalam golden window. Angka ini lebih rendah dari sebelumnya (~50-60) karena model probabilitas telah dikalibrasi ulang agar lebih konservatif — lebih sedikit tapi lebih akurat.

---

## 5. Cara Bot Menilai Apakah Layak Masuk

### 5.1 Menghitung Probabilitas

Setelah mendapat prediksi suhu dari Open-Meteo dan wttr.in, bot menggunakan rumus matematika untuk menghitung seberapa besar kemungkinan suatu bucket akan menang:

- **Pasar "di atas/di bawah"** — menggunakan kurva sigmoid. Semakin jauh prediksi dari batas threshold, semakin tinggi keyakinan bot
- **Pasar "persis X derajat"** — menggunakan kurva Gaussian (lonceng). Bot paling yakin jika prediksi tepat di angka tersebut, dan keyakinan menurun seiring jarak

**Kalibrasi k-factor sigmoid (diperbarui 19 April 2026):**

| Jarak ke resolve | k sebelumnya | k sekarang | Efek |
|---|---|---|---|
| ≤6 jam | 1.3 | 0.9 | Lebih hati-hati saat mendekati resolve |
| ≤14 jam (golden window) | 1.6 | 1.1 | Tidak lagi overconfident |
| ≤36 jam | 1.1 | 0.75 | Lebih konservatif |
| >36 jam | 0.75 | 0.50 | Jauh dari resolve = sangat rendah |

**Hard Ceiling Probabilitas (diperbarui 19 April 2026):**

Meskipun formula sigmoid bisa menghasilkan angka mendekati 100%, bot sekarang menerapkan batas keras berdasarkan margin forecast vs threshold:

| Selisih forecast vs threshold | Probabilitas maksimal |
|---|---|
| < 1°C | 75% |
| 1°C – 2°C | 87% |
| 2°C – 3°C | 94% |
| ≥ 3°C | Tidak dibatasi |

Alasan: model cuaca sendiri punya uncertainty ±2–3°C. Tidak realistis jika bot 99% yakin hanya karena selisih 1.5°C.

### 5.2 Dua Syarat Masuk

Bot hanya membuka posisi jika **dua syarat terpenuhi bersamaan**:

**Syarat 1 — Tingkat Keyakinan Minimum**
Bot harus yakin minimal **60%** bahwa prediksinya benar.

**Syarat 2 — Keuntungan Potensial Minimum**
Selisih antara keyakinan bot dan harga pasar harus minimal **20%** setelah dikurangi biaya transaksi. Artinya jika bot 65% yakin tapi harga pasar sudah $0.50, keuntungan potensial hanya 13% — tidak cukup, dilewati.

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

Setiap posisi menggunakan **$1.00 USD tepat** (cost + fee = $1.00 total). Tidak ada scaling dinamis berdasarkan confidence — semua posisi sama rata. Ini menjamin akuntansi yang bersih dan hasil yang mudah dibandingkan.

### 6.4 Bagaimana Bot Memilih dari Peluang yang Ada?

**Tahap 1 — Klasifikasi Bucket**

- `reject` — harga di luar rentang valid → **langsung dibuang**
- `watchlist` — harga terlalu murah (<$0.05) → **dibuang, hanya dipantau**
- `enter_swing` — harga OK, edge OK → **kandidat**
- `enter_hold_candidate` — edge DAN probabilitas sangat tinggi → **kandidat prioritas utama**

**Tahap 2 — Anti-Korelasi: Satu Kota, Satu Hari, Satu Posisi**
Dari kandidat yang tersisa, satu kota + satu tanggal hanya boleh menghasilkan 1 posisi. Jika London 19 April punya 3 bucket yang lolos, hanya yang terbaik yang diambil.

**Tahap 3 — Ranking**
Kandidat diurutkan berdasarkan: Confidence → Edge → Harga YES lebih murah.

**Tahap 4 — Slot Tersedia**
Bot hanya membuka posisi sebanyak slot yang tersedia (maks 5 posisi terbuka bersamaan).

**Tahap 5 — Review Claude Haiku**
Setiap kandidat final dikirim ke Claude Haiku untuk "second opinion". Haiku bisa memveto jika ada sesuatu yang mencurigakan.

**Contoh nyata:**

```
Bucket 17°C ($0.750) → reject (terlalu mahal)
Bucket 18°C ($0.051) → watchlist (terlalu murah)
Bucket 16°C ($0.217) → enter_swing ✓ → ranking → Haiku approve → BELI
```

---

## 7. Cara Bot Mengelola & Menutup Posisi

### 7.1 Pemantauan Harga Real-Time

Setelah posisi terbuka, bot memantau harga melalui WebSocket — koneksi langsung yang mendapat update harga dalam hitungan detik. Bot men-subscribe token ID posisi ke Polymarket WebSocket, dan setiap ada perubahan harga (event `price_change`, `last_trade_price`, atau `book`) bot langsung menerima notifikasi.

### 7.2 Tujuh Kondisi Penutupan Posisi (Hybrid Exit)

Bot mengevaluasi tujuh kondisi setiap siklus, berurutan dari prioritas tertinggi:

**1. Haiku Monitor — Forced Exit (Prioritas Tertinggi)**
Claude Haiku menganalisis setiap posisi terbuka setiap 1 jam. Jika Haiku memberi sinyal "close" dengan keyakinan ≥75% → posisi langsung ditutup paksa.

> ⚠️ **Newborn Guard (ditambahkan 19 April 2026):** Haiku TIDAK BISA menutup posisi yang berumur kurang dari 2 jam, kecuali kerugian sangat dalam (< -15%). Ini mencegah "false panic" seperti kejadian Seoul dimana Haiku menutup posisi +28% karena harga WS masih stale saat posisi baru dibuka.

> ⚠️ **Profit Guard (diperkuat 19 April 2026):** Haiku juga diblokir jika P&L posisi sedang > +5%. Posisi yang jelas sedang profit tidak boleh ditutup AI karena alasan apapun.

**2. Exit Sniper — Harga Tembus $0.90**
Jika harga YES tiba-tiba melonjak ke $0.90 atau lebih → langsung jual. Harga setinggi itu berarti pasar sudah hampir pasti — ambil untung sekarang.

**3. Sniper Stop Loss — Forecast Cuaca Rusak**
Jika prediksi cuaca tidak lagi valid (dua sumber tidak sepakat, atau prediksi bergeser drastis) → tutup posisi. Alasan: dasar keputusan masuk sudah tidak berlaku.

**4. Trailing Stop — Proteksi Keuntungan di Break-Even**
Aktif jika posisi pernah naik +20% atau lebih. Setelah itu, jika harga turun kembali ke harga entry → jual di break-even.

**5. Stop Loss Biasa (Hard Stop)**
Jika harga turun ke **55%** dari harga entry → potong rugi. Contoh: beli di $0.30 → stop loss di $0.165.

> [!IMPORTANT]
> **Cooldown 2 Jam**: Stop Loss ini tidak aktif selama 2 jam pertama sejak posisi dibuka. Ini memberi ruang bagi spread awal untuk settle.

**6. Take Profit +100%**
Target standar: 2x harga entry. Beli $0.30 → target $0.60.

**7. Late Window Logic (≤2 Jam Sebelum Resolve)**
Saat tinggal ≤2 jam sebelum pasar tutup jam 19:00 WIB:
- Haiku yakin ≥75% DAN forecast valid → **tahan sampai resolve**
- Yakin <75% atau forecast tidak valid → **jual sekarang**
- Yakin <45% → **"Thesis Decay Exit"** — jual meskipun belum rugi

### 7.3 Whiplash Shield — Cooldown Setelah Exit

Setelah posisi ditutup oleh Haiku atau stop-loss, kota tersebut masuk **blacklist 24 jam**. Bot tidak akan membuka posisi baru di kota yang sama untuk hari itu.

Alasan: setelah exit paksa, kondisi pasar di kota tersebut dianggap tidak kondusif. Membuka posisi lagi langsung ("churning") hanya membuang fee tanpa edge.

### 7.4 Penutupan Berbasis AI (Haiku Monitor)

Claude Haiku memantau posisi terbuka setiap 1 jam dan menerima konteks lengkap:

| Field | Keterangan |
|---|---|
| `market_question` | Pertanyaan pasar lengkap |
| `entry_price` | Harga saat posisi dibuka |
| `current_yes_price` | Harga pasar sekarang |
| `pnl_pct` | Untung/rugi saat ini dalam persen |
| `hours_until_resolve` | Jam tersisa sebelum resolve |
| `entry_model_prob` | Keyakinan bot saat masuk |
| `entry_edge` | Edge yang dihitung saat masuk |
| `forecast_temp_at_entry_c` | Prediksi cuaca waktu posisi dibuka |
| `forecast_temp_now_c` | Prediksi cuaca **sekarang** (terbaru) |
| `forecast_drift_c` | Selisih — berapa derajat forecast bergeser |

**Cara Haiku memutuskan:**
1. Forecast drift melawan thesis → sinyal kuat close
2. P&L < -30% DAN forecast drift melawan → close
3. P&L < -30% tapi forecast tidak berubah → market panic sementara, jangan buru-buru close
4. ≤2 jam sebelum resolve DAN masih rugi dalam → close
5. ≤2 jam sebelum resolve DAN posisi untung → tahan
6. Tidak yakin → default hold

### 7.5 Review AI Sebelum Masuk Posisi (Haiku Entry)

Sebelum membuka posisi, Haiku melakukan sanity check terakhir:
1. Apakah forecast masuk akal untuk kota dan musim tersebut?
2. Apakah hours_until_resolve cukup? (<6 jam → sangat skeptis)
3. Apakah arah + threshold konsisten dengan forecast?
4. Jika edge < 0.15 atau model_prob < 0.55 → skip

Haiku entry dibatasi **2 panggilan per hari**.

---

## 8. Sistem Pembelajaran Mandiri

### 8.1 Kalibrasi Probabilitas (Bayesian Shrinkage)

Setiap trade yang selesai dicatat. Setelah minimal **5 data per kategori** terkumpul (per kota, per arah, per jarak waktu), bot mulai "mengoreksi dirinya sendiri" — menyesuaikan perhitungan probabilitas ke depan berdasarkan akurasi historis.

Catatan: fitur ini baru aktif efektif setelah ~30 trade terkumpul.

### 8.2 Auto-Tuner Per Kota (A/C/B)

Berdasarkan rekam jejak per kota (minimal 3 trade), bot mengategorikan:

- **A (Aggressive)** — win rate tinggi, bot lebih agresif
- **C (Cautious)** — win rate rendah, bot lebih selektif
- **B (Blacklist)** — kerugian berturut-turut, dilewati sementara

---

## 9. Perlindungan & Manajemen Risiko

### 9.1 Circuit Breaker

Jika total kerugian hari ini melampaui batas → bot berhenti membuka posisi baru untuk hari itu.

### 9.2 Daily Target Gate

Jika target harian sudah tercapai → bot bisa berhenti membuka posisi baru untuk mengamankan keuntungan.

### 9.3 Anti-Korelasi

Tidak boleh punya dua posisi yang saling bertentangan dalam event yang sama (misal London 16°C DAN London 17°C).

### 9.4 Validasi Prediksi Ganda

- **Konsensus dua sumber** — Open-Meteo dan wttr.in harus sepakat dalam ±2.5°C
- **Anomaly check** — prediksi tidak boleh >7°C dari rata-rata historis 10 tahun

### 9.5 Consensus Guard

Jika bot >90% yakin tapi harga pasar <30% (pasar sangat tidak setuju) dalam 12 jam terakhir sebelum resolve → probabilitas bot diredam. Ini mencegah bot ngotot melawan konsensus pasar tanpa alasan yang sangat kuat.

### 9.6 Perlindungan Data

Semua data posisi disimpan di SQLite (`blueprints_master.db`) dengan mirror JSON (`paper_positions_5usd.json`). Jika bot restart, posisi yang sedang terbuka tetap terlacak.

---

## 10. Infrastruktur & Komponen Teknis

### 10.1 Database Terpusat

Semua data tersimpan dalam SQLite (`blueprints_master.db`):
- Posisi aktif dan historis
- Rekam jejak trade (untuk pembelajaran)
- Data cuaca historis 31 kota
- Statistik kalibrasi per kota
- Biaya AI yang digunakan
- Heartbeat proses

### 10.2 Data Warmer

Setiap 2 jam, komponen terpisah mengambil data cuaca historis untuk tanggal mendatang dan menyimpan ke database. Memastikan data historis selalu tersedia saat dibutuhkan.

### 10.3 WebSocket Price Watcher (Diperbarui 19 April 2026)

Komponen terpisah yang menjaga koneksi langsung ke Polymarket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`).

**Perbaikan yang diterapkan:**
- **IPv4 Force** — VPS tidak punya routing IPv6 ke internet, menyebabkan koneksi hang. Kini dipaksa IPv4.
- **Subscribe by token ID** — Bot subscribe ke `assets_ids` dengan token ID posisi aktif. Polymarket mengirim `book` (snapshot orderbook) + `price_change`/`last_trade_price` secara real-time.
- **Universal book parser** — Mendukung format orderbook lama (list) dan baru (dict).
- **Watchdog thread** — Thread terpisah memantau koneksi, reconnect jika tidak ada pesan >120 detik.
- **Heartbeat logging** — 5% dari event yang diterima di-log untuk membuktikan koneksi aktif.

Saat tidak ada posisi terbuka, WS tetap terkoneksi tapi tidak subscribe ke token apapun — hanya menerima broadcast global `new_market`. Saat posisi dibuka, token ID langsung di-subscribe dan harga mulai mengalir.

### 10.4 AI — Claude Haiku

| Fungsi | Kapan Dipanggil | Interval | Batas Per Hari |
|---|---|---|---|
| **Entry Review** | Sanity check sebelum buka posisi | Per kandidat | 2 panggilan |
| **Position Monitor** | Pantau posisi terbuka — hold atau close? | Setiap 1 jam per posisi | 80 panggilan |
| **Market Sensing** | Identifikasi kode ICAO dari deskripsi pasar | Per pasar baru | 50 panggilan |

Biaya estimasi: **~$0.03–0.04 per hari**.

### 10.5 Telegram Notifikasi

Bot mengirim notifikasi untuk:
- Posisi baru dibuka
- Posisi ditutup (beserta hasil profit/rugi)
- Peringatan sistem
- **Laporan atribusi profit mingguan** (hanya setiap 7 hari — bukan setiap siklus)

### 10.6 Server

VPS di Jakarta, IP `103.253.244.158`, aktif 24 jam. Dikelola sebagai systemd service yang otomatis restart jika crash.

---

## 11. Dasbor & Monitoring

Antarmuka web di `http://103.253.244.158:8080/web_ui/`:

| Panel | Informasi |
|---|---|
| Portfolio Value | Total nilai portofolio saat ini |
| Floating PnL | Keuntungan/kerugian posisi terbuka |
| Realized PnL | Keuntungan/kerugian yang sudah direalisasi |
| Win Rate | Persentase trade yang menang |
| Circuit Breaker | Status apakah bot masih aktif |
| Bot Health | Kapan siklus terakhir berjalan |
| Self-Learning | Status A/C/B per kota |
| Last Cycle Stats | Statistik siklus terakhir |
| Live Open Positions | Semua posisi dengan harga real-time |

---

## 12. Status Saat Ini & Roadmap

### Status Per 19 April 2026

| Komponen | Status |
|---|---|
| Bot | ✅ Aktif berjalan di server |
| Mode | 📄 Paper Trading (simulasi) |
| Modal awal | $5.00 USD (fresh reset 19 April 2026) |
| Stake per posisi | $1.00 USD tepat (cost + fee) |
| Maks posisi terbuka | 5 posisi bersamaan |
| Threshold keyakinan | 60% |
| Threshold edge minimum | 20% |
| Stop Loss Multiplier | 0.55 (harga turun 45% dari entry → cut loss) |
| Cooldown Stop Loss | 120 menit (2 jam) |
| Golden window | 05:00–11:00 WIB setiap hari |
| Resolve time | 19:00 WIB setiap hari |
| Data historis | ✅ Lengkap — 31 kota |
| Claude Haiku | ✅ Aktif — entry, monitor, sensing |
| WebSocket | ✅ Aktif dengan IPv4 force |
| Newborn Guard | ✅ Aktif — 2 jam proteksi setelah entry |
| Profit Guard | ✅ Aktif — blokir exit jika P&L > +5% |
| Whiplash Shield | ✅ Aktif — cooldown 24 jam per kota setelah exit paksa |
| Consensus Guard | ✅ Aktif — redam prob jika bot vs pasar divergen |
| Prob Ceiling | ✅ Aktif — max 75%/87%/94% per margin |
| AI budget | ~$0.03–0.04/hari, estimasi cukup 3+ minggu |

### Roadmap Bertahap

**Phase 1 — Stabilitas (3 hari pertama: 19–21 April)**
- Target: Bot berjalan tanpa crash, WS tidak freeze, tidak ada SyntaxError/ImportError
- Indikator sukses: Trade masuk normal jam 05:00–11:00 WIB setiap hari
- Jika kalah semua: investigasi dulu sebelum top-up $5

**Phase 2 — Evaluasi (7 hari: 19–25 April)**
- Target: Kumpulkan 20–30 closed trades
- Indikator sukses: Win rate ≥ 50%, tidak ada rugi > $2 total
- Sistem Bayesian mulai punya data untuk kalibrasi

**Phase 3 — Live Trading (setelah 15 hari: mulai ~4 Mei)**
- Masuk uang asli $5
- Prasyarat: Win rate ≥ 55%, avg profit > avg loss, tidak ada bug dalam 7 hari terakhir

### Roadmap Jangka Panjang

1. **Tambah sumber prediksi ketiga** — forecast lebih akurat dengan 3 model independen
2. **Stake dinamis (Kelly Criterion)** — taruhan lebih besar saat edge tinggi
3. **Perluas kota** — tambah kota yang sering muncul di Polymarket
4. **REST API fallback sebelum Haiku exit** — verifikasi harga real via REST jika WS stale sebelum memperbolehkan Haiku menutup posisi (Bug #8 dari RCA Seoul, belum diimplementasikan)

---

## 13. Riwayat Bug & Perbaikan

### Insiden Seoul — 19 April 2026

**Kronologi:** Bot masuk posisi Seoul (YES) di $0.53. Harga naik ke $0.68 (+28% profit). WebSocket freeze → harga di DB stuck di $0.52 → Haiku hitung P&L = -1.9% → Haiku panic close → posisi ditutup rugi padahal sedang profit.

**Root cause:**
1. **WS IPv6 Deadlock** — VPS tidak punya routing IPv6, library hang menunggu timeout
2. **Haiku Hypersensitivity** — Haiku close posisi baru berdasarkan harga stale tanpa menunggu WS sync
3. **Sigmoid Overconfidence** — k=1.6 terlalu agresif, model 99% yakin untuk gap 2°C

**Perbaikan yang diterapkan:**

| Bug | Fix | File |
|---|---|---|
| WS IPv6 deadlock | Force IPv4 via socket.getaddrinfo | `ws_price_watcher.py` |
| WS tidak dapat initial snapshot | `initial_dump: True` + subscribe `book` event | `ws_price_watcher.py` |
| WS format parser gagal | Universal parser (list + dict) | `ws_price_watcher.py` |
| WS freeze silent | Watchdog thread + heartbeat log 5% | `ws_price_watcher.py` |
| Haiku false panic exit | Newborn Guard (2 jam) + Profit Guard (>5%) | `analysis.py` |
| Sigmoid overconfident | k-factors diturunkan + hard ceiling cap | `pricing.py` |
| Telegram spam tiap siklus | Attribution report hanya setiap 7 hari | `cycles.py` |
| Stake tidak exact $1 | Hapus risk-multiplier + bypass depth scaling | `cycles.py` + server `.env` |

---

_Dokumen ini adalah referensi lengkap untuk memahami cara kerja The Blueprints Trading Bot. Diperbarui 19 April 2026 mencakup semua perbaikan pasca-insiden Seoul._
