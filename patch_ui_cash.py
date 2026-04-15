with open("web_ui/index.html", "r") as f:
    js = f.read()

# Update the HTML heading for Portfolio
js = js.replace("<h3>💳 Live Wallet Balance</h3>", "<h3>💳 Portfolio Value</h3>")

# Update JS calculation block
old_js_block = """        // Saldo Live Trade (Simulasi)
        
        let initial_balance = 5.0; 
        let current_balance = initial_balance + total_pnl + openPnlTotal; // Saldo bersih termasuk open pnl
        let sim_stake = current_balance >= 10.0 ? Math.floor(current_balance / 5.0) : 1.0;
        document.getElementById('valWallet').innerHTML = `$${current_balance.toFixed(4)}`;
        document.getElementById('valWalletMode').innerHTML = `Mode: Paper | Stake: $${sim_stake}/trade`;"""

new_js_block = """        // Fetch Cash Data from Meta (Bot Engine direct injection)
        const meta = data.meta || {};
        let current_wallet = typeof meta.current_wallet === 'number' ? meta.current_wallet : (5.0 + total_pnl);
        let cash = meta.cash || (current_wallet - (meta.open_cost_basis || 0));
        let pos_limit = meta.open_positions_count || openKeys.length;
        
        let portfolio_value = current_wallet + openPnlTotal; 
        let sim_stake = current_wallet >= 10.0 ? Math.floor(current_wallet / 5.0) : 1.0;

        document.getElementById('valWallet').innerHTML = `$${portfolio_value.toFixed(4)}`;
        document.getElementById('valWalletMode').innerHTML = `Cash: $${cash.toFixed(4)} | Stake: $${sim_stake}/trade`;"""

js = js.replace(old_js_block, new_js_block)

with open("web_ui/index.html", "w") as f:
    f.write(js)
print("UI cash patched")
