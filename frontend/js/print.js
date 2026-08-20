/**
 * iPos 5.0 — Thermal Receipt Printer & PDF Export Components
 * ---------------------------------------------------------
 */

// --- INTERNAL UTILS (Renamed to avoid conflict with api.js) ---
const pdfRp = (n) => "Rp " + Math.round(n || 0).toLocaleString("id-ID");
const pdfFmtDate = (d) => d ? new Date(d + "T00:00:00").toLocaleDateString("id-ID", { day: "2-digit", month: "long", year: "numeric" }) : "-";

async function getPrintSettings() {
  try {
    const server = await api("GET", "/print/settings");
    return {
      storeName: server.receipt_name,
      storeAddr: server.address,
      storePhone: server.phone,
    };
  } catch (_) {
    try {
      return JSON.parse(localStorage.getItem("ipos_print_settings") || "{}");
    } catch (_) {
      return {};
    }
  }
}

// --- SHIFT REPORT ---
async function printShiftReport(shift) {
  const win = window.open("", "_blank", "width=350,height=600");
  if (!win) { alert("Pop-up diblokir browser."); return; }
  win.document.write("<p style='font-family:sans-serif;text-align:center;margin-top:40px'>Menyiapkan laporan shift...</p>");
  const s = await getPrintSettings();
  const STORE_NAME = s.storeName || "iPos 5.0";
  const openTime  = shift.opened_at ? new Date(shift.opened_at).toLocaleString("id-ID") : "-";
  const closeTime = shift.closed_at ? new Date(shift.closed_at).toLocaleString("id-ID") : "-";

  const html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
  @page { margin: 0; size: 80mm auto; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Courier New', monospace; font-size: 11px; width: 76mm; padding: 3mm; }
  .center { text-align: center; } .bold { font-weight: bold; } .big { font-size: 14px; font-weight: bold; }
  .sep { border-top: 1px dashed #000; margin: 4px 0; } table { width: 100%; }
  td { padding: 1px 0; vertical-align: top; } .right { text-align: right; }
</style></head><body>
  <div class="center"><div class="big">${STORE_NAME}</div><div>LAPORAN SHIFT KASIR</div></div>
  <div class="sep"></div>
  <table><tr><td>Kasir</td><td class="right">${shift.username || "-"}</td></tr><tr><td>Buka</td><td class="right">${openTime}</td></tr><tr><td>Tutup</td><td class="right">${closeTime}</td></tr></table>
  <div class="sep"></div>
  <table><tr><td>Modal Awal</td><td class="right">${pdfRp(shift.opening_cash)}</td></tr><tr><td>Total Penjualan</td><td class="right">${pdfRp(shift.total_sales)}</td></tr><tr><td>Jml Transaksi</td><td class="right">${shift.total_transactions || 0}</td></tr></table>
  <div class="sep"></div>
  <table><tr class="bold"><td>Kas Sistem</td><td class="right">${pdfRp(shift.system_cash)}</td></tr><tr class="bold"><td>Kas Aktual</td><td class="right">${pdfRp(shift.closing_cash)}</td></tr>
    <tr class="bold ${(shift.difference || 0) < 0 ? "diff-neg" : ""}"><td>Selisih</td><td class="right">${(shift.difference || 0) >= 0 ? "+" : ""}${pdfRp(shift.difference)}</td></tr>
  </table><div class="sep"></div><div class="center" style="font-size:10px">iPos 5.0</div>
</body></html>`;

  win.document.open(); win.document.write(html); win.document.close(); win.focus();
  setTimeout(() => { win.print(); setTimeout(() => win.close(), 1500); }, 300);
}

// --- REUSABLE PDF COMPONENTS ---

/**
 * generatePurchaseHTML
 * Merender template HTML untuk Faktur Pembelian atau PO
 */
function generatePurchaseHTML(p) {
  const isPO = p.status === 'draft' || p.is_branch_request;
  const docTitle = isPO ? "PESANAN PEMBELIAN (PO)" : "FAKTUR PEMBELIAN";
  
  // Surat jalan/faktur letterhead selalu cabang pembuat dokumen
  const destBranch = p.branch || {};

  // Kalo request antar cabang, "Supplier" (yang menerima pesanan) adalah cabang tujuan
  const supplier = (p.is_branch_request && p.target_branch) ? p.target_branch : (p.supplier || {});  
  const items = p.items || [];

  const itemRows = items.map((i, idx) => `
    <tr>
      <td style="text-align:center">${idx + 1}</td>
      <td>${i.item?.code || "-"}</td>
      <td>${i.item?.name || "-"}</td>
      <td style="text-align:center">${i.qty}</td>
      <td style="text-align:right">${pdfRp(i.buy_price)}</td>
      <td style="text-align:right">${pdfRp(i.total)}</td>
    </tr>`).join("");

  return `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${docTitle} ${p.number}</title>
<style>
  @page { margin: 15mm; size: A4; }
  body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; font-size: 12px; color: #333; line-height: 1.5; }
  .header { display: flex; justify-content: space-between; margin-bottom: 30px; border-bottom: 2px solid #333; padding-bottom: 10px; }
  .header h1 { margin: 0; color: #1e293b; font-size: 24px; }
  .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-bottom: 30px; }
  .info-box h3 { margin-top: 0; margin-bottom: 8px; font-size: 13px; color: #64748b; text-transform: uppercase; border-bottom: 1px solid #e2e8f0; }
  .info-box p { margin: 2px 0; } .bold { font-weight: bold; color: #0f172a; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
  th { background: #f8fafc; color: #475569; font-weight: 600; text-transform: uppercase; font-size: 11px; padding: 10px 8px; border-bottom: 2px solid #e2e8f0; }
  td { padding: 8px; border-bottom: 1px solid #f1f5f9; }
  .totals { margin-left: auto; width: 300px; } .totals div { display: flex; justify-content: space-between; padding: 4px 0; }
  .grand-total { font-size: 16px; font-weight: 800; color: #2563eb; border-top: 1px solid #e2e8f0; margin-top: 8px; padding-top: 8px !important; }
  .footer { margin-top: 50px; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; text-align: center; }
  .sign-box { height: 80px; border-bottom: 1px solid #333; margin-bottom: 10px; }
</style></head><body>
  <div class="header"><div><h1>${docTitle}</h1><p class="bold">${p.number}</p></div><div style="text-align: right"><p>Tanggal: <span class="bold">${pdfFmtDate(p.date)}</span></p><p>Status: <span class="bold" style="text-transform: uppercase">${p.status}</span></p></div></div>
  <div class="info-grid">
    <div class="info-box"><h3>DARI (SUPPLIER)</h3><p class="bold" style="font-size: 14px">${supplier.name || "-"}</p><p>${supplier.address || "-"}</p><p>Telp: ${supplier.phone || "-"}</p></div>
    <div class="info-box"><h3>DIKIRIM KE (CABANG)</h3><p class="bold" style="font-size: 14px">${destBranch.name || "Pusat"}</p><p>${destBranch.address || "-"}</p><p>Telp: ${destBranch.phone || "-"}</p></div>
  </div>
  <table><thead><tr><th style="width: 40px">No</th><th style="width: 100px">Kode</th><th>Nama Barang</th><th style="width: 60px; text-align: center">Qty</th><th style="width: 100px; text-align: right">Harga Satuan</th><th style="width: 120px; text-align: right">Total</th></tr></thead><tbody>${itemRows}</tbody></table>
  <div class="totals"><div><span>Subtotal</span><span>${pdfRp(p.subtotal)}</span></div>${p.discount > 0 ? `<div><span>Diskon</span><span>-${pdfRp(p.discount)}</span></div>` : ""}${p.tax > 0 ? `<div><span>Pajak (PPN)</span><span>${pdfRp(p.tax)}</span></div>` : ""}<div class="grand-total"><span>TOTAL</span><span>${pdfRp(p.total)}</span></div></div>
  <div style="margin-top: 30px"><p class="bold">Catatan:</p><p>${p.notes || "-"}</p></div>
  <div class="footer"><div><p>Dibuat Oleh,</p><div class="sign-box"></div><p>( ........................ )</p></div><div><p>Mengetahui,</p><div class="sign-box"></div><p>( ........................ )</p></div><div><p>Supplier,</p><div class="sign-box"></div><p>( ........................ )</p></div></div>
  <div style="margin-top: 40px; font-size: 10px; color: #94a3b8; text-align: center;">Dicetak pada ${new Date().toLocaleString("id-ID")} • iPos 5.0 Cloud System</div>
</body></html>`;
}

/**
 * exportPurchasePDF
 * Fungsi utama untuk fetch data dan buka jendela cetak
 */
async function exportPurchasePDF(id) {
  // 🔥 FIX: Buka window segera untuk menghindari Popup Blocker
  const win = window.open("", "_blank");
  if (!win) {
    alert("Pop-up diblokir browser. Izinkan pop-up untuk mencetak.");
    return;
  }
  
  win.document.write("<html><body><p style='font-family:sans-serif; text-align:center; margin-top:50px;'>Sedang memuat dokumen, mohon tunggu...</p></body></html>");

  try {
    if (typeof showLoading === 'function') showLoading("Menyiapkan dokumen...");
    const p = await api("GET", `/purchases/${id}`);
    if (typeof hideLoading === 'function') hideLoading();

    const html = generatePurchaseHTML(p);
    win.document.open();
    win.document.write(html);
    win.document.close();
    
    // Fokus dan print setelah render selesai
    setTimeout(() => { 
        win.focus();
        win.print(); 
    }, 500);
    
  } catch (e) {
    if (typeof hideLoading === 'function') hideLoading();
    win.close();
    alert("Gagal export PDF: " + e.message);
  }
}

// Global scope assignment
window.exportPurchasePDF = exportPurchasePDF;
window.printShiftReport = printShiftReport;
