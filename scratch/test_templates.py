import sys
import os
import time

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.utils import load_telegram_template, send_telegram_alert

def send_test(name, msg):
    print(f"--- Sending {name} ---")
    res = send_telegram_alert(msg)
    if res:
        print(f"[{name.upper()}]: SENT SUCCESS")
    else:
        print(f"[{name.upper()}]: FAILED TO SEND")
    time.sleep(1.5) # Safe gap

def test_templates():
    # 1. Test Startup
    startup_msg = load_telegram_template(
        category="system",
        type_name="startup",
        commit_hash="9341b6b",
        env_name="PRODUCTION",
        timestamp="2026-04-18 12:00:00"
    )
    send_test("startup", startup_msg)

    # 2. Test Entry
    entry_msg = load_telegram_template(
        category="execution",
        type_name="entry",
        market_name="Market Zero-Flaw Audit",
        outcome="YES",
        prob="99.0",
        strategy="SWING",
        price="0.50",
        stake="5.0",
        tp="1.0",
        cash="100.0",
        url="https://polymarket.com"
    )
    send_test("entry", entry_msg)

    # 3. Test Exit
    exit_msg = load_telegram_template(
        category="execution",
        type_name="exit",
        market_name="Market Zero-Flaw Audit",
        result_emoji="✅",
        result_text="PROFIT",
        pnl_emoji="🟢",
        pnl_usd="+1.0",
        pnl_pct="20.0",
        returned_amount="6.0",
        cash="106.0"
    )
    send_test("exit", exit_msg)

    # 4. Test Risk Guard
    rg_msg = load_telegram_template(
        category="risk",
        type_name="risk_guard",
        timestamp="12:00:00",
        risk_type="MAX_EXPOSURE",
        details="Exposure reached $50.00 limits",
        action="PAUSING_NEW_TRADES"
    )
    send_test("risk_guard", rg_msg)

    # 5. Test Tier Update
    tu_msg = load_telegram_template(
        category="system",
        type_name="tier_update",
        new_tier="2",
        wallet_balance=105.20,
        current_stake_usd=5.00,
        max_slots=15
    )
    send_test("tier_update", tu_msg)

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
    send_test("summary", summary_msg)

if __name__ == "__main__":
    test_templates()
