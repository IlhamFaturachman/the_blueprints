# GEMINI EXECUTION GUIDE — Bot Hardening (3 Fixes)

**BACA DULU SEBELUM MULAI:**
- Ini adalah instruksi eksekusi, bukan diskusi. Ikuti urutan TEPAT.
- Setiap langkah ada VERIFIKASI. Jangan lanjut ke langkah berikutnya sebelum verifikasi lulus.
- Jika ada error, STOP dan laporkan. Jangan coba-coba fix sendiri.
- SSH server: `ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158`
- Setelah semua selesai: git push → git pull di server → restart service.

---

## KONTEKS SINGKAT (baca sekali, lalu lanjut eksekusi)

Dua bug terjadi setelah posisi Seoul dibuka:
1. WebSocket stuck → harga freeze → Haiku panik → posisi Seoul di-close paksa meski harga nyata naik 28%.
2. Probabilitas model selalu 90-100% → terlalu percaya diri → kadang salah besar (Austin).

Fix yang harus dieksekusi: 3 perubahan kode di 2 file.

---

## FIX #1 — Perkuat Profit Guard di `analysis.py`

**Masalah**: Profit Guard sekarang hanya melindungi posisi yang sudah profit >5%. Posisi baru yang baru saja dibuka (harga belum settle dari spread) tidak terlindungi. Haiku bisa close posisi yang hanya -1.9% meski WS price stale.

**File**: `market_discovery_internal/analysis.py`

### Langkah 1.1 — Temukan blok ini (EXACT, copy-paste untuk search):

```python
        # [FIX] Profit Guard: Override AI if the position is clearly winning.
        # This protects us from 'False Panic' during price feed lags.
        if result.get("action") == "close":
            try:
                # If PNL is positive and significant (>5%), forced hold.
                cur_pnl = float(pnl_pct) if pnl_pct is not None else 0.0
                if cur_pnl > 5.0:
                    result["action"] = "hold"
                    result["reasoning"] = f"[PROFIT-GUARD] Overrode AI close due to {cur_pnl}% profit. " + result.get("reasoning", "")
            except:
                pass
```

### Langkah 1.2 — Ganti SELURUH blok di atas dengan ini:

```python
        # [FIX] Profit Guard + Newborn Guard: Override AI if position is winning OR too new.
        # This protects from 'False Panic' during price feed lags and spread noise.
        if result.get("action") == "close":
            try:
                cur_pnl = float(pnl_pct) if pnl_pct is not None else 0.0

                # Calculate position age in hours
                age_hours = 0.0
                opened_at_str = position.get("opened_at")
                if opened_at_str:
                    try:
                        from datetime import datetime, timezone
                        opened_at = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                        age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                    except Exception:
                        pass

                is_newborn = age_hours < 2.0  # Position under 2 hours old

                if cur_pnl > 5.0:
                    # Clearly profitable — block exit
                    result["action"] = "hold"
                    result["reasoning"] = f"[PROFIT-GUARD] Blocked: P&L={cur_pnl:.1f}% > 5%. " + result.get("reasoning", "")
                elif is_newborn and cur_pnl > -15.0:
                    # Position under 2h old, not deeply underwater — allow spread to settle
                    result["action"] = "hold"
                    result["reasoning"] = f"[NEWBORN-GUARD] Blocked: Age={age_hours:.1f}h < 2h, P&L={cur_pnl:.1f}%. Spread still settling. " + result.get("reasoning", "")
            except Exception:
                pass
```

### Verifikasi Fix #1:

Pastikan file tersimpan. Cari string `[NEWBORN-GUARD]` di file — harus ditemukan:
```bash
grep -n "NEWBORN-GUARD" market_discovery_internal/analysis.py
```
Output harus menampilkan nomor baris (bukan kosong).

---

## FIX #2 — Turunkan k-factor Sigmoid di `pricing.py`

**Masalah**: k=1.6 untuk golden window membuat selisih 2°C sudah menghasilkan prob 95%+. Ini tidak realistis — model cuaca sendiri punya uncertainty ±2-3°C. Perlu k lebih rendah agar butuh margin lebih besar untuk reach confidence tinggi.

**Perubahan k-values**:
- `≤6h`: 1.3 → 0.9
- `≤14h` (golden window): 1.6 → 1.1
- `≤36h`: 1.1 → 0.75
- `>36h`: 0.75 → 0.50

**File**: `market_discovery_internal/pricing.py`

### Langkah 2.1 — Temukan blok ini (EXACT, copy-paste untuk search):

```python
        if k_factor_override is not None:
            k = k_factor_override
        elif _h_val <= 6:
            k = 1.3
        elif _h_val <= 14:
            k = 1.6
        elif _h_val <= 36:
            k = 1.1
        else:
            k = 0.75
```

### Langkah 2.2 — Ganti SELURUH blok di atas dengan ini:

```python
        if k_factor_override is not None:
            k = k_factor_override
        elif _h_val <= 6:
            k = 0.9
        elif _h_val <= 14:
            k = 1.1
        elif _h_val <= 36:
            k = 0.75
        else:
            k = 0.50
```

### Verifikasi Fix #2:

```bash
grep -n "k = 1.1" market_discovery_internal/pricing.py
```
Harus muncul satu baris dengan `elif _h_val <= 14:` di atasnya.

```bash
grep -n "k = 1.6" market_discovery_internal/pricing.py
```
Harus TIDAK muncul (0 hasil) — berarti nilai lama sudah hilang.

---

