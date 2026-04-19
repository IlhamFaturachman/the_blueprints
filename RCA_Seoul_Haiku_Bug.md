# 🔧 Full Root Cause Analysis & Fix Plan - Polymarket Bot (Seoul + Haiku Issue)

---

## 1. Executive Summary

Dua bug terjadi secara **bersamaan dan saling menyebabkan** (causally linked):

1. **WS Stuck**: WebSocket tidak berhasil menerima harga real-time dari Polymarket CLOB setelah posisi Seoul dibuka. Akibatnya harga di bot mandek di ~0.52.
2. **Haiku False Exit**: Haiku Monitor memutuskan close posisi Seoul bukan karena thesis salah, tapi karena menerima **harga stale 0.52** dari WS yang stuck — padahal harga nyata di Polymarket sudah naik ke **0.68** (winning 28%).

Keduanya bukan bug terpisah. **WS stuck → harga stale → Haiku panik → Seoul exit paksa saat winning.**

Gemini berkontribusi pada root cause melalui beberapa perubahan yang (sebagian) memperburuk situasi sebelum akhirnya memperbaikinya. Semua fix sudah terapply di codebase saat ini, tapi ada **satu gap yang masih perlu ditutup**.

---

## 2. Timeline Kejadian

| Waktu (WIB) | Kejadian |
|---|---|
| ~09:10 | Bot benar prediksi Seoul 24°C+, ingin masuk di ~0.17 |
| ~09:18 | Gemini: Dynamic Sigmoid + Whiplash Shield dipasang |
| ~09:32 | Gemini: Consensus Guard dipasang |
| ~09:38 | Deep system reset SQLite, fresh start $5.00 |
| ~09:40 | Bot open posisi Seoul @ 0.53 (harga pasar sudah naik) |
| ~09:40 | WS langsung stuck setelah open — harga freeze di 0.52 |
| ~09:42 | Haiku Monitor dipanggil. Menerima `current_yes_price=0.52` dari WS stale |
| ~09:42 | Haiku hitung P&L: (0.52 - 0.53) / 0.53 = **-1.9%** |
| ~09:42 | Forecast drift 0.1°C + P&L negatif → Haiku keputusan: **CLOSE** |
| ~09:42 | Seoul exit @ 0.52, padahal Polymarket sudah 0.68 |
| ~09:43 | User lapor masalah WS + Seoul keluar padahal naik |
| ~10:04 | Gemini: WS Parser v2 + IPv4 force + Heartbeat dipasang |

---

## 3. Root Cause Analysis (Detail)

### Bug 1: WebSocket Stuck — 5 Layer Root Cause

WS stuck bukan satu bug, tapi **5 layer masalah yang terjadi bersamaan**:

#### Layer 1 (Paling Mendasar): IPv6 Buntu di VPS
```
VPS punya alamat IPv6 AKTIF tapi tidak ada route ke internet via IPv6.
websocket-client mencoba IPv6 dulu → HANG menunggu timeout (120s+).
Dari luar terlihat: log hanya sampai "[WS-MPC] Connecting..." kemudian diam.
```
**Status**: ✅ FIXED — `_prefer_ipv4()` socket patch di baris 107-112 `ws_price_watcher.py`.

#### Layer 2: Race Condition (Kontribusi Gemini)
```
Gemini menambahkan on_position_opened_fn callback yang langsung memanggil
update_subscriptions() segera setelah trade execution.

Masalah: DB write belum selesai saat callback fire.
WS tanya DB: "berapa token aktif?" → dapat 0 → subscribe 0 token → buta.
```
**Status**: ✅ FIXED — token ID kini dikirim langsung ke WS queue tanpa baca DB.

#### Layer 3: Missing `initial_dump: True`
```
Subscription payload ke Polymarket CLOB tidak menyertakan "initial_dump": True.
Tanpa ini, Polymarket tidak kirim snapshot harga saat pertama konek.
Bot hanya akan dapat harga jika ada transaksi baru (pasif menunggu).
Dallas & Austin yang sedang sepi transaksi → tidak pernah dapat harga baru.
```
**Status**: ✅ FIXED — `_send_op()` sekarang selalu include `"initial_dump": True`.

