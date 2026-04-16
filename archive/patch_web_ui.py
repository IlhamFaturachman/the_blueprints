with open("web_ui/index.html", "r") as f:
    html = f.read()

html = html.replace("Mode: Paper / Demo ($100 Init)", "Mode: Paper / Demo ($5 Init)")
html = html.replace("let initial_balance = 100.0;", "let initial_balance = 5.0;")

# We want to display the current stake multiplier.
stake_display_code = """
        let initial_balance = 5.0; 
        let current_balance = initial_balance + total_pnl + openPnlTotal; // Saldo bersih termasuk open pnl
        let sim_stake = current_balance >= 10.0 ? Math.floor(current_balance / 5.0) : 1.0;
        document.getElementById('valWallet').innerHTML = `$${current_balance.toFixed(4)}`;
        document.getElementById('valWalletMode').innerHTML = `Mode: Paper | Stake: $${sim_stake}/trade`;
"""
import re
html = re.sub(r'let initial_balance.*?document\.getElementById\(\'valWallet\'\)\.innerHTML.*?;', stake_display_code, html, flags=re.DOTALL)

with open("web_ui/index.html", "w") as f:
    f.write(html)
print("web_ui patched")
