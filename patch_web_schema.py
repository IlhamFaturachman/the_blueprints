import re

with open("web_ui/index.html", "r") as f:
    js = f.read()

# Replace assignments
js = js.replace("const d_open = data.open_positions || {};", "const d_open = data.positions || [];")
js = js.replace("const d_closed = data.closed_positions || {};", "const d_closed = data.history || [];")
js = js.replace("const openKeys = Object.keys(d_open);", "const openKeys = Object.keys(d_open); // using as array indices")
js = js.replace("const closedKeys = Object.keys(d_closed);", "const closedKeys = Object.keys(d_closed);")

# total_pnl needs to summarize history
# the original code did: let total_pnl = data.realized_pnl || 0.0;
pnl_block = """let total_pnl = data.realized_pnl || 0.0;
        if(last_cycle && typeof last_cycle.closed_realized_pnl_usd === 'number' && total_pnl === 0) {
           total_pnl = last_cycle.closed_realized_pnl_usd;
        }"""
new_pnl_block = """let total_pnl = 0.0;
        d_closed.forEach(pos => { total_pnl += (pos.realized_pnl_usd || 0.0); });"""
js = js.replace(pnl_block, new_pnl_block)

# unrealized pnl logic
unpnl = "openKeys.forEach(k => { openPnlTotal += (d_open[k].unrealized_pnl_usd || 0.0); });"
new_unpnl = """openKeys.forEach(k => { 
            let pos = d_open[k];
            let upnl = (pos.last_price || pos.entry_price || 0) * (pos.quantity || 0) - (pos.cost_basis || 0);
            openPnlTotal += upnl; 
        });"""
js = js.replace(unpnl, new_unpnl)

# Table row rendering
table_row = """const unPnl = pos.unrealized_pnl_usd ? pos.unrealized_pnl_usd : 0.0;"""
new_table_row = """const unPnl = ((pos.last_price || pos.entry_price || 0) * (pos.quantity || 0)) - (pos.cost_basis || 0);"""
js = js.replace(table_row, new_table_row)

id_row = """<td><strong>${pos.market_id || k}</strong></td>"""
new_id_row = """<td><strong>${pos.city || 'Token ' + pos.token_id.substring(0,8)}</strong></td>"""
js = js.replace(id_row, new_id_row)

stake_row = """<td>$${(pos.stake_usd || 0).toFixed(2)}</td>"""
new_stake_row = """<td>$${(pos.cost_basis || 0).toFixed(2)}</td>"""
js = js.replace(stake_row, new_stake_row)

with open("web_ui/index.html", "w") as f:
    f.write(js)
print("patch completed")