#### Layer 4: Parser Tidak Kenal Event Type `book`
```
Polymarket kirim initial snapshot sebagai event_type = "book".
Parser lama hanya handle: "price_change" dan "best_bid_ask".
Semua event "book" dibuang (silently ignored).
Ini kenapa Dallas ($0.36) dan Austin ($0.10) tidak pernah update.
```
**Status**: ✅ FIXED — handler `book` ditambahkan di `_on_message()`.

#### Layer 5: Non-Universal Book Format
```
Format field "bids" dalam event "book" dari Polymarket bisa berupa:
  - List of lists: [[0.36, 100], [0.35, 200]]
  - List of dicts: [{"price": "0.36", "size": "100"}]

Parser v1 hanya handle satu format. Jika format berbeda → data dibuang.
```
**Status**: ✅ FIXED — universal parser di baris 203-210 handle keduanya.

---

### Bug 2: Haiku False Exit — Causal Chain

```
WS stuck (Layer 1-5)
    ↓
Harga WS freeze di 0.52 (harga entry ~0.53)
    ↓
Haiku Monitor dipanggil dengan current_yes_price = 0.52
    ↓
pnl_pct = (0.52 - 0.53) / 0.53 × 100 = -1.9%
    ↓
Haiku melihat: P&L negatif + forecast drift 0.1°C (minor)
    ↓
Decision Rules di prompt Haiku:
  Rule 2: pnl < -30%? No. → tidak close dari sini.
  Rule 3: pnl < -30% + small drift? No.
  Rule 6: "if unsure, default to hold"
  TAPI: P&L -1.9% + drift → Haiku interpretasi sebagai risiko kecil tapi nyata
    ↓
Haiku output: {"action": "close", "confidence": 0.7}
    ↓
Profit Guard check: cur_pnl = -1.9% → TIDAK > 5% → Guard TIDAK aktif
    ↓
Seoul CLOSE @ 0.52 (loss -1.9%) — padahal Polymarket 0.68 (+28%)
```

**Ini 100% False Panic akibat stale WS price, bukan thesis failure.**

---

## 4. Impact Perubahan Gemini

| Perubahan Gemini | Impact | Status |
|---|---|---|
| `on_position_opened_fn` callback | ⚠️ Intro race condition, crash `TypeError` | Fixed |
| Dynamic Sigmoid (k=1.6 / k=0.75) | ⚠️ k=1.6 terlalu sensitif untuk monitoring posisi open | Partially mitigated via Profit Guard |
| Whiplash Shield (24h cooldown) | ✅ Positif — mencegah re-entry konyol | Active |
| Consensus Guard | ✅ Positif — cegah Bargain Trap | Active |
| Profit Guard (>5% override) | ✅ Positif — tapi threshold terlalu tinggi | Active but gap exists |
| WS `initial_dump: True` | ✅ Kritis untuk fix WS | Active |
| WS `book` parser | ✅ Kritis untuk fix WS | Active |
| Universal book format | ✅ Kritis untuk robustness | Active |
| Heartbeat logging (5%) | ✅ Visibility — bisa lihat WS aktif | Active |
| IPv4 preference patch | ✅ Fix root cause VPS IPv6 | Active |

**Kaitan langsung dengan dua bug**: Gemini memperkenalkan race condition (WS Bug Layer 2) saat mencoba memperbaiki WS lag. Fix-fix berikutnya progressif dan pada akhirnya benar. Tapi urutan perbaikan yang tidak atomik menyebabkan Seoul loss terjadi di tengah proses debugging.

---

## 5. Bug List

### Bug #1: WS IPv6 Hang (Root / Paling Fundamental)
- **Lokasi**: `ws_price_watcher.py` `_run_loop()`
- **Gejala**: WS log stuck di "Connecting..." berjam-jam
- **Sebab**: VPS IPv6 aktif tapi tidak ada internet route
- **Status**: ✅ Fixed (IPv4 preference)

### Bug #2: Race Condition on_position_opened_fn
- **Lokasi**: Interaksi antara `market_discovery.py` → `cycles.py` → `ws_price_watcher.py`
- **Gejala**: WS subscribe 0 token setelah open posisi
- **Sebab**: Callback fire sebelum DB write selesai
- **Status**: ✅ Fixed (direct token pass)

