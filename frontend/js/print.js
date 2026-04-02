/**
 * iPos 5.0 — Thermal Receipt Printer
 * Compatible with: any thermal printer set as default printer in Windows
 * Paper width: 58mm or 80mm (configurable)
 *
 * Hardware yang didukung:
 * - Thermal printer USB (Epson TM, Bixolon, POS-80, dll)
 * - Thermal printer Bluetooth
 * - Cash drawer via printer kick (ESC/POS)
 * - Barcode scanner (handled via keyboard input di pos.html)
 *
 * Cara setup thermal printer di Windows:
 * 1. Install driver thermal printer, set sebagai printer default
 * 2. Di printer settings: paper size = 80mm x Roll, tanpa margin
 * 3. Print dari iPos → Ctrl+P atau tombol cetak
 */

function getPrintSettings() {
  try {
    return JSON.parse(localStorage.getItem("ipos_print_settings") || "{}");
  } catch (e) {
    return {};
  }
}

function printReceipt(sale) {
  const s = getPrintSettings();
  const STORE_NAME   = s.storeName   || "iPos 5.0";
  const STORE_ADDR   = s.storeAddr   || "";
  const STORE_PHONE  = s.storePhone  || "";
  const STORE_FOOTER = s.storeFooter || "Terima kasih telah berbelanja!";
  const PAPER_WIDTH  = s.paperWidth  || "80mm";

  const items = sale.items || [];
  const saleDate = new Date((sale.date || new Date().toISOString().slice(0,10)) + "T00:00:00");
  const dateStr = saleDate.toLocaleDateString("id-ID", {
    day: "2-digit", month: "short", year: "numeric"
  });
  const timeStr = new Date().toLocaleTimeString("id-ID", {
    hour: "2-digit", minute: "2-digit"
  });

  // Format rupiah tanpa desimal
  const rp = (n) => "Rp " + Math.round(n || 0).toLocaleString("id-ID");

  const itemRows = items.map(i => `
    <tr>
      <td class="item-name">${i.item?.name || i.name || "Item"}</td>
      <td class="item-qty">${i.qty}</td>
      <td class="item-price">${rp(i.sell_price || i.price || 0)}</td>
    </tr>
    <tr>
      <td colspan="2"></td>
      <td class="item-price" style="border-bottom:1px dashed #ccc">${rp(i.total)}</td>
    </tr>`).join("");

  const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Struk ${sale.number || ""}</title>
<style>
  @page { margin: 0; size: ${PAPER_WIDTH} auto; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Courier New', Courier, monospace;
    font-size: 11px;
    width: ${PAPER_WIDTH === "58mm" ? "54mm" : "76mm"};
    padding: 3mm;
    color: #000;
  }
  .center  { text-align: center; }
  .right   { text-align: right; }
  .bold    { font-weight: bold; }
  .big     { font-size: 14px; font-weight: bold; }
  .sep     { border-top: 1px dashed #000; margin: 4px 0; }
  .sep-solid { border-top: 2px solid #000; margin: 4px 0; }
  table    { width: 100%; border-collapse: collapse; }
  td       { padding: 1px 0; vertical-align: top; }
  .item-name  { width: 55%; }
  .item-qty   { width: 10%; text-align: right; }
  .item-price { width: 35%; text-align: right; }
  .grand-total { font-size: 13px; font-weight: bold; }
  @media print {
    body { -webkit-print-color-adjust: exact; }
  }
</style>
</head>
<body>
  <div class="center">
    <div class="big">${STORE_NAME}</div>
    ${STORE_ADDR ? `<div>${STORE_ADDR}</div>` : ""}
    ${STORE_PHONE ? `<div>Telp: ${STORE_PHONE}</div>` : ""}
  </div>

  <div class="sep"></div>

  <table>
    <tr><td>No.</td><td class="right">${sale.number || "-"}</td></tr>
    <tr><td>Tgl</td><td class="right">${dateStr} ${timeStr}</td></tr>
    <tr><td>Kasir</td><td class="right">${sale.cashier || "Kasir"}</td></tr>
    ${sale.customer?.name ? `<tr><td>Pelanggan</td><td class="right">${sale.customer.name}</td></tr>` : ""}
  </table>

  <div class="sep"></div>

  <table>
    <tr>
      <td class="item-name bold">Barang</td>
      <td class="item-qty bold">Qty</td>
      <td class="item-price bold">Harga</td>
    </tr>
  </table>
  <div class="sep"></div>
  <table>${itemRows}</table>
  <div class="sep"></div>

  <table>
    <tr><td>Subtotal</td><td class="right">${rp(sale.subtotal)}</td></tr>
    ${sale.discount > 0 ? `<tr><td>Diskon</td><td class="right">-${rp(sale.discount)}</td></tr>` : ""}
    ${sale.tax > 0 ? `<tr><td>PPN</td><td class="right">${rp(sale.tax)}</td></tr>` : ""}
  </table>
  <div class="sep-solid"></div>
  <table>
    <tr class="grand-total">
      <td>TOTAL</td>
      <td class="right">${rp(sale.total)}</td>
    </tr>
    <tr>
      <td>Bayar (${(sale.payment_method || "cash").toUpperCase()})</td>
      <td class="right">${rp(sale.paid)}</td>
    </tr>
    <tr class="bold">
      <td>Kembalian</td>
      <td class="right">${rp(sale.change)}</td>
    </tr>
  </table>

  <div class="sep"></div>
  <div class="center" style="font-size:10px">
    ${STORE_FOOTER}
    <br>
    <span style="font-size:9px">Powered by iPos 5.0</span>
  </div>
</body>
</html>`;

  const win = window.open("", "_blank",
    `width=350,height=600,toolbar=0,menubar=0,scrollbars=1`);
  if (!win) {
    alert("Pop-up diblokir browser. Izinkan pop-up untuk mencetak struk.");
    return;
  }
  win.document.write(html);
  win.document.close();
  win.focus();
  setTimeout(() => {
    win.print();
    setTimeout(() => win.close(), 1500);
  }, 300);
}


function printShiftReport(shift) {
  const s = getPrintSettings();
  const STORE_NAME = s.storeName || "iPos 5.0";

  const openTime  = shift.opened_at ? new Date(shift.opened_at).toLocaleString("id-ID") : "-";
  const closeTime = shift.closed_at ? new Date(shift.closed_at).toLocaleString("id-ID") : "-";
  const rp = (n) => "Rp " + Math.round(n || 0).toLocaleString("id-ID");

  const html = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Laporan Shift</title>
<style>
  @page { margin: 0; size: 80mm auto; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Courier New', monospace; font-size: 11px; width: 76mm; padding: 3mm; }
  .center { text-align: center; }
  .bold { font-weight: bold; }
  .big { font-size: 14px; font-weight: bold; }
  .sep { border-top: 1px dashed #000; margin: 4px 0; }
  table { width: 100%; }
  td { padding: 1px 0; vertical-align: top; }
  .right { text-align: right; }
  .diff-neg { font-weight: bold; }
</style>
</head>
<body>
  <div class="center">
    <div class="big">${STORE_NAME}</div>
    <div>LAPORAN SHIFT KASIR</div>
  </div>
  <div class="sep"></div>
  <table>
    <tr><td>Kasir</td><td class="right">${shift.username || "-"}</td></tr>
    <tr><td>Buka</td><td class="right">${openTime}</td></tr>
    <tr><td>Tutup</td><td class="right">${closeTime}</td></tr>
  </table>
  <div class="sep"></div>
  <table>
    <tr><td>Modal Awal</td><td class="right">${rp(shift.opening_cash)}</td></tr>
    <tr><td>Total Penjualan</td><td class="right">${rp(shift.total_sales)}</td></tr>
    <tr><td>Jml Transaksi</td><td class="right">${shift.total_transactions || 0}</td></tr>
  </table>
  <div class="sep"></div>
  <table>
    <tr class="bold"><td>Kas Sistem</td><td class="right">${rp(shift.system_cash)}</td></tr>
    <tr class="bold"><td>Kas Aktual</td><td class="right">${rp(shift.closing_cash)}</td></tr>
    <tr class="bold ${(shift.difference || 0) < 0 ? "diff-neg" : ""}">
      <td>Selisih</td>
      <td class="right">${(shift.difference || 0) >= 0 ? "+" : ""}${rp(shift.difference)}</td>
    </tr>
  </table>
  <div class="sep"></div>
  <div class="center" style="font-size:10px">iPos 5.0</div>
</body>
</html>`;

  const win = window.open("", "_blank", "width=350,height=600");
  if (!win) { alert("Pop-up diblokir browser."); return; }
  win.document.write(html);
  win.document.close();
  win.focus();
  setTimeout(() => { win.print(); setTimeout(() => win.close(), 1500); }, 300);
}
