Kamu adalah Senior Python Trading Bot Engineer yang sangat ahli di Polymarket MCP, WebSocket handling, price feed, order management, dan risk engine (khususnya stop-loss logic).

### BACKGROUND LENGKAP

Kita punya trading bot Polymarket yang menggunakan MCP resmi[](https://docs.polymarket.com/mcp).

Di market "Seoul":

- Bot kita awalnya sudah benar memprediksi dan ingin open posisi buy di harga ~0.17 (saat market masih tidak percaya).
- Saya ragu, lalu menyuruh Gemini melakukan beberapa perubahan sesuai file **@gemini_job.md**.
- Ternyata prediksi bot benar → harga langsung meroket ke 0.68.
- Setelah open posisi Seoul, muncul dua masalah serius:
  1. WebSocket (WS) stuck → tidak update harga real-time lagi.
  2. Haiku stop-loss ter-trigger padahal posisi Seoul sedang winning (seharusnya tidak terjadi).

Saya juga memberikan **history chat lengkap dengan Gemini** di file **@Debugging Trading Bot Gate.md** yang berisi semua percakapan debugging sebelumnya.

### TUGAS KAMU

Lakukan analisis ROOT CAUSE secara menyeluruh, rinci, dan tanpa melewatkan satu detail pun. Kamu HARUS menganalisis:

1. Mengapa WebSocket stuck tepat setelah open posisi Seoul?
2. Mengapa Haiku stop-loss salah trigger padahal posisi Seoul winning?
3. Apa pengaruh perubahan-perubahan yang dilakukan Gemini (lihat @gemini_job.md)?
4. Apakah ada kaitan antara perubahan Gemini dengan dua bug di atas?
5. Cek semua kemungkinan: race condition, WS subscription, heartbeat, position state management, price feed parsing, stop-loss logic (Haiku vs Seoul), dll.

Gunakan semua informasi yang ada:

- @gemini_job.md
- @Debugging Trading Bot Gate.md (history chat Gemini)

### OUTPUT YANG DIMINTA

Buatkan saya **satu file Markdown lengkap** dengan judul:

# 🔧 Full Root Cause Analysis & Fix Plan - Polymarket Bot (Seoul + Haiku Issue)

Struktur markdown harus persis seperti ini (ikut urutannya):

## 1. Executive Summary

## 2. Timeline Kejadian (berdasarkan semua file)

## 3. Root Cause Analysis (sangat detail, step-by-step)

## 4. Impact Perubahan Gemini (@gemini_job.md)

## 5. Bug List (nomor 1, 2, 3, … dengan penjelasan)

## 6. Recommended Fixes (step-by-step + code snippet jika perlu)

## 7. Prevention Plan (agar tidak terulang)

## 8. Command/SSH yang perlu saya jalankan di server

SSH server:
`ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158`

Jika ada informasi yang masih kurang jelas, tulis di bagian akhir:
**Butuh Informasi Tambahan:** ...

Mulai analisis sekarang.  
Think step by step secara mendalam dan teliti.