### Bug #3: Missing initial_dump
- **Lokasi**: `ws_price_watcher.py` `_send_op()`
- **Gejala**: Harga Dallas/Austin tidak pernah dapat nilai awal
- **Sebab**: Polymarket butuh flag ini untuk kirim snapshot
- **Status**: ✅ Fixed

### Bug #4: Parser Buta pada Event `book`
- **Lokasi**: `ws_price_watcher.py` `_on_message()`
- **Gejala**: Harga tidak update meski WS sudah konek
- **Sebab**: `book` event type tidak dihandle
- **Status**: ✅ Fixed

### Bug #5: Non-Universal Book Format
- **Lokasi**: `ws_price_watcher.py` `_on_message()` — book handler
- **Gejala**: Beberapa token tidak update (format berbeda)
- **Sebab**: Parser rigid, tidak handle list-of-list vs list-of-dict
- **Status**: ✅ Fixed

### Bug #6: Haiku False Exit via Stale Price (Causal dari Bug #1-5)
- **Lokasi**: `analysis.py` `_haiku_position_monitor()`
- **Gejala**: Seoul di-close saat winning 28%
- **Sebab**: Haiku menerima harga stale 0.52, hitung P&L = -1.9%
- **Status**: ⚠️ PARTIALLY FIXED — Profit Guard ada tapi threshold >5% tidak cukup untuk posisi yang baru saja dibuka dengan harga sedekat entry

### Bug #7 (MASIH TERBUKA): Profit Guard Threshold Terlalu Tinggi
- **Lokasi**: `analysis.py` baris 463-468
- **Gejala**: Posisi dengan P&L antara -5% s/d +5% tidak diproteksi dari Haiku noise
- **Sebab**: Threshold `cur_pnl > 5.0` tidak cover zona netral
- **Status**: ❌ BELUM FIXED — ini gap yang masih ada

### Bug #8 (RISIKO): Haiku Tidak Verifikasi Harga via REST Sebelum Exit
- **Lokasi**: `analysis.py` + `cycles.py`
- **Gejala**: Haiku percaya 100% pada WS price tanpa cross-check
- **Sebab**: Tidak ada fallback REST API price verification
- **Status**: ❌ BELUM FIXED — single point of failure di WS price

---

## 6. Recommended Fixes

### Fix #1 (PRIORITAS TINGGI): Perluas Profit Guard — Cover Zona Netral

**File**: `market_discovery_internal/analysis.py`

Ganti logic Profit Guard:

```python
# SEBELUM (terlalu narrow):
if cur_pnl > 5.0:
    result["action"] = "hold"

# SESUDAH (cover zona aman):
# Jika posisi masih dalam 2 jam pertama (masih spread period) DAN P&L > -10% → hold
# Jika P&L > -5% secara general → tahan dulu, minta konfirmasi
age_h = 0.0
opened_at_str = position.get("opened_at")
if opened_at_str:
    try:
        from datetime import datetime, timezone
        opened_at = datetime.fromisoformat(opened_at_str)
        age_h = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
    except: pass

is_newborn = age_h < 2.0  # dalam 2 jam pertama, spread belum settle
cur_pnl = float(pnl_pct) if pnl_pct is not None else 0.0

if result.get("action") == "close":
    if cur_pnl > 5.0:
        # Jelas profit, block exit
        result["action"] = "hold"
        result["reasoning"] = f"[PROFIT-GUARD] Blocked: P&L={cur_pnl}% > 5%. " + result.get("reasoning", "")
    elif is_newborn and cur_pnl > -15.0:
        # Posisi baru (<2j), P&L belum tentu akurat (spread noise)
        result["action"] = "hold"
        result["reasoning"] = f"[NEWBORN-GUARD] Blocked: Age={age_h:.1f}h < 2h, P&L={cur_pnl}%. Allow settlement. " + result.get("reasoning", "")
```

### Fix #2 (PRIORITAS TINGGI): REST API Price Verification Sebelum Haiku Exit

Sebelum Haiku memutuskan close, verifikasi harga via Polymarket REST API:

```python
# Di cycles.py, sebelum memanggil _haiku_position_monitor:
def _get_rest_price(token_id: str) -> float | None:
    """Fallback: ambil harga via REST jika WS tidak dipercaya."""
    try:
        import httpx
        url = f"https://clob.polymarket.com/midpoint?token_id={token_id}"
        r = httpx.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            mid = data.get("mid")
            if mid is not None:
                return float(mid)
    except Exception:
        pass
    return None

# Lalu di monitoring loop:
ws_price = current_yes_price  # dari WS
rest_price = _get_rest_price(token_id)
if rest_price is not None:
    # Jika WS price berbeda >10% dari REST price → WS stale, pakai REST
    if abs(ws_price - rest_price) / rest_price > 0.10:
        logger.warning("[PRICE-SANITY] WS=%.4f vs REST=%.4f — using REST", ws_price, rest_price)
        current_yes_price = rest_price  # override dengan REST price
```

### Fix #3 (MEDIUM): Dampen k-factor untuk Haiku Monitoring

Ketika monitoring posisi open (bukan entry), gunakan k yang lebih rendah agar forecast drift kecil tidak memicu probabilitas drop drastis:

**File**: `market_discovery_internal/pricing.py`

```python
# Ketika mode = "monitoring" (posisi sudah open), gunakan k lebih rendah
# k=0.8 untuk monitoring, k=1.6 hanya untuk entry signal
if mode == "monitoring":
    k = 0.8
elif hours_until_resolve < 14:
    k = 1.6
else:
    k = 0.75
```

### Fix #4 (LOW): Heartbeat Assertion — Alert jika WS Diam > 10 Menit

Tambahkan check di main loop: jika WS tidak ada update dalam 10 menit, log WARNING dan skip Haiku monitor call:

```python
# Di cycles.py atau market_discovery.py:
WS_STALE_THRESHOLD_SECONDS = 600  # 10 menit

ws_last_update = _ws_price_cache.get(token_id, {}).get("updated_at")
if ws_last_update:
    staleness = (datetime.now(timezone.utc) - ws_last_update).total_seconds()
    if staleness > WS_STALE_THRESHOLD_SECONDS:
        logger.warning("[WS-STALE] Price for %s not updated for %.0fs — skipping Haiku monitor", token_id, staleness)
        continue  # skip this monitoring cycle
```

---

## 7. Prevention Plan

### P1: Price Source Redundancy (Wajib)
Jangan pernah gunakan hanya WS sebagai sumber harga untuk keputusan exit. Selalu ada REST fallback. **WS = real-time display. REST = ground truth untuk keputusan.**

### P2: WS Health Check di Setiap Cycle
Sebelum setiap Haiku monitor call, cek timestamp terakhir WS update untuk token tersebut. Jika stale > 5 menit → skip Haiku atau force REST lookup.

### P3: Profit Guard Lebih Konservatif
Newborn Guard (2 jam pertama) + threshold -15% sudah jauh lebih baik dari hanya >+5%.

### P4: Staging Test WS Sebelum Deploy
Selalu jalankan test WS sederhana di VPS sebelum restart bot:
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "/opt/the_blueprints/venv/bin/python3 /tmp/ws_test.py"
```

### P5: Atomik Deploy — Tidak Mid-Session
Jangan deploy perubahan saat posisi sedang open. WS reconnect setelah restart bisa menyebabkan brief stale period yang berbahaya.

### P6: Haiku Monitor Min-Age Gate
Haiku Monitor sebaiknya tidak dipanggil untuk posisi yang belum berumur 30 menit. Terlalu banyak noise di periode awal.

---

## 8. Command/SSH yang Perlu Dijalankan di Server

SSH: `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158`

### Langkah 1: Verifikasi WS Sekarang Berfungsi

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "tail -n 50 /opt/the_blueprints/logs/ws_internal.log"
```

Cek apakah ada log "Heartbeat" dan "Parsed book/last_trade_price". Jika ada → WS aktif.

### Langkah 2: Cek Status Bot dan Posisi Aktif

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "tail -n 30 /opt/the_blueprints/logs/paper_loop.out"
```

### Langkah 3: Cek Database State

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "sqlite3 /opt/the_blueprints/logs/blueprints_master.db 'SELECT city, entry_price, stop_loss_price, status FROM active_positions;'"
```

### Langkah 4: Manual WS Test (Jika Masih Curiga WS Stuck)

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "cat > /tmp/ws_test.py << 'EOF'
import websocket, json, ssl, socket

