# 🚀 IMPROVEMENT PLAN: Phase 3 (The Hardened Resilience)

This document outlines the strategic improvements to neutralize the remaining flaws identified in the "Gold Standard" build. **USER ACTION REQUIRED**: Please answer the questions in the sections below to finalize the implementation requirements.

---

## 📅 1. Precise Timezone Synchronization

**Flaw**: Current `lon / 15` logic is a coarse estimation and fails for DST or large countries (e.g., China).

### Proposed Changes

- Replace manual estimation with a robust timezone lookup (e.g., integrated city-to-timezone map).
- Implement DST-aware NOAA/METAR monitoring windows.

> [!IMPORTANT]
> **Question 1**: Should we add a Python dependency like `pytz` atau `timezonefinder`, atau Anda lebih suka _hardcoded_ IANA timezone map di `config.py` untuk menjaga environment VPS tetap minimal? = jawab saya ingin timezone sync nya sangat akurat agar tidak miss ketika nanti cari lokasinya.

---

## 📊 2. Adaptive "Shield" (Circuit Breaker)

**Flaw**: Limit $1.50 drawdown tetap untuk wallet $5. Ini akan menghentikan portfolio Tier 2/3 terlalu dini.

### Proposed Changes

- Ubah `DAILY_LOSS_LIMIT` dari nilai mata uang statis menjadi **Percentage-based Drawdown** (misal: 10% dari Daily Baseline).
- Implementasi "Safe-Mode" otomatis: kurangi stake sebesar 50% jika ambang batas kerugian tingkat pertama tercapai.

> [!IMPORTANT]
> **Question 2**: Berapa persentase Max Daily Drawdown yang Anda inginkan (misal: 10%, 15%) sebelum bot benar-benar berhenti total? = jawab 15%

---

## 🌊 3. Liquidity-Aware Execution

**Flaw**: Simulasi 1% slippage mungkin tidak mencerminkan kedalaman market nyata untuk order yang lebih besar.

### Proposed Changes

- Implementasi **"Depth-Adjusted Stake"**: jika kedalaman top-level orderbook kurang dari 3x stake kita, otomatis kurangi ukuran order.
- Tambahkan penalti pelebaran spread ke logika `calculate_edge`.

> [!IMPORTANT]
> **Question 3**: Saat naik ke Tier 2 ($20+), berapa slippage maksimum (%) yang bersedia Anda terima pada satu entry? = jawab, ini kamu yang ngatur aja, saya percaya kamu pasti paham dan tau bagusnya berapa

---

## 🤖 4. AI Budget & Safety Guardrails

**Flaw**: Kehabisan budget AI membuat bot "buta" terhadap jebakan (traps).

### Proposed Changes

- Implementasi mode **"Safety-First"**: jika budget AI sudah < 10% tersisa, bot membatasi entry hanya ke "Safe Cities" (kota-kota NOAA AS).
- Tambahkan "Budget Warmer": prioritaskan panggilan AI untuk kota-kota dengan profitabilitas tertinggi.

---

## 🛰️ 5. "Warmer" Watchdog & Health Checks

**Flaw**: Jika warmer di latar belakang mati, loop utama akan stagnan secara diam-diam.

### Proposed Changes

- Implementasi **IPC (Inter-Process Communication)** heartbeat antara `warmer.py` dan `market_discovery.py`.
- Bot akan mengirimkan "Emergency Telegram Alert" jika cache belum diperbarui selama > 3 jam.

---

## 📝 User Response Area

Silakan berikan jawaban Anda di sini atau di chat:

- **A1 (Timezone)**:
- **A2 (Max Drawdown %)**:
- **A3 (Max Slippage %)**:
- **A4 (Prioritas lain?)**:

A4 saya ingin nutup flaws ini sesempurna mungkin, dan tidak ada masalah sama sekali

---

**SIGIL**: `PLAN_PHASE_3_PENDING_APPROVAL`
