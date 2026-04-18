import os
import sys

# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_discovery_internal.utils import load_telegram_template

def test_templates():
    print("--- Testing Telegram Templates ---")
    
    # Test Entry
    entry_msg = load_telegram_template(
        category="execution",
        type_name="entry",
        market_name="Test Market",
        outcome="YES (Target 33.0C)",
        prob="95.5",
        strategy="SWING",
        price="0.5000",
        stake="2.00",
        tp="0.9500",
        cash="10.50",
        url="https://polymarket.com"
    )
    print("\n[ENTRY TEMPLATE]:")
    print(entry_msg)
    
    # Test Circuit Breaker
    cb_msg = load_telegram_template(
        category="risk",
        type_name="circuit_breaker",
        reason_text="Max Daily Drawdown Reached",
        current_loss="2.01",
        limit_amount="1.50",
        cash="2.99"
    )
    print("\n[CIRCUIT BREAKER TEMPLATE]:")
    print(cb_msg)

    # Test Summary
    summary_msg = load_telegram_template(
        category="system",
        type_name="summary",
        date_str="2026-04-18",
        status="ONLINE",
        integrity_pct="100",
        log_status="Clean",
        total_trades=10,
        win_rate="70.0",
        pnl_emoji="🟢",
        pnl_usd="+0.5000",
        cash="5.50",
        sync_status="IN SYNC"
    )
    print("\n[SUMMARY TEMPLATE]:")
    print(summary_msg)

if __name__ == "__main__":
    test_templates()