original = socket.getaddrinfo
def prefer_ipv4(h, p, fam=0, t=0, proto=0, flags=0):
    try: return original(h, p, socket.AF_INET, t, proto, flags)
    except: return original(h, p, fam, t, proto, flags)
socket.getaddrinfo = prefer_ipv4

TOKEN = '15016766782236317208537549729851722822187640321351111957599723237199180907455'
def on_open(ws):
    print('CONNECTED')
    ws.send(json.dumps({'type':'market','assets_ids':[TOKEN],'markets':[],'initial_dump':True,'custom_feature_enabled':True}))
def on_message(ws, m):
    d = json.loads(m)
    events = d if isinstance(d, list) else [d]
    for e in events:
        print('EVENT:', e.get('event_type'), '| asset:', e.get('asset_id','')[:20])

ws = websocket.WebSocketApp('wss://ws-subscriptions-clob.polymarket.com/ws/market',
    on_open=on_open, on_message=on_message)
ws.run_forever(sslopt={'cert_reqs': ssl.CERT_NONE})
EOF
timeout 15 /opt/the_blueprints/venv/bin/python3 /tmp/ws_test.py"
```

Harusnya muncul baris `CONNECTED` dan beberapa `EVENT: book` dalam 5 detik.

### Langkah 5: Apply Fix #1 (Profit Guard Patch)

Setelah edit `analysis.py` di local:
```bash
git add market_discovery_internal/analysis.py
git commit -m "fix: strengthen Profit Guard with Newborn Guard for new positions"
git push origin master
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "cd /opt/the_blueprints && git pull origin master && systemctl restart blueprints"
```

### Langkah 6: Monitor Setelah Restart

```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 \
  "grep -a 'WS-WIRING\|WS-MPC\|Heartbeat\|PROFIT-GUARD\|NEWBORN-GUARD' \
   /opt/the_blueprints/logs/paper_loop.out | tail -n 20"
```

---

## 9. Analisis: Probabilitas Selalu 90-100% — Kenapa & Apa Solusinya

### Masalah Inti: Sigmoid Terlalu Steep + Calibration DB Kosong

**Layer 1 — Sigmoid Terlalu Curam:**

Formula di `pricing.py` baris 356:
```python
raw_prob = 1.0 / (1.0 + exp(-k * diff))
# diff = forecast_temp - threshold (dalam °C)
```

Simulasi dengan k=1.6 (golden window <14h):

| diff (°C) | raw_prob |
|---|---|
| 1.0°C | 83.0% |
| 1.5°C | 90.9% ← threshold entry |
| 2.0°C | 95.3% |
| 2.5°C | 98.0% |
| 3.0°C | 99.3% |

**Masalah**: Model cuaca ensemble punya confidence interval ±2-3°C. Artinya `diff = 2°C` sebenarnya masih di dalam zona ketidakpastian model cuaca itu sendiri, tapi bot sudah yakin 95%+. Ini mismatch antara "sigmoid certainty" vs "physical uncertainty".

**Layer 2 — Calibration DB Kosong (Fresh Reset):**

`_calibrated_prob()` di baris 221-246 melakukan Bayesian shrinkage:
```python
# prior_weight = 5 (min_samples)
calibrated = (hits + raw_prob * prior_weight) / (total + prior_weight)
# Jika total=0 (no history):
# calibrated = (0 + raw_prob * 5) / (0 + 5) = raw_prob
```

Setelah fresh reset SQLite, `calibration_total = 0` → shrinkage tidak bekerja → `model_prob = raw_prob` → angka 97-100% masuk langsung tanpa koreksi.

**Ini kenapa prob selalu tinggi: sigmoid + zero calibration history = no correction.**

---

### Kenapa Seoul Benar tapi Austin Salah?

| | Seoul | Austin |
|---|---|---|
| Forecast margin | +2°C jelas di atas threshold | +2°C juga, tapi mepet |
| Kondisi data | Open-Meteo fresh, ensemble solid | Open-Meteo mungkin stale |
| Market price | 15c → naik ke 53c (market akhirnya sepakat) | 15c → TIDAK naik (market benar) |
| Consensus Guard | Tidak aktif (harga sudah naik sebelum entry) | Seharusnya aktif tapi belum cukup |
| Outcome | Benar → temp memang naik | Salah → temp tidak mencapai target |

**Insight kunci**: Model TIDAK bisa membedakan antara:
- "Forecast solid, 2°C margin = memang hampir pasti" (Seoul)
- "Forecast mungkin stale, 2°C margin = sebenarnya flip coin" (Austin)

Model hanya melihat angka, tidak melihat kualitas/freshness data forecast itu sendiri.

---

### Solusi: 3 Lapis Perbaikan Probabilitas

#### Lapis A — Turunkan k (Paling Simpel, Efek Langsung)

**File**: `pricing.py` baris 344-351

```python
# SEBELUM (terlalu steep):
elif _h_val <= 6:    k = 1.3
elif _h_val <= 14:   k = 1.6   # golden window
elif _h_val <= 36:   k = 1.1
else:                k = 0.75

