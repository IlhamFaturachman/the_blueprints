with open("web_ui/index.html", "r") as f:
    html = f.read()

# 1. Insert HTML for the new history table before the Discovery Cycles heading
history_html = """
    <h2 style="margin-top: 40px;">📜 Closed History <span id="closedCountBadge" class="badge mode-manual">0</span></h2>
    <div class="card" style="padding: 0; overflow-x: auto;">
        <table>
            <thead>
                <tr>
                    <th>Market</th>
                    <th>Harga Beli (Ask)</th>
                    <th>Harga Keluar (Exit)</th>
                    <th>Realized PnL</th>
                    <th>Market (Target)</th>
                    <th>Waktu / Alasan Tutup</th>
                </tr>
            </thead>
            <tbody id="historyTable">
                <tr><td colspan="6" style="text-align:center;">Memuat data...</td></tr>
            </tbody>
        </table>
    </div>

    <h2 style="margin-top: 40px;">📓 Recent Discovery Cycles</h2>"""
html = html.replace('<h2 style="margin-top: 40px;">📓 Recent Discovery Cycles</h2>', history_html)

# 2. Add JavaScript logic to render history
history_js = """
        const tHist = document.getElementById('historyTable');
        tHist.innerHTML = "";
        document.getElementById('closedCountBadge').innerHTML = d_closed.length;
        
        if(d_closed.length === 0) {
            tHist.innerHTML = "<tr><td colspan='6' style='text-align:center;'>Belum ada histori penutupan posisi.</td></tr>";
        } else {
            let sortedHistory = [...d_closed].sort((a, b) => new Date(b.closed_at || 0) - new Date(a.closed_at || 0)).slice(0, 50);
            
            sortedHistory.forEach(pos => {
                const rPnl = pos.realized_pnl_usd || 0.0;
                const pnlCls = rPnl >= 0 ? "good" : "bad";
                const pnlPct = pos.realized_roi_pct || 0;
                
                let directionLabel = pos.direction === 'above' ? '≥' : (pos.direction === 'below' ? '≤' : '=');
                const targetText = `Posisi YES: Suhu ${directionLabel} ${pos.threshold || ''}°${pos.unit || ''}`;
                const closeReason = pos.close_reason ? pos.close_reason.replace(/_/g, ' ') : '-';
                
                let rawSlug = pos.market_slug || "";
                let cleanSlug = rawSlug.replace(/-[0-9]+[cf]?(orhigher|orbelow)?$/i, '');
                const marketUrl = cleanSlug ? `https://polymarket.com/event/${cleanSlug}` : `https://polymarket.com/search?query=${encodeURIComponent(pos.market_question || pos.city)}`;
                
                let closedTime = pos.closed_at ? new Date(pos.closed_at).toLocaleString() : '-';

                tHist.innerHTML += `
                    <tr>
                        <td>
                            <strong><a href="${marketUrl}" target="_blank" style="color: #03a9f4; text-decoration: underline;">${pos.city ? pos.city.toUpperCase() : 'Token ' + pos.token_id.substring(0,8)}</a></strong>
                        </td>
                        <td>$${(pos.entry_price || 0).toFixed(4)}</td>
                        <td>$${(pos.exit_price || pos.last_price || 0).toFixed(4)}</td>
                        <td class="${pnlCls}"><strong>$${rPnl.toFixed(4)} <small>(${pnlPct > 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</small></strong></td>
                        <td><span class="badge mode-auto" style="margin-bottom:4px; display:inline-block; border-color: #888;">${targetText}</span></td>
                        <td><small>${closedTime}</small><br><small style="color: #cf6679">${closeReason.toUpperCase()}</small></td>
                    </tr>
                `;
            });
        }

        const tCyc = document.getElementById('cycleTable');"""
html = html.replace("const tCyc = document.getElementById('cycleTable');", history_js)

with open("web_ui/index.html", "w") as f:
    f.write(html)
print("UI History Patched")
