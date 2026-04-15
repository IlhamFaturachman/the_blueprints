with open("web_ui/index.html", "r") as f:
    js = f.read()

# Fix table header
js = js.replace("<th>Entry Price</th>", "<th>Harga Beli (Ask)</th>")
js = js.replace("<th>Current Price</th>", "<th>Harga Pasar (Bid)</th>")
js = js.replace("<th>Target outcome</th>", "<th>Market (Target)</th>")

# Fix row construction
old_js_block = """                const targetText = pos.direction ? `${pos.direction.toUpperCase()} ${pos.threshold || ''}°${pos.unit || ''}` : 'LONG';
                const bucketText = pos.entry_bucket ? pos.entry_bucket : '-';
                
                const marketUrl = pos.market_slug ? `https://polymarket.com/event/${pos.market_slug}` : `https://polymarket.com/search?query=${encodeURIComponent(pos.market_question || pos.city)}`;
                tPos.innerHTML += `
                    <tr>
                        <td><strong>${pos.city || 'Token ' + pos.token_id.substring(0,8)}</strong></td>
                        <td>$${(pos.entry_price || 0).toFixed(4)}</td>
                        <td>$${(pos.cost_basis || 0).toFixed(2)}</td>
                        <td>$${pos.last_price ? pos.last_price.toFixed(4) : (pos.entry_price||0).toFixed(4)}</td>
                        <td class="${pnlCls}"><strong>$${unPnl.toFixed(4)} <small>(${pnlPct > 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</small></strong></td>
                        <td><span class="badge mode-auto" style="margin-bottom:4px; display:inline-block;">${targetText}</span><br><small>${bucketText.replace(/_/g, ' ')}</small></td>
                        <td>
                            <a href="${marketUrl}" target="_blank" class="refresh-btn" style="padding: 5px 10px; font-size:12px; background: #03a9f4; text-decoration: none; color: white; margin-right: 5px;">Buka Market</a>"""

new_js_block = """                let directionLabel = pos.direction === 'above' ? '≥' : (pos.direction === 'below' ? '≤' : '=');
                const targetText = `Posisi YES: Suhu ${directionLabel} ${pos.threshold || ''}°${pos.unit || ''}`;
                const bucketText = pos.entry_bucket ? pos.entry_bucket.replace(/_/g, ' ') : '-';
                
                // Polymarket slugs sometimes have suffixes like -24corhigher, which return 404s. We strip them out.
                let rawSlug = pos.market_slug || "";
                let cleanSlug = rawSlug.replace(/-[0-9]+(c|f)?(|orhigher|orbelow)$/i, '');
                const marketUrl = cleanSlug ? `https://polymarket.com/event/${cleanSlug}` : `https://polymarket.com/search?query=${encodeURIComponent(pos.market_question || pos.city)}`;
                
                tPos.innerHTML += `
                    <tr>
                        <td>
                            <strong>${pos.city ? pos.city.toUpperCase() : 'Token ' + pos.token_id.substring(0,8)}</strong>
                        </td>
                        <td>$${(pos.entry_price || 0).toFixed(4)}</td>
                        <td>$${(pos.cost_basis || 0).toFixed(2)}</td>
                        <td>$${pos.last_price ? pos.last_price.toFixed(4) : (pos.entry_price||0).toFixed(4)}</td>
                        <td class="${pnlCls}"><strong>$${unPnl.toFixed(4)} <small>(${pnlPct > 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</small></strong></td>
                        <td><span class="badge mode-auto" style="margin-bottom:4px; display:inline-block;">${targetText}</span><br><small style="color: #bbb">${bucketText}</small></td>
                        <td>
                            <a href="${marketUrl}" target="_blank" class="refresh-btn" style="padding: 5px 10px; font-size:12px; background: #03a9f4; text-decoration: none; color: white; margin-right: 5px;">Buka Market</a>"""

js = js.replace(old_js_block, new_js_block)

with open("web_ui/index.html", "w") as f:
    f.write(js)
print("UI Names Patched")