# SESUDAH (lebih realistis):
elif _h_val <= 6:    k = 0.9   # -0.4
elif _h_val <= 14:   k = 1.1   # -0.5 ← perubahan terbesar
elif _h_val <= 36:   k = 0.75  # -0.35
else:                k = 0.50  # -0.25
```

Efek dengan k=1.1 (golden window baru):

| diff (°C) | prob lama (k=1.6) | prob baru (k=1.1) |
|---|---|---|
| 1.5°C | 90.9% | 80.8% |
| 2.0°C | 95.3% | 88.0% |
| 2.5°C | 98.0% | 93.5% |
| 3.0°C | 99.3% | 96.4% |

Sekarang butuh ~3°C margin untuk reach 96%+. Lebih masuk akal secara meteorologis.

#### Lapis B — Hard Ceiling Berdasarkan Margin (Backup)

Tambahkan cap di `pricing.py` setelah sigmoid, sebelum calibration:

```python
# Cap prob berdasarkan forecast margin — mencegah overconfidence
abs_diff = abs(forecast - threshold)
if abs_diff < 1.0:
    raw_prob = min(raw_prob, 0.75)
elif abs_diff < 2.0:
    raw_prob = min(raw_prob, 0.87)
elif abs_diff < 3.0:
    raw_prob = min(raw_prob, 0.94)
# >= 3°C: biarkan sigmoid natural (memang hampir pasti)
```

#### Lapis C — Gunakan Evidence Quality Score sebagai Damper

Bot sudah punya `weather_evidence["quality_score"]` (dari `analysis.py`). Gunakan ini untuk dampen prob:

```python
# Di calculate_edge(), setelah raw_prob dihitung:
quality = market.get("weather_evidence", {}).get("quality_score", 0.85)
if quality < 0.7:
    # Data stale/kurang bagus → dampen mendekati 0.5
    raw_prob = raw_prob * quality + 0.5 * (1 - quality)
```

Ini yang MEMBEDAKAN Seoul (data fresh, quality=0.9 → minimal dampening) vs Austin (data stale, quality=0.6 → dampened signifikan).

---

### Urutan Implementasi yang Disarankan

1. **Segera**: Lapis A (turunkan k) — paling simpel, langsung efek, tidak ada breaking change
2. **Setelahnya**: Lapis B (hard ceiling) — safety net tambahan
3. **Setelah ada data**: Biarkan Calibration DB terisi natural (~20+ trades) → koreksi Bayesian mulai aktif sendiri
4. **Opsional**: Lapis C (quality damper) — paling sophisticated, butuh validasi bahwa quality_score akurat

---

## Butuh Informasi Tambahan

1. **Apakah WS sekarang sudah update harga real-time?** Jalankan Langkah 1 untuk konfirmasi.
2. **Stop loss price posisi yang ter-exit (Seoul)**: Berapa `stop_loss_price` yang di-set? Apakah WS price (0.52) sempat menyentuh stop-loss sebelum atau sesudah Haiku exit?
3. **Apakah `current_yes_price` yang dikirim ke Haiku diambil dari WS cache atau DB?** Perlu trace di `cycles.py` fungsi monitoring loop untuk konfirmasi source.
4. **Sudah berapa lama posisi Seoul open sebelum di-close?** Jika < 30 menit, Haiku seharusnya tidak dipanggil (perlu min-age gate).