## FIX #3 — Tambahkan Hard Ceiling Cap di `pricing.py`

**Masalah**: Meskipun k sudah diturunkan, untuk diff besar (>3°C) prob masih bisa 99%+. Perlu safety net agar prob tidak pernah terlalu ekstrem berdasarkan margin forecast semata.

**File**: `market_discovery_internal/pricing.py`

### Langkah 3.1 — Temukan blok ini (EXACT, copy-paste untuk search):

```python
        if direction == "above":
            # Sigmoid: smooth gradient around threshold.
            diff = max(-50, min(50, (forecast - threshold)))
            raw_prob = 1.0 / (1.0 + math.exp(-k * diff))
        elif direction == "below":
            diff = max(-50, min(50, (threshold - forecast)))
            raw_prob = 1.0 / (1.0 + math.exp(-k * diff))
```

### Langkah 3.2 — Ganti SELURUH blok di atas dengan ini:

```python
        if direction == "above":
            # Sigmoid: smooth gradient around threshold.
            diff = max(-50, min(50, (forecast - threshold)))
            raw_prob = 1.0 / (1.0 + math.exp(-k * diff))
        elif direction == "below":
            diff = max(-50, min(50, (threshold - forecast)))
            raw_prob = 1.0 / (1.0 + math.exp(-k * diff))

        # [FIX] Hard ceiling: cap confidence based on forecast margin.
        # Weather ensemble uncertainty is ±2-3°C, so even large margins
        # should not yield near-100% certainty from the sigmoid alone.
        if direction in ("above", "below"):
            abs_diff = abs(forecast - threshold)
            if abs_diff < 1.0:
                raw_prob = min(raw_prob, 0.75)
            elif abs_diff < 2.0:
                raw_prob = min(raw_prob, 0.87)
            elif abs_diff < 3.0:
                raw_prob = min(raw_prob, 0.94)
            # abs_diff >= 3.0: no cap — margin is large enough to justify high confidence
```

### Verifikasi Fix #3:

```bash
grep -n "Hard ceiling" market_discovery_internal/pricing.py
```
Harus muncul satu baris.

```bash
grep -n "abs_diff" market_discovery_internal/pricing.py
```
Harus muncul beberapa baris (3-4 baris dari blok yang baru ditambahkan).

---

## DEPLOYMENT — Setelah Semua Fix Selesai

### Langkah D1 — Cek status git (pastikan hanya 2 file yang berubah):
```bash
git diff --name-only
```
Output yang diharapkan:
```
market_discovery_internal/analysis.py
market_discovery_internal/pricing.py
```
Jika ada file lain yang berubah → STOP, laporkan ke user.

### Langkah D2 — Commit dan push ke GitHub:
```bash
git add market_discovery_internal/analysis.py market_discovery_internal/pricing.py
git commit -m "fix: strengthen Profit Guard with Newborn Guard, tune sigmoid k-factors, add prob ceiling cap"
git push origin master
```

### Langkah D3 — Pull dan restart di server:
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "cd /opt/the_blueprints && git pull origin master && systemctl restart blueprints"
```

### Langkah D4 — Verifikasi server berjalan normal:
```bash
ssh -i ~/.ssh/id_ed25519_blueprints root@103.253.244.158 "sleep 10 && tail -n 30 /opt/the_blueprints/logs/paper_loop.out"
```

Output yang NORMAL (harus ada salah satu):
- `[WS-WIRING] Initial sync: monitoring X active tokens`
- `Starting paper loop every 300s`
- `[BOOT] Data Warmer started`

Output yang BERARTI ADA MASALAH (STOP jika muncul):
- `ImportError`
- `SyntaxError`
- `Paper cycle failed`
- `Traceback`

### Langkah D5 — Update gemini_job.md (append, JANGAN rewrite):

Tambahkan log baru di bagian PALING BAWAH file `gemini_job.md`:

```markdown
### [TANGGAL DAN JAM SEKARANG WIB] Prob Calibration & Profit Guard Hardening
- **Status**: ✅ COMPLETED
- **Task**: Fix overconfident probability model dan perluas proteksi Haiku false exit.
- **Changes**:
    - **analysis.py**: Tambahkan Newborn Guard — posisi <2 jam dan P&L >-15% tidak bisa di-close Haiku (mencegah Seoul incident terulang).
    - **pricing.py**: Turunkan sigmoid k-factors (golden window: 1.6→1.1, dst) agar prob 90%+ butuh margin >2.5°C.
    - **pricing.py**: Tambahkan hard probability ceiling berdasarkan forecast margin (<1°C→cap 75%, <2°C→cap 87%, <3°C→cap 94%).
- **Verification**: grep "NEWBORN-GUARD" analysis.py → found. grep "Hard ceiling" pricing.py → found. Bot restart sukses tanpa error.
```

---

## CHECKLIST AKHIR

Sebelum lapor selesai, konfirmasi semua ini:

- [ ] `grep -n "NEWBORN-GUARD" market_discovery_internal/analysis.py` → ada output
- [ ] `grep -n "k = 1.6" market_discovery_internal/pricing.py` → TIDAK ada output
- [ ] `grep -n "Hard ceiling" market_discovery_internal/pricing.py` → ada output
- [ ] `git diff --name-only` setelah commit → kosong (semua sudah committed)
- [ ] Server log setelah restart tidak ada `ImportError` atau `SyntaxError`
- [ ] `gemini_job.md` sudah di-append (bukan rewrite)

Jika semua centang → SELESAI. Laporkan hasil verifikasi ke user.
