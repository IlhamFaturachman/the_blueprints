import os
import sys

# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.utils import load_telegram_template

def test_templates():
    print("--- Testing Telegram Templates (Fixed Suite) ---")
    
    # 1. Test Entry
    entry_msg = load_telegram_template(
        category="execution",
        type_name="entry",
        market_name="Market Uji Coba Premium",
        outcome="YES (Target 33.0C)",
        prob="98.5",
        strategy="SWING",
        price="0.5500",
        stake="2.50",
        tp="0.9900",
        cash="110.20",
        url="https://polymarket.com"
    )
    print("\n[ENTRY]: OK" if "[N/A]" not in entry_msg else f"\n[ENTRY] MISSING VARS: {entry_msg}")
    
    # 2. Test Exit
    exit_msg = load_telegram_template(
        category="execution",
        type_name="exit",
        market_name="Market Uji Coba Premium",
        result_emoji="✅",
        result_text="PROFIT",
        pnl_emoji="🟢",
        pnl_usd="+0.45",
        pnl_pct="+15.0",
        returned_amount="2.95",
        cash="112.50"
    )
    print("[EXIT]: OK" if "[N/A]" not in exit_msg else f"[EXIT] MISSING VARS: {exit_msg}")

    # 3. Test Circuit Breaker
    cb_msg = load_telegram_template(
        category="risk",
        type_name="circuit_breaker",
        reason_text="Max Daily Drawdown (15%) Reached",
        current_loss="2.55",
        limit_amount="2.00",
        cash="98.45"
    )
    print("[CIRCUIT]: OK" if "[N/A]" not in cb_msg else f"[CIRCUIT] MISSING VARS: {cb_msg}")

    # 4. Test Risk Guard
    rg_msg = load_telegram_template(
        category="risk",
        type_name="risk_guard",
        market_name="Market Risiko Tinggi",
        risk_type="LIQUIDITY_SHOCK",
        details="Slippage > 5% detected.",
        action="SKIPPED"
    )
    print("[RISK_GUARD]: OK" if "[N/A]" not in rg_msg else f"[RISK_GUARD] MISSING VARS: {rg_msg}")

    # 5. Test Tier Update
    tu_msg = load_telegram_template(
        category="system",
        type_name="tier_update",
        new_tier="2",
        wallet_balance=105.20,
        current_stake_usd=5.00,
        max_slots=15
    )
    print("[TIER_UPDATE]: OK" if "[N/A]" not in tu_msg else f"[TIER_UPDATE] MISSING VARS: {tu_msg}")

    # 6. Test Summary
    summary_msg = load_telegram_template(
        category="system",
        type_name="summary",
        date_str="2026-04-18",
        status="OPERATIONAL",
        integrity_pct="100",
        log_status="CLEAN",
        total_trades=10,
        win_rate="70.0",
        pnl_emoji="🟢",
        pnl_usd="+5.50",
        cash="115.50",
        sync_status="SYNCED"
    )
    print("[SUMMARY]: OK" if "[N/A]" not in summary_msg else f"[SUMMARY] MISSING VARS: {summary_msg}")

if __name__ == "__main__":
    test_templates()
