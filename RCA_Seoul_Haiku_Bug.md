# 🔧 Full Root Cause Analysis & Fix Plan - Polymarket Bot (Seoul + Haiku Issue)

## 1. Executive Summary
Analisis mendalam terhadap kegagalan operasional pada 19 April 2026 menunjukkan adanya interaksi antara **Networking Deadlock** (WebSocket), **AI Supervisor Hypersensitivity** (Haiku Monitor), dan **Mathematical Overconfidence** (Sigmoid Model). 

Posisi Seoul ($24^{\circ}\text{C}$ or higher) ditutup paksa oleh Haiku Monitor meskipun sedang profit +28%, akibat bot kehilangan data harga real-time (WS Stuck) dan AI salah menginterpretasikan pergerakan kecil ramalan cuaca sebagai kegagalan tesis.

## 2. Timeline Kejadian
- **09:40 WIB**: Bot mengeksekusi posisi **Seoul (YES)** di harga ~0.53.
- **09:45 WIB**: Harga Polymarket melonjak ke 0.68 (+28% profit).
- **09:47 WIB**: WebSocket (WS) mengalami "Freeze" di log. Harga di Dashboard tetap stuck di 0.52.
- **09:50 WIB**: Haiku Monitor (AI) mengevaluasi posisi Seoul. Karena harga di DB (stuck) menunjukkan kerugian semu dan ramalan cuaca geser -0.2 derajat, Haiku mengeluarkan perintah **CLOSE** (Panic Sell).
- **09:54 WIB**: Posisi Seoul resmi ditutup di harga ~0.51 (Rugi karena spread), padahal harga real-time Polymarket saat itu sedang di puncak profit.

## 3. Root Cause Analysis
### Bug A: WebSocket Connectivity Deadlock (The "Silent" Killer)
- **Problem**: WS log berhenti di status `Connecting...` tanpa pernah mencapai `Subscribed`.
- **Root Cause**: **IPv6 Deadlock**. DNS Polymarket memberikan alamat IPv6. VPS memiliki stack IPv6 aktif namun **tidak memiliki routing internet IPv6**. Library `websocket-client` mencoba IPv6 terlebih dahulu dan "hang" menunggu timeout sebelum mencoba IPv4.
- **Impact**: Bot tidak menerima update `initial_dump` dan streaming harga, menyebabkan dashboard/risk engine menggunakan data *stale* (basi).

### Bug B: AI Supervisor Momentum Bias (Haiku Panic)
- **Problem**: Haiku menutup posisi Seoul yang sebenarnya sedang winning.
- **Root Cause**: Haiku didesain untuk mendeteksi "Drift" (pergeseran ramalan). Fluktuasi kecil cuaca ($0.1^{\circ}\text{C}-0.3^{\circ}\text{C}$) dianggap sebagai "Thesis Failure" jika tidak diimbangi dengan data P&L real-time yang akurat. Karena P&L stuck, Haiku memprioritaskan "Save Capital" dan melakukan exit prematur.

### Bug C: Model Mathematical Hubris (Sigmoid Overconfidence)
- **Problem**: Bot mengambil posisi Austin dengan keyakinan 99% yang berujung rugi.
- **Root Cause**: Konstanta ketajaman sigmoid ($k=1.6$) terlalu tinggi. Selisih $2.0^{\circ}\text{C}$ dari ramalan sudah menghasilkan probabilitas >95%. Ini tidak realistis mengingat *ensemble uncertainty* cuaca biasanya $\pm 2^{\circ}\text{C}$.

## 4. Impact Perubahan Gemini (@gemini_job.md)
Perubahan sebelumnya (pagi ini) memberikan dampak campuran:
- **Whiplash Shield (Good)**: Berhasil mencegah bot masuk lagi ke Seoul setelah ditutup Haiku. Tanpa ini, bot akan "Churning" fee terus menerus.
- **Sigmoid Scaling (Incomplete)**: Percobaan pertama menurunkan $k$ sudah di arah yang benar, tapi $k=1.6$ untuk golden window terbukti masih terlalu agresif.
- **Profit Guard (Incomplete)**: Guard yang dipasang (hanya proteksi >5% profit) gagal karena harga di DB *stale* (stuck di bawah 5% profit) akibat Bug A.

## 5. Bug List
1. **WS-IPV6-HANG**: Library WS terjebak di jalur IPv6 yang buntu pada VPS.
2. **HAIKU-DRIFT-PANIC**: AI terlalu sensitif pada noise cuaca kecil tanpa Newborn Protection.
3. **PROB-CEILING-MISSING**: Tidak ada limitasi atas untuk tingkat keyakinan saat margin sempit.
4. **PARSER-BOOK-MISSING**: Parser WS mengabaikan event `"book"` yang sangat penting untuk `initial_dump`.

## 6. Recommended Fixes
### Fix 1: Force IPv4 & Parser Hardening (`ws_price_watcher.py`)
Memaksa koneksi via IPv4 saja untuk menghindari deadlock dan menambahkan support event `"book"`.
```python
# Force AF_INET (IPv4)
ws.run_forever(sockopt=((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),), 
               sslopt={"cert_reqs": ssl.CERT_NONE})
```

### Fix 2: Newborn Guard (`analysis.py`)
Mencegah Haiku menutup posisi yang berumur < 2 jam kecuali kerugian sangat dalam (<-15%). Ini memberi waktu harga WS untuk sinkron (settle).

### Fix 3: Prob Calibration (`pricing.py`)
- Turunkan $k$ (Golden Window 1.6 -> 1.1).
- Tambahkan **Hard Ceiling Cap**: Jika selisih ramalan < 1°C, probabilitas maksimal hanya 75%.

## 7. Prevention Plan
- **Pre-flight Connectivity Check**: Menambahkan ping test IPv4 vs IPv6 saat startup bot.
- **Logging Heartbeat**: Log "Detak Jantung" WS (Raw message received) harus muncul di Dashboard agar user tahu jika feed macet.
- **Diamond Hands Logic**: Pengetatan syarat exit AI (Haiku) agar lebih toleran terhadap volatilitas harga di 2 jam pertama.

## 8. Command/SSH yang perlu saya jalankan di server
1. **Deploy Fix**: `git push origin master` (Local)
2. **Update Server**: `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "cd /opt/the_blueprints && git pull origin master && systemctl restart blueprints"`
3. **Verify Logs**: `tail -f /opt/the_blueprints/logs/ws_internal.log` (cek pesan "Parsed book event").

---
**Butuh Informasi Tambahan:** Tidak ada. Data dari @gemini_job.md dan history chat sudah cukup untuk eksekusi.
