import re

with open('frontend/purchases.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the header and add tabs
header_pattern = r'<div class="pg-header">.*?</div>'
new_header = """
<div class="pg-header" style="flex-direction: column; align-items: flex-start; gap: 16px;">
  <div>
    <h1>📥 Pembelian & Pesanan</h1>
    <p>Kelola pesanan, penerimaan barang, dan pembayaran.</p>
  </div>
  <div class="tabs" style="display: flex; gap: 8px; overflow-x: auto; width: 100%;">
    <button id="btnTabList" class="btn btn-primary" onclick="switchTab('list')">Tampilan Utama</button>
    <button id="btnTabPO" class="btn" style="background:var(--bg-color); border:1px solid var(--border-color); color:var(--text-main);" onclick="switchTab('po')">📝 Catat Pesanan</button>
    <button id="btnTabPurchase" class="btn" style="background:var(--bg-color); border:1px solid var(--border-color); color:var(--text-main);" onclick="switchTab('purchase')">📥 Catat Pembelian</button>
    <button id="btnTabPayment" class="btn" style="background:var(--bg-color); border:1px solid var(--border-color); color:var(--text-main);" onclick="switchTab('payment')">💸 Catat Pembayaran</button>
  </div>
</div>
"""
content = re.sub(header_pattern, new_header, content, flags=re.DOTALL)

# Add Supplier Filter to the filter bar
filter_bar_pattern = r'<div class="filter-bar">(.*?)</div>'
filter_bar_match = re.search(filter_bar_pattern, content, re.DOTALL)
if filter_bar_match:
    old_filter = filter_bar_match.group(1)
    new_filter = f"""
    {old_filter}
    <select id="fSupplierFilter" class="input-control" onchange="load()">
      <option value="">Semua Supplier</option>
    </select>
    """
    content = content.replace(filter_bar_match.group(0), f'<div class="filter-bar">{new_filter}</div>')

# Wrap the main table in a view-section
content = content.replace('<div class="filter-bar">', '<div id="viewList" class="view-section">\n<div class="filter-bar">')
content = content.replace('</main>', '</div>\n</main>')

# We will move the "mBuat" modal content out of the modal and into a view-section
modal_buat_pattern = r'<div class="modal-overlay" id="mBuat">.*?<div class="modal-box wide" id="modalBuatContent">(.*?)</div>\s*</div>'
modal_buat_match = re.search(modal_buat_pattern, content, re.DOTALL)
if modal_buat_match:
    form_html = modal_buat_match.group(1)
    
    # modify header in form
    form_html = form_html.replace('<h2>📥 Catat Pembelian Baru</h2>', '<h2 id="formTitle">📥 Catat Pembelian</h2>')
    form_html = form_html.replace('<button class="btn-x" onclick="closeModal(\'mBuat\')">×</button>', '')
    
    # replace purchase grid header to include Qty Diterima
    header_grid = """
              <div class="purchase-header">
                <span>Barang</span>
                <span style="text-align: center" id="hdrQtyDipesan">Qty (Pesan)</span>
                <span style="text-align: center; display:none;" id="hdrQtyDiterima">Qty (Terima)</span>
                <span style="text-align: right">Harga Beli</span>
                <span style="text-align: center">Margin (%)</span>
                <span style="text-align: right">Harga Jual</span>
                <span style="padding-left: 20px">Diskon Bertingkat</span>
                <span style="text-align: right">Harga/Qty</span>
                <span style="text-align: right">Total</span>
                <span></span>
              </div>
"""
    form_html = re.sub(r'<div class="purchase-header">.*?</div>', header_grid, form_html, flags=re.DOTALL)
    
    # Update buttons
    form_html = form_html.replace('onclick="closeModal(\'mBuat\')"', 'onclick="switchTab(\'list\')"')
    form_html = form_html.replace('Simpan Pembelian', 'Simpan')
    
    view_form = f'<div id="viewForm" class="view-section" style="display:none; padding-top: 16px;">\n{form_html}\n</div>'
    
    content = content.replace(modal_buat_match.group(0), view_form)


# We will move the "mBayar" modal content out of the modal and into a view-section
modal_bayar_pattern = r'<div class="modal-overlay" id="mBayar">.*?<div class="modal-box">(.*?)</div>\s*</div>'
modal_bayar_match = re.search(modal_bayar_pattern, content, re.DOTALL)
if modal_bayar_match:
    bayar_html = modal_bayar_match.group(1)
    bayar_html = bayar_html.replace('<button class="btn-x" onclick="closeModal(\'mBayar\')">×</button>', '')
    bayar_html = bayar_html.replace('onclick="closeModal(\'mBayar\')"', 'onclick="switchTab(\'list\')"')
    bayar_html = bayar_html.replace('doBayar()', 'doBayarFull()')
    
    # Add a dropdown to select Unpaid Purchase
    select_invoice = """
        <div class="input-group" style="margin-bottom: 16px">
          <label style="margin-bottom: 6px">Pilih Faktur Pembelian (Belum Lunas) *</label>
          <select id="bayarPurchaseSelect" class="input-control" onchange="onSelectFakturBayar()">
            <option value="">-- Pilih Faktur --</option>
          </select>
        </div>
    """
    bayar_html = bayar_html.replace('<input type="hidden" id="bayarId" />', f'<input type="hidden" id="bayarId" />\n{select_invoice}')
    
    view_payment = f'<div id="viewPayment" class="view-section" style="display:none; padding-top: 16px; max-width: 600px; margin: 0 auto;">\n{bayar_html}\n</div>'
    
    content = content.replace(modal_bayar_match.group(0), view_payment)

# Add Javascript logic for Tabs and new Qty Diterima
js_additions = """
      let currentTab = 'list';
      let currentFormMode = 'purchase'; // 'po' or 'purchase'
      let draftId = null;

      function switchTab(tab) {
        currentTab = tab;
        document.querySelectorAll('.view-section').forEach(el => el.style.display = 'none');
        document.querySelectorAll('.tabs .btn').forEach(btn => {
          btn.style.background = 'var(--bg-color)';
          btn.style.borderColor = 'var(--border-color)';
          btn.style.color = 'var(--text-main)';
        });
        
        if (tab === 'list') {
          document.getElementById('viewList').style.display = 'block';
          let btn = document.getElementById('btnTabList');
          btn.style.background = 'var(--primary)';
          btn.style.color = '#fff';
          load();
        } else if (tab === 'po') {
          document.getElementById('viewForm').style.display = 'block';
          let btn = document.getElementById('btnTabPO');
          btn.style.background = 'var(--primary)';
          btn.style.color = '#fff';
          currentFormMode = 'po';
          document.getElementById('formTitle').textContent = '📝 Catat Pesanan (Draft)';
          document.getElementById('hdrQtyDiterima').style.display = 'none';
          openForm();
        } else if (tab === 'purchase') {
          document.getElementById('viewForm').style.display = 'block';
          let btn = document.getElementById('btnTabPurchase');
          btn.style.background = 'var(--primary)';
          btn.style.color = '#fff';
          currentFormMode = 'purchase';
          document.getElementById('formTitle').textContent = '📥 Catat Pembelian';
          document.getElementById('hdrQtyDiterima').style.display = 'block';
          openForm();
        } else if (tab === 'payment') {
          document.getElementById('viewPayment').style.display = 'block';
          let btn = document.getElementById('btnTabPayment');
          btn.style.background = 'var(--primary)';
          btn.style.color = '#fff';
          openPaymentTab();
        }
      }

      async function openForm(existingDraft = null) {
        document.getElementById("barisContainer").innerHTML = "";
        barisIdx = 0;
        currentSupplierItems = [];
        document.getElementById("bDate").value = today();
        document.getElementById("bDisc").value = 0;
        document.getElementById("bTax").value = 0;
        document.getElementById("bNotes").value = "";
        document.getElementById("totalLabel").textContent = "Rp 0";
        draftId = existingDraft ? existingDraft.id : null;
        
        allSuppliers = await api("GET", "/suppliers/?limit=5000&active_only=true");
        allSuppliers.sort((a, b) => (a.name || "").localeCompare(b.name || "", "id"));
        createSupplierCombobox("supplierCombobox");
        
        if (existingDraft) {
             document.querySelector("#supplierCombobox .combobox-input").value = existingDraft.supplier?.name || "";
             document.querySelector("#supplierCombobox .combobox-value").value = existingDraft.supplier_id;
             await loadSupplierItems(existingDraft.supplier_id);
             document.getElementById("bDate").value = existingDraft.date;
             
             document.getElementById("bNotes").value = existingDraft.notes || "";
             
             for (const it of existingDraft.items) {
                 addBaris(it);
             }
             document.getElementById("bDisc").value = 0; 
             document.getElementById("bTax").value = 0; 
        } else {
             addBaris();
        }
      }

      async function openPaymentTab() {
        document.getElementById('bayarId').value = '';
        document.getElementById('bayarInfo').innerHTML = '';
        document.getElementById('bayarRemaining').value = '0';
        document.getElementById('bayarMethod').value = 'cash';
        
        const purchases = await api("GET", "/purchases/?status=unpaid&limit=500");
        const partial = await api("GET", "/purchases/?status=partial&limit=500");
        const allUnpaid = [...purchases, ...partial];
        
        const sel = document.getElementById('bayarPurchaseSelect');
        sel.innerHTML = '<option value="">-- Pilih Faktur --</option>' + allUnpaid.map(p => 
           `<option value="${p.id}" data-total="${p.total}" data-paid="${p.paid}" data-num="${p.number}">[${p.number}] ${p.supplier?.name || '-'} - Sisa Rp ${fmtRp(p.total - p.paid)}</option>`
        ).join('');
        
        await loadLiquidBalances();
        applyBayarMethod(true);
      }

      function onSelectFakturBayar() {
          const sel = document.getElementById('bayarPurchaseSelect');
          const opt = sel.options[sel.selectedIndex];
          if (!opt.value) {
              document.getElementById('bayarInfo').innerHTML = '';
              return;
          }
          const id = opt.value;
          const total = parseFloat(opt.getAttribute('data-total'));
          const paid = parseFloat(opt.getAttribute('data-paid'));
          const sisa = total - paid;
          
          document.getElementById("bayarId").value = id;
          document.getElementById("bayarRemaining").value = sisa;
          document.getElementById("bayarInfo").innerHTML = 
            `<div style="display:flex;justify-content:space-between;margin-bottom:6px"><span>Total Tagihan</span><b>${fmtRp(total)}</b></div><div style="display:flex;justify-content:space-between;margin-bottom:6px"><span>Sudah Dibayar</span><b style="color:#10b981">${fmtRp(paid)}</b></div><div style="display:flex;justify-content:space-between;font-size:18px;font-weight:800;border-top:1px solid var(--border-color);padding-top:8px"><span>Sisa Hutang</span><b style="color:#ef4444">${fmtRp(sisa)}</b></div>`;
          
          applyBayarMethod(true);
      }
"""

content = content.replace('function addBaris() {', 'function addBaris(existingItem = null) {')
content = content.replace('class="input-control qty-input" value="1"', 'class="input-control qty-input" value="${existingItem ? existingItem.qty : 1}"')

# Inject received qty
qty_diterima_html = """
        const qtyTerimaWrapper = document.createElement("div");
        qtyTerimaWrapper.className = "input-wrapper qty-terima-wrapper";
        qtyTerimaWrapper.style.display = currentFormMode === 'purchase' ? 'block' : 'none';
        qtyTerimaWrapper.innerHTML = `<span class="field-label">Diterima</span><input type="text" inputmode="numeric" class="input-control qty-terima-input" value="${existingItem ? existingItem.qty : 1}" min="0" style="margin:0;padding:10px 12px;font-size:14px;text-align:center; background:#e0f2fe; border: 1px solid #7dd3fc;">`;
        const qtyTerimaInp = qtyTerimaWrapper.querySelector("input");
        qtyTerimaInp.oninput = () => {
          const raw = parseNum(qtyTerimaInp.value), pos = qtyTerimaInp.selectionStart, oldLen = qtyTerimaInp.value.length;
          qtyTerimaInp.value = formatNum(raw);
          qtyTerimaInp.setSelectionRange(pos + (qtyTerimaInp.value.length - oldLen), pos + (qtyTerimaInp.value.length - oldLen));
          hitungTotal();
          updateScrollLogic();
        };
"""
content = content.replace('const hargaWrapper = document.createElement("div");', qty_diterima_html + '\n        const hargaWrapper = document.createElement("div");')
content = content.replace('container.appendChild(qtyWrapper);', 'container.appendChild(qtyWrapper);\n        container.appendChild(qtyTerimaWrapper);')

# Also sync received qty when qty is typed
sync_qty = """
          qtyInp.setSelectionRange(
            pos + (qtyInp.value.length - oldLen),
            pos + (qtyInp.value.length - oldLen),
          );
          if (currentFormMode === 'purchase') {
              const qti = row.querySelector(".qty-terima-input");
              if (qti) qti.value = qtyInp.value;
          }
"""
content = content.replace('qtyInp.setSelectionRange(\n            pos + (qtyInp.value.length - oldLen),\n            pos + (qtyInp.value.length - oldLen),\n          );', sync_qty)


# Modify hitungTotal to use qtyTerima if in purchase mode
hitung_total_mod = """
            if (qtyInp && hargaInp) {
              let qtyDipesan = parseNum(qtyInp.value) || 0;
              let qtyTerima = row.querySelector(".qty-terima-input") ? parseNum(row.querySelector(".qty-terima-input").value) : qtyDipesan;
              let qtyCalc = currentFormMode === 'purchase' ? qtyTerima : qtyDipesan;
              
              let hargaAwal = parseNum(hargaInp.value) || 0,
"""
content = content.replace('if (qtyInp && hargaInp) {\n              let qty = parseNum(qtyInp.value) || 0,\n                hargaAwal = parseNum(hargaInp.value) || 0,', hitung_total_mod)
content = content.replace('const barisTotal = hargaSekarang * qty;', 'const barisTotal = hargaSekarang * qtyCalc;')


# Modify simpanPembelian logic
simpan_pembelian_mod = """
      async function simpanPembelian() {
        const supplierId = document.querySelector("#supplierCombobox .combobox-value")?.value;
        if (!supplierId) return showToast("Pilih supplier dulu", "error");

        const rows = [];
        let priceChanges = [];
        let hasMissingItems = false;
        let missingItemsList = [];

        document.querySelectorAll("#barisContainer > .purchase-row").forEach((row) => {
            const hiddenItem = row.querySelector(".combobox-value");
            if (!hiddenItem || !hiddenItem.value) return;

            const itemId = parseInt(hiddenItem.value);
            const qtyDipesan = parseNum(row.querySelector(".qty-input").value) || 0;
            const qtyDiterima = row.querySelector(".qty-terima-input") ? parseNum(row.querySelector(".qty-terima-input").value) : qtyDipesan;
            
            const qtyFinal = currentFormMode === 'purchase' ? qtyDiterima : qtyDipesan;
            
            if (currentFormMode === 'purchase' && qtyDiterima < qtyDipesan) {
                hasMissingItems = true;
                missingItemsList.push({
                    item_id: itemId,
                    qty: qtyDipesan - qtyDiterima, // Sisa yang belum datang
                    buy_price: parseNum(row.querySelector(".baris-harga").value) || 0,
                    sell_price: parseNum(row.querySelector(".baris-jual").value) || 0,
                    profit_margin: parseFloat(row.querySelector(".baris-margin").value) || 0,
                });
            }

            if (qtyFinal <= 0 && currentFormMode === 'purchase') return; // Skip jika 0 untuk pembelian

            const buy_price = parseNum(row.querySelector(".baris-harga").value) || 0,
              sell_price = parseNum(row.querySelector(".baris-jual").value) || 0,
              profit_margin = parseFloat(row.querySelector(".baris-margin").value) || 0;

            const originalItem = currentSupplierItems.find((i) => i.id === itemId);
            if (originalItem && originalItem.buy_price !== buy_price) {
              priceChanges.push({name: originalItem.name, old: originalItem.buy_price, new: buy_price});
            }

            let hargaSekarang = buy_price;
            row.querySelectorAll(".baris-disc").forEach((inp) => {
              const d = parseFloat(inp.value) || 0;
              hargaSekarang = hargaSekarang * (1 - d / 100);
            });
            let ekuivalenDiskon = 0;
            if (buy_price > 0) ekuivalenDiskon = ((buy_price - hargaSekarang) / buy_price) * 100;
            
            rows.push({
              item_id: itemId,
              qty: qtyFinal,
              buy_price,
              sell_price,
              profit_margin,
              discount: parseFloat(ekuivalenDiskon.toFixed(4)),
            });
        });
        
        if (!rows.length) return showToast("Tambahkan minimal 1 barang", "error");

        if (priceChanges.length > 0) {
          let msg = "Deteksi perubahan harga beli pada barang berikut:\\n";
          priceChanges.forEach((c) => { msg += `- ${c.name}: ${fmtRp(c.old)} ➜ ${fmtRp(c.new)}\\n`; });
          msg += "\\nSimpan dengan harga baru ini?";
          if (!(await showConfirm(msg))) return;
        }
        
        let createNewPO = false;
        if (hasMissingItems) {
            if (await showConfirm("Ada barang yang kurang dari pesanan (Qty Diterima < Qty Dipesan).\\n\\nApakah Anda ingin membuat DRAFT PESANAN BARU otomatis untuk sisa barang yang kurang?")) {
                createNewPO = true;
            }
        }

        try {
          showLoading("Menyimpan...");
          
          const payload = {
            date: document.getElementById("bDate").value,
            supplier_id: parseInt(supplierId),
            discount: parseFloat(document.getElementById("bDisc").value) || 0,
            tax: parseFloat(document.getElementById("bTax").value) || 0,
            notes: document.getElementById("bNotes").value || null,
            items: rows,
            paid: 0,
            status: currentFormMode === 'po' ? "draft" : "unpaid"
          };

          if (draftId) {
              await api("PUT", `/purchases/${draftId}`, payload);
          } else {
              await api("POST", "/purchases/", payload);
          }
          
          if (createNewPO && missingItemsList.length > 0) {
              const poPayload = {
                  date: document.getElementById("bDate").value,
                  supplier_id: parseInt(supplierId),
                  discount: 0, tax: 0,
                  notes: "Sisa pesanan dari penerimaan sebelumnya",
                  items: missingItemsList,
                  paid: 0,
                  status: "draft"
              };
              const newDraft = await api("POST", "/purchases/", poPayload);
              hideLoading();
              showToast("Pembelian berhasil disimpan & Draft Sisa Pesanan dibuat!");
              switchTab('po');
              openForm(newDraft);
              return;
          }

          hideLoading();
          showToast(currentFormMode === 'po' ? "Draft Pesanan disimpan ✓" : "Pembelian disimpan ✓");
          switchTab('list');
        } catch (ex) {
          hideLoading();
          showToast(ex.message, "error");
        }
      }
"""
content = re.sub(r'async function simpanPembelian\(\) \{.*?catch \(ex\) \{\s*hideLoading\(\);\s*showToast\(ex\.message, "error"\);\s*\}\s*\}', simpan_pembelian_mod, content, flags=re.DOTALL)


# Supplier filter population and handling
sup_filter_js = """
        allSuppliers = await api("GET", "/suppliers/?limit=5000&active_only=true");
        const fSup = document.getElementById("fSupplierFilter");
        if (fSup) {
            fSup.innerHTML = '<option value="">Semua Supplier</option>' + allSuppliers.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
        }
"""
content = content.replace('document.getElementById("fEnd").value = today();', 'document.getElementById("fEnd").value = today();\n' + sup_filter_js)
content = content.replace('const st = document.getElementById("fStatus").value;', 'const st = document.getElementById("fStatus").value;\n        const supId = document.getElementById("fSupplierFilter")?.value;')
content = content.replace('if (st) url += `&status=${st}`;', 'if (st) url += `&status=${st}`;\n        if (supId) url += `&supplier_id=${supId}`;')

# Change "Detail" buttons inside the table row
# Wait, `p.status !== "paid"` inside the `load()` function:
detail_mod = """
          <td style="white-space:nowrap">
              ${p.status === "draft" ? `<button class="bsm bp" style="padding:4px 8px;border-radius:6px;font-size:12px;cursor:pointer;background:var(--primary);color:#fff;border:none;" onclick="loadDraft(${p.id})">Proses</button>` : ''}
              <button class="bsm bo" style="padding:4px 8px;border-radius:6px;font-size:12px;cursor:pointer;background:transparent;border:1px solid #f59e0b;color:#f59e0b;" onclick="showDetail(${p.id})">Detail</button>
          </td>
"""
content = re.sub(r'<td style="white-space:nowrap">.*?</td>', detail_mod, content, flags=re.DOTALL)

# Add loadDraft function and doBayarFull for the payment tab
load_draft_fn = """
      async function loadDraft(id) {
          try {
              const draft = await api("GET", `/purchases/${id}`);
              switchTab('purchase');
              await openForm(draft);
          } catch(e) { showToast(e.message, "error"); }
      }
      
      async function doBayarFull() {
          await doBayar();
          switchTab('list');
      }
"""
content = content.replace('function fmtRp(n) {', load_draft_fn + '\n      function fmtRp(n) {')

# Inject css for tabs
css_injection = """
      .tabs .btn {
          white-space: nowrap;
          border-radius: 8px;
          font-weight: 600;
          font-size: 14px;
          padding: 8px 16px;
      }
      .view-section {
          animation: fadeIn 0.2s ease;
      }
"""
content = content.replace('/* --- TABEL RESPONSIF & GRID --- */', css_injection + '\n      /* --- TABEL RESPONSIF & GRID --- */')

# Inject script logic variables
content = content.replace('requireAuth();', 'requireAuth();\n' + js_additions)

# Fix openBuatModal call
content = content.replace('openBuatModal()', 'switchTab(\'purchase\')')

# Ensure we remove desktop-add-btn and mobile-add-btn display toggles that depend on old class names
content = content.replace("container.querySelector('.baris-harga').value = formatNum(hb);", "const bh = container.querySelector('.baris-harga'); if(bh) bh.value = formatNum(hb);")

with open('frontend/purchases.html', 'w', encoding='utf-8') as f:
    f.write(content)
