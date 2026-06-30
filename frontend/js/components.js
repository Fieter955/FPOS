/**
 * FPOS Shared UI Components
 * --------------------------
 * reusable UI logic for standard components
 */

/**
 * createPremiumCombo
 * Creates a premium searchable dropdown with fixed positioning and high performance.
 */
function createPremiumCombo(container, data, config = {}) {
  const target =
    typeof container === "string"
      ? document.getElementById(container)
      : container;
  const {
    valField = "id",
    labField = "name",
    placeholder = "Cari...",
    isItem = false,
    onSelect = null,
  } = config;

  target.innerHTML = `
        <div class="combobox-container">
            <input type="text" class="combobox-input" placeholder="${placeholder}" autocomplete="off" />
            <div class="premium-dropdown"></div>
            <input type="hidden" class="combobox-value" />
        </div>
    `;

  const input = target.querySelector(".combobox-input");
  const dropdown = target.querySelector(".premium-dropdown");
  const hidden = target.querySelector(".combobox-value");
  let currentData = data;

  const positionDropdown = () => {
    const rect = input.getBoundingClientRect();
    const lebarLayar = document.documentElement.clientWidth;
    const tinggiLayar = document.documentElement.clientHeight;
    // Lebar dropdown tidak boleh melebihi layar (penting di HP)
    const lebar = Math.min(rect.width, lebarLayar - 16);
    // Geser kiri agar tepi kanan dropdown tetap di dalam layar
    let kiri = Math.max(8, Math.min(rect.left, lebarLayar - lebar - 8));
    dropdown.style.setProperty("width", `${lebar}px`, "important");
    dropdown.style.setProperty("left", `${kiri}px`, "important");
    // Kalau ruang di bawah input sempit & ruang di atas lebih lega → buka ke ATAS
    const ruangBawah = tinggiLayar - rect.bottom;
    if (ruangBawah < 260 && rect.top > ruangBawah) {
      dropdown.style.setProperty("bottom", `${tinggiLayar - rect.top + 2}px`, "important");
      dropdown.style.setProperty("top", "auto", "important");
    } else {
      dropdown.style.setProperty("top", `${rect.bottom + 2}px`, "important");
      dropdown.style.setProperty("bottom", "auto", "important");
    }
  };

  const render = (q = "") => {
    const search = q.toLowerCase().trim();
    const filtered = currentData.filter(
      (d) =>
        (d[labField] || "").toLowerCase().includes(search) ||
        (d.barcode && String(d.barcode).toLowerCase().includes(search)) ||
        (d.code && d.code.toLowerCase().includes(search)),
    );

    if (filtered.length === 0) {
      dropdown.innerHTML = `<div class="premium-dropdown-empty">Tidak ditemukan...</div>`;
    } else {
      dropdown.innerHTML = filtered
        .slice(0, 50)
        .map(
          (d) => `
                <div class="premium-dropdown-item" data-id="${d[valField]}" data-label="${d[labField]}">
                    <span>${d[labField]} ${isItem ? `<small>[${d.code}]</small>` : ""}</span>
                </div>
            `,
        )
        .join("");
    }

    dropdown.classList.add("show");
    positionDropdown();

    dropdown
      .querySelectorAll(".premium-dropdown-item[data-id]")
      .forEach((el) => {
        el.onclick = (e) => {
          e.stopPropagation();
          hidden.value = el.dataset.id;
          input.value = el.dataset.label;
          dropdown.classList.remove("show");
          if (onSelect) {
            const obj = currentData.find((d) => d[valField] == el.dataset.id);
            onSelect(obj);
          }
        };
      });
  };

  input.onfocus = () => render(input.value);
  input.oninput = () => render(input.value);

  const handleOutsideClick = (e) => {
    if (!target.contains(e.target)) {
      dropdown.classList.remove("show");
    }
  };
  document.addEventListener("click", handleOutsideClick);

  const methods = {
    updateData: (newData) => {
      currentData = newData;
    },
    set: (v, l) => {
      hidden.value = v;
      input.value = l;
    },
    disable: () => {
      input.disabled = true;
      input.style.opacity = "0.7";
      input.style.cursor = "not-allowed";
      target.style.pointerEvents = "none";
    },
    enable: () => {
      input.disabled = false;
      input.style.opacity = "1";
      input.style.cursor = "text";
      target.style.pointerEvents = "auto";
    },
    val: () => hidden.value,
    clear: () => {
      hidden.value = "";
      input.value = "";
    },
    destroy: () => {
      document.removeEventListener("click", handleOutsideClick);
    },
  };

  target._combo = methods;
  return methods;
}

/**
 * createPurchaseGrid (Unified Grid - Use for PO and Fulfillment)
 */
function createPurchaseGrid(container, config = {}) {
  const target =
    typeof container === "string"
      ? document.getElementById(container)
      : container;
  const {
    initialItems = [],
    itemDataSource = [],
    onChange = null,
    isFulfillment = false,
    isBranchRequest = false,
    showSupplierColumn = false,
    allowNameEdit = false,
    readonlySelector = false,
  } = config;

  let barisIdx = 0;
  let currentData = typeof itemDataSource === "function" ? [] : itemDataSource;
  let supplierList = [];

  // Header column configuration
  let columns;
  if (showSupplierColumn) {
    columns = "2.5fr 120px 2.5fr 40px";
  } else if (isBranchRequest) {
    columns = "2.5fr 120px 40px";
  } else {
    columns = isFulfillment
      ? "2.5fr 60px 60px 0.7fr 60px 0.7fr var(--disc-col-width, 1fr) 110px 30px"
      : "2.5fr 60px 0.7fr 60px 0.7fr var(--disc-col-width, 1fr) 110px 30px";
  }

  target.innerHTML = `
        <div class="purchase-grid-container">
            <div class="purchase-grid-header" style="display:grid; grid-template-columns: ${columns}; gap:10px; padding:10px; background:var(--bg-color); font-weight:700; font-size:12px; border-radius:8px 8px 0 0">
                <div>Nama Barang</div>
                <div style="text-align:center">Pesan</div>
                ${
                  showSupplierColumn
                    ? "<div>Pilih Supplier</div>"
                    : isBranchRequest
                      ? ""
                      : `
                ${isFulfillment ? '<div style="text-align:center">Terima</div>' : ""}
                <div>Harga Beli</div>
                <div style="text-align:center">Margin (%)</div>
                <div>Harga Jual</div>
                <div style="text-align:center">Diskon (%)</div>
                <div style="text-align:right">Total</div>
                `
                }
                <div></div>
            </div>
            <div class="purchase-grid-body" style="border:1px solid var(--border-color); border-top:none; border-radius:0 0 8px 8px; min-height:100px"></div>
        </div>
    `;

  const body = target.querySelector(".purchase-grid-body");
  const fmtRp = (n) =>
    new Intl.NumberFormat("id-ID", {
      style: "currency",
      currency: "IDR",
      minimumFractionDigits: 0,
    }).format(n || 0);
  const toAngka = (s) =>
    parseFloat(String(s).replace(/\./g, "").replace(",", ".")) || 0;
  const toRibuan = (n) => (n || 0).toLocaleString("id-ID");

  const addRow = (item = null) => {
    const id = barisIdx++;
    const row = document.createElement("div");
    row.className = "purchase-grid-row";
    row.id = `pg-row-${id}`;
    row.style = `display:grid; grid-template-columns: ${columns}; gap:10px; padding:8px 10px; border-bottom:1px solid var(--border-color); align-items:center`;

    // Default kolom "Terima" (qty_received) mengikuti kolom "Pesan" (qty_ordered)
    // pada mode fulfillment. Hanya pakai nilai tersimpan bila benar-benar ada
    // penerimaan parsial (> 0); nilai 0 dianggap belum diisi → samakan dengan Pesan.
    const qtyOrdered = item?.qty_ordered || item?.qty || 1;
    const qtyReceived = item?.qty_received
      ? item.qty_received
      : isFulfillment
        ? qtyOrdered
        : 0;

    row.innerHTML = `
            <div class="item-col-container">
                <div class="item-selector" id="pg-combo-${id}" style="${readonlySelector ? "display:none" : ""}"></div>
             
                ${allowNameEdit ? `<input type="text" class="input-control pg-name-edit" value="${item?.name || ""}" placeholder="Nama barang (bisa diubah)..." style="font-size:11px; margin-top:4px; border-color:var(--primary)" />` : ""}
            </div>
            <input type="number" class="combobox-input pg-ordered" value="${qtyOrdered}" style="text-align:center" />
            ${
              showSupplierColumn
                ? '<div id="pg-supp-' + id + '"></div>'
                : !isBranchRequest
                  ? `
                ${isFulfillment ? `<input type="number" class="combobox-input pg-received" value="${qtyReceived}" style="text-align:center; border-color:var(--primary)" />` : ""}
                <input type="text" class="combobox-input pg-beli" value="${toRibuan(item?.buy_price || 0)}" style="text-align:left" />
                <input type="number" step="0.01" class="combobox-input pg-margin" value="${item?.profit_margin || 0}" style="text-align:center" />
                <input type="text" class="combobox-input pg-jual" value="${toRibuan(item?.sell_price || 0)}" style="text-align:left" />
                <div class="disc-group">
                    <input type="number" class="combobox-input pg-disc" value="${item?.disc1 || 0}" placeholder="0" style="text-align:center" />
                    ${item?.disc2 ? `<input type="number" class="combobox-input pg-disc" value="${item.disc2}" placeholder="0" style="text-align:center" />` : ""}
                    <button class="btn-plus-disc" title="Diskon Bertingkat">+</button>
                </div>
                <div class="purchase-grid-netto">Rp 0</div>
            `
                  : ""
            }
            ${readonlySelector ? "<div></div>" : `<button class="btn btn-del-row" style="color:var(--danger); background:transparent; padding:0; justify-content:center">✕</button>`}
        `;

    body.appendChild(row);

    // === Tampilan KARTU di HP (<=640px): beri nama kolom (data-label) ke
    // tiap sel. Input "telanjang" dibungkus <label class="pg-cell"> agar
    // bisa menampilkan label lewat ::before. Tidak mengubah tampilan desktop. ===
    if (!isBranchRequest && !showSupplierColumn) {
      const labelKolom = isFulfillment
        ? ["", "Pesan", "Terima", "Harga Beli", "Margin (%)", "Harga Jual", "Diskon (%)", "Total", ""]
        : ["", "Pesan", "Harga Beli", "Margin (%)", "Harga Jual", "Diskon (%)", "Total", ""];
      Array.from(row.children).forEach((sel, i) => {
        const teks = labelKolom[i];
        if (!teks) return;
        if (sel.tagName === "INPUT") {
          const bungkus = document.createElement("label");
          bungkus.className = "pg-cell";
          bungkus.setAttribute("data-label", teks);
          sel.replaceWith(bungkus);
          bungkus.appendChild(sel);
        } else {
          sel.setAttribute("data-label", teks);
        }
      });
    }

    const ordInp = row.querySelector(".pg-ordered");
    const recInp = row.querySelector(".pg-received");
    // Awali _prevVal dengan nilai Pesan saat ini agar auto-sync Terima←Pesan
    // langsung aktif sejak baris dimuat (Terima ikut berubah saat Pesan diubah).
    ordInp._prevVal = qtyOrdered;
    const beliInp = row.querySelector(".pg-beli");
    const jualInp = row.querySelector(".pg-jual");
    const btnPlus = row.querySelector(".btn-plus-disc");
    const marginInp = row.querySelector(".pg-margin");
    const nettoDiv = row.querySelector(".purchase-grid-netto");
    const delBtn = row.querySelector(".btn-del-row");
    const discGroup = row.querySelector(".disc-group");
    const formBarangEdit = row.querySelector(".pg-name-edit");

    const calculateRow = () => {
      if (isBranchRequest || showSupplierColumn) {
        if (onChange) onChange();
        return;
      }
      // Total based on received qty in fulfillment mode, otherwise ordered qty
      const qOrd = parseFloat(ordInp.value) || 0;
      const qRec = recInp ? parseFloat(recInp.value) || 0 : 0;
      const qty = isFulfillment ? qRec : qOrd;

      // Visual feedback for mismatch in fulfillment mode
      if (isFulfillment && recInp) {
        if (qRec !== qOrd) {
          recInp.style.borderColor = "var(--warning)";
          recInp.style.backgroundColor = "rgba(245, 158, 11, 0.05)";
        } else {
          recInp.style.borderColor = "var(--primary)";
          recInp.style.backgroundColor = "";
        }
      }

      const hb = toAngka(beliInp.value);
      let hargaNeto = hb;
      row.querySelectorAll(".pg-disc").forEach((inp) => {
        const d = parseFloat(inp.value) || 0;
        hargaNeto = hargaNeto * (1 - d / 100);
      });
      nettoDiv.textContent = fmtRp(qty * hargaNeto);
      const hj = toAngka(jualInp.value);
      if (hargaNeto > 0 && hj > 0) {
        const margin = ((hj - hargaNeto) / hargaNeto) * 100;
        marginInp.value = margin.toFixed(2).replace(/\.00$/, "");
      }
      if (onChange) onChange();
    };

    const updateDiscColWidth = () => {
      if (isBranchRequest || showSupplierColumn) return;
      const groups = Array.from(target.querySelectorAll(".disc-group"));
      const maxInputs = Math.max(
        1,
        ...groups.map((g) => g.querySelectorAll(".pg-disc").length),
      );
      // Batasi lebar kolom diskon di HP agar grid tidak melebar tak terkendali
      const batasAtas = window.innerWidth < 640 ? 160 : 99999;
      const w = Math.min(batasAtas, Math.max(100, maxInputs * 60 + 30));
      target.style.setProperty("--disc-col-width", `${w}px`);
    };

    const combo = createPremiumCombo(`pg-combo-${id}`, currentData, {
      isItem: true,
      placeholder: "Cari barang...",
      onSelect: (sel) => {
        if (!isBranchRequest && !showSupplierColumn) {
          beliInp.value = toRibuan(sel.buy_price);
          jualInp.value = toRibuan(sel.sell_price);
          marginInp.value = sel.profit_margin || 0;
        }
        row._itemData = sel;
        const nameEditInp = row.querySelector(".pg-name-edit");
        if (nameEditInp) nameEditInp.value = sel.name || "";

        if (showSupplierColumn && row._suppCombo) {
          // Filter suppliers to only those who have this item
          const validSuppliers = sel.suppliers || [];
          row._suppCombo.updateData(validSuppliers);

          // Clear current selection and auto-select if only 1 valid supplier
          row._suppCombo.clear();
          if (validSuppliers.length === 1) {
            row._suppCombo.set(validSuppliers[0].id, validSuppliers[0].name);
          }
        }
        calculateRow();
      },
    });

    let suppCombo = null;
    if (showSupplierColumn) {
      const rowSuppliers = item?.suppliers || [];
      suppCombo = createPremiumCombo(`pg-supp-${id}`, rowSuppliers, {
        placeholder: "Pilih Supplier...",
        onSelect: (sel) => {
          // Find buy price for this supplier
          const spec = row._itemData?.supplier_details?.find(
            (s) => s.supplier_id == sel.id,
          );
          row._buyPrice = spec ? spec.buy_price : row._itemData?.buy_price || 0;
          calculateRow();
        },
      });
    }

    if (btnPlus) {
      btnPlus.onclick = () => {
        const inputs = row.querySelectorAll(".pg-disc");
        if (inputs.length >= 4)
          return showToast("Maksimal 4 tingkat diskon", "warning");
        const newInp = document.createElement("input");
        newInp.type = "number";
        newInp.className = "combobox-input pg-disc";
        newInp.placeholder = "0";
        newInp.style.textAlign = "center";
        newInp.oninput = calculateRow;
        discGroup.insertBefore(newInp, btnPlus);
        updateDiscColWidth();
      };
    }

    ordInp.oninput = () => {
      // If fulfillment, auto-update received if it was the same as ordered (synced)
      if (isFulfillment && recInp) {
        const prevOrd = ordInp._prevVal || 0;
        const currentRec = parseFloat(recInp.value) || 0;
        if (currentRec === prevOrd || currentRec === 0) {
          recInp.value = ordInp.value;
        }
      }
      ordInp._prevVal = parseFloat(ordInp.value) || 0;
      calculateRow();
    };
    if (recInp) recInp.oninput = calculateRow;

    if (beliInp) {
      beliInp.oninput = (e) => {
        e.target.value = toRibuan(toAngka(e.target.value));
        calculateRow();
      };
    }
    if (jualInp) {
      jualInp.oninput = (e) => {
        e.target.value = toRibuan(toAngka(e.target.value));
        calculateRow();
      };
    }
    row
      .querySelectorAll(".pg-disc")
      .forEach((inp) => (inp.oninput = calculateRow));
    if (marginInp) {
      marginInp.oninput = () => {
        const hb = toAngka(beliInp.value);
        let hargaNeto = hb;
        row.querySelectorAll(".pg-disc").forEach((inp) => {
          const d = parseFloat(inp.value) || 0;
          hargaNeto = hargaNeto * (1 - d / 100);
        });
        const margin = parseFloat(marginInp.value) || 0;
        const hj = hargaNeto + (hargaNeto * margin) / 100;
        jualInp.value = toRibuan(Math.round(hj));
        calculateRow();
      };
    }
    if (delBtn) {
      delBtn.onclick = () => {
        row.remove();
        if (onChange) onChange();
      };
    }
    if (item) {
      combo.set(item.id, item.name);
      row._itemData = item;
      calculateRow();
    }
    row._combo = combo;
    row._suppCombo = suppCombo;
    return row;
  };

  initialItems.forEach((it) => addRow(it));
  if (initialItems.length === 0) addRow();

  const methods = {
    addRow,
    updateDataSource: (newData) => {
      currentData = newData;
      target.querySelectorAll(".item-selector").forEach((sel) => {
        const row = sel.closest(".purchase-grid-row");
        if (row && row._combo) row._combo.updateData(newData);
      });
    },
    updateSuppliers: (newList) => {
      supplierList = newList;
      target.querySelectorAll(".purchase-grid-row").forEach((row) => {
        if (row._suppCombo) row._suppCombo.updateData(newList);
      });
    },
    getData: () => {
      const data = [];
      target.querySelectorAll(".purchase-grid-row").forEach((row) => {
        const comboElem = row.querySelector(".item-selector");
        const iid = comboElem._combo.val();
        if (iid) {
          const hbInp = row.querySelector(".pg-beli");
          const hb = hbInp
            ? toAngka(hbInp.value)
            : row._buyPrice || row._itemData?.buy_price || 0;

          const sid = row._suppCombo ? row._suppCombo.val() : null;

          const discInputs = row.querySelectorAll(".pg-disc");
          const discs =
            discInputs.length > 0
              ? Array.from(discInputs).map((inp) => parseFloat(inp.value) || 0)
              : [0, 0];

          let hargaNeto = hb;
          discs.forEach((d) => {
            hargaNeto = hargaNeto * (1 - d / 100);
          });

          const qOrd = parseFloat(row.querySelector(".pg-ordered").value) || 0;
          const recInp = row.querySelector(".pg-received");
          // Kolom "Terima" hanya muncul pada mode fulfillment. Bila tidak ada
          // (pembelian biasa, request cabang, atau pilih-supplier), jumlah yang
          // diterima dianggap sama dengan "Pesan" — bukan 0.
          const qRec = recInp ? parseFloat(recInp.value) || 0 : qOrd;

          const hjInp = row.querySelector(".pg-jual");
          const hj = hjInp
            ? toAngka(hjInp.value)
            : row._itemData?.sell_price || 0;

          const margInp = row.querySelector(".pg-margin");
          const margin = margInp
            ? parseFloat(margInp.value) || 0
            : row._itemData?.profit_margin || 0;

          const nameEditInp = row.querySelector(".pg-name-edit");
          const customName = nameEditInp
            ? nameEditInp.value
            : row._itemData?.name || "";

          data.push({
            item_id: parseInt(iid),
            supplier_id: sid ? parseInt(sid) : null,
            name: customName,
            code: row._itemData?.code || "",
            qty: showSupplierColumn ? qOrd : qRec,
            qty_ordered: qOrd,
            qty_received: showSupplierColumn ? qOrd : qRec,
            buy_price: hb,
            discount: hb - hargaNeto,
            disc1: discs[0] || 0,
            disc2: discs[1] || 0,
            sell_price: hj,
            profit_margin: margin,
            total: (showSupplierColumn ? qOrd : qRec) * hargaNeto,
            // Tarif PPN baris → dari master barang (grid ini tak punya kolom PPN).
            // null aman: backend mundur ke Item.ppn_percent / tarif toko.
            ppn_percent: row._itemData?.ppn_percent ?? null,
          });
        }
      });
      return data;
    },
    clear: () => {
      body.innerHTML = "";
      addRow();
      if (onChange) onChange();
    },
  };
  target._grid = methods;
  return methods;
}

/**
 * createPurchaseSummaryGrid
 * Grid ringkas untuk halaman purchases.html. Kolom mengikuti desain:
 * No | Nama Barang | Jenis | Jumlah | Satuan | Harga Beli | Diskon | Total | Tax | (aksi)
 *
 * Semua kolom utama bisa diedit LANGSUNG di tabel dan tersimpan otomatis (saat blur):
 *  - Jenis & Satuan  → master barang (PUT /items/{id})
 *  - Harga Beli & Tax(PPN) → data barang untuk supplier yang dipilih (PUT /items/{id}/harga-supplier)
 *  - Jumlah & Diskon → hanya per-transaksi (ikut tersimpan saat pembelian disimpan)
 *
 * Tax = PPN: saat barang dipilih, nilainya diambil dari setelan supplier
 * (ppn_percent bila ppn_type "excluded", 0 bila "included").
 * Tombol "Detail" tetap ada untuk margin/harga jual & history (detail_item.html).
 */
function createPurchaseSummaryGrid(container, config = {}) {
  const target =
    typeof container === "string"
      ? document.getElementById(container)
      : container;
  const {
    initialItems = [],
    onChange = null,
    onDetail = null,
    getSupplierId = null,
    // Tipe PPN transaksi saat ini ("include" | "exclude" | ""), dari dropdown form.
    getTipePpn = null,
    // Tarif PPN standar toko (angka), dipakai untuk popup "ubah ke X%".
    getTarifStandar = null,
    // Mode exclude: tulis persentase PPN ke field "Pajak" di ringkasan.
    setPajakRingkasan = null,
  } = config;

  let currentData = [];
  // Lebar kolom tetap (px) + Nama Barang dilebarkan. Tabel boleh scroll horizontal.
  const kolom =
    "40px minmax(260px, 1fr) 130px 70px 110px 120px 170px 150px 90px 120px";
  const lebarMin = "1300px";

  // Daftar Jenis & Satuan (untuk combo per baris). Dimuat sekali saat init.
  let daftarJenis = [];
  let daftarSatuan = [];

  const fmtRp = (n) =>
    new Intl.NumberFormat("id-ID", {
      style: "currency",
      currency: "IDR",
      minimumFractionDigits: 0,
    }).format(n || 0);
  const toAngka = (s) =>
    parseFloat(String(s).replace(/\./g, "").replace(",", ".")) || 0;
  const toRibuan = (n) => (n || 0).toLocaleString("id-ID");

  const cariByName = (daftar, name) => {
    const t = (name || "").trim().toLowerCase();
    if (!t || t === "-") return null;
    return (daftar || []).find(
      (d) => (d.name || "").trim().toLowerCase() === t,
    );
  };

  target.innerHTML = `
        <div style="overflow-x:auto">
          <div class="purchase-grid-container" style="min-width:${lebarMin}">
            <div class="purchase-grid-header" style="display:grid; grid-template-columns:${kolom}; gap:10px; padding:10px; background:var(--bg-color); font-weight:700; font-size:12px; border-radius:8px 8px 0 0">
                <div style="text-align:center">No</div>
                <div>Nama Barang</div>
                <div>Jenis</div>
                <div style="text-align:center">Jumlah</div>
                <div>Satuan</div>
                <div style="text-align:right">Harga Beli</div>
                <div style="text-align:center">Diskon (%)</div>
                <div style="text-align:right">Total</div>
                <div style="text-align:center">PPN Included (%)</div>
                <div></div>
            </div>
            <div class="purchase-grid-body" style="border:1px solid var(--border-color); border-top:none; border-radius:0 0 8px 8px; min-height:100px"></div>
          </div>
        </div>
    `;

  const body = target.querySelector(".purchase-grid-body");

  // harga neto setelah seluruh diskon bertingkat
  const hitungNeto = (detail) => {
    let neto = detail.buy_price || 0;
    (detail.discs || []).forEach((d) => {
      neto = neto * (1 - (parseFloat(d) || 0) / 100);
    });
    return neto;
  };

  const renumber = () => {
    Array.from(body.children).forEach((row, i) => {
      const noCell = row.querySelector(".pg2-no");
      if (noCell) noCell.textContent = i + 1;
    });
  };

  // Perbarui sel Total (qty × harga neto setelah diskon)
  const hitungTotal = (row) => {
    const d = row._detail;
    const totalCell = row.querySelector(".pg2-total");
    if (totalCell) totalCell.textContent = fmtRp((d.qty || 0) * hitungNeto(d));
  };

  // Simpan perubahan ke MASTER barang (jenis/satuan) — berlaku semua supplier.
  const simpanMaster = async (row, patch) => {
    const id = row._detail.item_id;
    if (!id || typeof api !== "function") return;
    try {
      await api("PUT", `/items/${id}`, patch);
    } catch (e) {
      if (typeof showToast === "function")
        showToast("Gagal simpan ke barang: " + e.message, "error");
    }
  };

  const ambilSupplierId = () => {
    try {
      return typeof getSupplierId === "function" ? getSupplierId() : null;
    } catch (e) {
      return null;
    }
  };

  // Simpan perubahan ke data barang untuk SUPPLIER yang dipilih (harga beli / PPN).
  const simpanSupplier = async (row, patch) => {
    const id = row._detail.item_id;
    if (!id || typeof api !== "function") return;
    const sid = ambilSupplierId();
    if (!sid) {
      if (typeof showToast === "function")
        showToast(
          "Pilih supplier dulu — harga/PPN belum tersimpan ke supplier",
          "warning",
        );
      return;
    }
    try {
      await api("PUT", `/items/${id}/harga-supplier`, {
        supplier_id: sid,
        ...patch,
      });
    } catch (e) {
      if (typeof showToast === "function")
        showToast("Gagal simpan ke supplier: " + e.message, "error");
    }
  };

  // Kosongkan satu baris (dipakai saat user menolak konversi tipe PPN → barang batal masuk).
  const kosongkanBaris = (row) => {
    const d = row._detail;
    d.item_id = null;
    d.name = "";
    d.code = "";
    d.buy_price = 0;
    d.sell_price = 0;
    d.profit_margin = 0;
    d.discs = [0];
    d.ppn = 0;
    d.ppn_type = "included";
    d.ppn_percent = 0;
    if (row._combo) row._combo.clear();
    if (row._isi) row._isi();
    if (onChange) onChange();
  };

  // Validasi PPN barang yang baru dipilih terhadap setelan transaksi.
  //  1) Tipe barang (include/exclude) harus sama dengan Tipe PPN transaksi.
  //     Bila beda → tanya "ubah jadi <tipe transaksi>?". Tolak = barang batal masuk.
  //  2) Persentase PPN harus sama dengan tarif standar toko.
  //     Bila beda → tanya "ubah ke <tarif>% sesuai faktur?". Tolak = pakai apa adanya.
  // Return true bila barang boleh masuk tabel, false bila dibatalkan.
  const prosesPpnSaatPilih = async (row, sel) => {
    const tipeTransaksi =
      (typeof getTipePpn === "function" ? getTipePpn() : "") || "";

    // Tipe transaksi "none" (Tanpa PPN): barang bebas PPN → tarif 0, tanpa konfirmasi.
    if (tipeTransaksi === "none") {
      row._detail.ppn_type = "none";
      row._detail.ppn_percent = 0;
      row._detail.ppn = 0;
      return true;
    }

    let tipeBarang = sel.ppn_type === "excluded" ? "exclude" : "include";
    let persen = sel.ppn_percent || 0;
    const tarifStandar =
      (typeof getTarifStandar === "function" ? getTarifStandar() : 11) || 11;
    let perluSimpan = false;

    // 1) Cek kecocokan tipe barang dengan tipe transaksi.
    if (tipeTransaksi && tipeBarang !== tipeTransaksi) {
      const labelBarang = tipeBarang === "include" ? "Include" : "Exclude";
      const labelTransaksi =
        tipeTransaksi === "include" ? "Include" : "Exclude";
      const ya =
        typeof showConfirm === "function"
          ? await showConfirm(
              `Barang "${sel.name}" untuk supplier ini tercatat PPN ${labelBarang}, padahal pembelian ini bertipe ${labelTransaksi}.\n\nUbah barang ini menjadi ${labelTransaksi}?`,
            )
          : true;
      if (!ya) return false; // batal menambah barang
      tipeBarang = tipeTransaksi;
      perluSimpan = true;
    }

    // 2) Cek persentase PPN terhadap tarif standar toko.
    if (persen !== tarifStandar && typeof showConfirm === "function") {
      const ya = await showConfirm(
        `PPN barang "${sel.name}" tercatat ${persen}%, bukan ${tarifStandar}%.\n\nUbah ke ${tarifStandar}% sesuai faktur?`,
      );
      if (ya) {
        persen = tarifStandar;
        perluSimpan = true;
      }
    }

    // Terapkan hasil ke baris.
    row._detail.ppn_type = tipeBarang === "exclude" ? "excluded" : "included";
    row._detail.ppn_percent = persen;
    row._detail.ppn = persen; // tampil di kolom (mode include); kolom tersembunyi saat exclude

    // Mode exclude: kolom per-baris hilang → persentase ditulis ke ringkasan "Pajak".
    if (tipeBarang === "exclude" && typeof setPajakRingkasan === "function") {
      setPajakRingkasan(persen);
    }

    // Simpan konversi ke setelan supplier + sinkronkan data combo (sesi ini tak menanya lagi).
    if (perluSimpan) {
      simpanSupplier(row, {
        ppn_type: row._detail.ppn_type,
        ppn_percent: persen,
      });
      sel.ppn_type = row._detail.ppn_type;
      sel.ppn_percent = persen;
    }

    return true;
  };

  const addRow = (item = null) => {
    const row = document.createElement("div");
    row.className = "purchase-grid-row";
    row.style = `display:grid; grid-template-columns:${kolom}; gap:10px; padding:8px 10px; border-bottom:1px solid var(--border-color); align-items:center`;

    // state lengkap baris
    row._detail = {
      item_id: item?.id || item?.item_id || null,
      name: item?.name || "",
      code: item?.code || "",
      category_id: item?.category_id || null,
      category_name: item?.category_name || "-",
      unit_id: item?.unit_id || null,
      unit_name: item?.unit_name || "-",
      qty: item?.qty || item?.qty_ordered || 1,
      buy_price: item?.buy_price || 0,
      sell_price: item?.sell_price || 0,
      profit_margin: item?.profit_margin || 0,
      discs:
        item?.discs ||
        [item?.disc1 || 0, item?.disc2 || 0].filter((v, i) => i === 0 || v),
      ppn: item?.ppn || 0,
      ppn_type: item?.ppn_type || "included",
      ppn_percent: item?.ppn_percent || 0,
    };

    row.innerHTML = `
            <div class="pg2-no" style="text-align:center; color:var(--text-muted); font-weight:600"></div>
            <div class="pg2-combo"></div>
            <div class="pg2-jenis"></div>
            <input type="number" class="combobox-input pg2-qty" value="${row._detail.qty}" min="0" style="text-align:center" />
            <div class="pg2-satuan"></div>
            <input type="text" class="combobox-input pg2-beli" value="0" style="text-align:right" />
            <div class="pg2-disc disc-group"></div>
            <div class="pg2-total purchase-grid-netto" style="text-align:right">Rp 0</div>
            <input type="number" class="combobox-input pg2-tax" value="0" min="0" step="0.01" style="text-align:center" />
            <div style="display:flex; gap:6px; justify-content:flex-end; align-items:center">
                <button class="btn btn-primary pg2-detail" style="padding:6px 10px; font-size:12px; border-radius:8px" title="Detail Item">Detail</button>
                <button class="btn pg2-del" style="color:var(--danger); background:transparent; padding:0; justify-content:center" title="Hapus baris">✕</button>
            </div>
        `;

    body.appendChild(row);

    const qtyInp = row.querySelector(".pg2-qty");
    const beliInp = row.querySelector(".pg2-beli");
    const taxInp = row.querySelector(".pg2-tax");
    const discCell = row.querySelector(".pg2-disc");
    const detailBtn = row.querySelector(".pg2-detail");
    const delBtn = row.querySelector(".pg2-del");

    // ── Diskon bertingkat (maks 4: disc1 lalu disc2 dari harga hasil disc1) ──
    const bacaDiskon = () => {
      row._detail.discs = Array.from(
        discCell.querySelectorAll(".pg2-disc-inp"),
      ).map((inp) => parseFloat(inp.value) || 0);
    };
    const bangunDiskon = () => {
      const discs =
        row._detail.discs && row._detail.discs.length ? row._detail.discs : [0];
      discCell.innerHTML = "";
      discs.forEach((d) => {
        const inp = document.createElement("input");
        inp.type = "number";
        inp.min = "0";
        inp.className = "combobox-input pg2-disc-inp";
        inp.value = d || 0;
        inp.style.textAlign = "center";
        inp.oninput = () => {
          bacaDiskon();
          hitungTotal(row);
          if (onChange) onChange();
        };
        discCell.appendChild(inp);
      });
      if (discs.length < 4) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-plus-disc";
        btn.textContent = "+";
        btn.title = "Tambah diskon bertingkat";
        btn.onclick = () => {
          bacaDiskon();
          row._detail.discs.push(0);
          bangunDiskon();
          hitungTotal(row);
          if (onChange) onChange();
        };
        discCell.appendChild(btn);
      }
    };

    // Combo Nama Barang (pemilih item)
    const combo = createPremiumCombo(
      row.querySelector(".pg2-combo"),
      currentData,
      {
        isItem: true,
        placeholder: "Cari barang...",
        onSelect: async (sel) => {
          row._detail.item_id = sel.id;
          row._detail.name = sel.name || "";
          row._detail.code = sel.code || "";
          row._detail.category_id = sel.category_id || null;
          row._detail.category_name = sel.category_name || "-";
          row._detail.unit_id = sel.unit_id || null;
          row._detail.unit_name = sel.unit_name || "-";
          row._detail.buy_price = sel.buy_price || 0;
          row._detail.sell_price = sel.sell_price || 0;
          row._detail.profit_margin = sel.profit_margin || 0;
          // Validasi tipe & persentase PPN terhadap setelan transaksi.
          // Bila user menolak konversi tipe, barang batal ditambahkan.
          const lanjut = await prosesPpnSaatPilih(row, sel);
          if (!lanjut) {
            kosongkanBaris(row);
            return;
          }
          isi();
          if (onChange) onChange();
        },
      },
    );
    row._combo = combo;

    // Combo Jenis (kategori) — ubah master barang
    const comboJenis = createPremiumCombo(
      row.querySelector(".pg2-jenis"),
      daftarJenis,
      {
        placeholder: "Jenis...",
        onSelect: (sel) => {
          row._detail.category_id = sel.id;
          row._detail.category_name = sel.name;
          simpanMaster(row, { category_id: sel.id });
          if (onChange) onChange();
        },
      },
    );
    row._comboJenis = comboJenis;

    // Combo Satuan — ubah master barang
    const comboSatuan = createPremiumCombo(
      row.querySelector(".pg2-satuan"),
      daftarSatuan,
      {
        placeholder: "Satuan...",
        onSelect: (sel) => {
          row._detail.unit_id = sel.id;
          row._detail.unit_name = sel.name;
          simpanMaster(row, { unit_id: sel.id });
          if (onChange) onChange();
        },
      },
    );
    row._comboSatuan = comboSatuan;

    // Isi seluruh input/combo dari row._detail (dipakai saat pilih item / pulihkan baris)
    const isi = () => {
      const d = row._detail;
      qtyInp.value = d.qty || 0;
      beliInp.value = toRibuan(d.buy_price || 0);
      taxInp.value = d.ppn || 0;
      const j = cariByName(daftarJenis, d.category_name);
      if (j) d.category_id = j.id;
      comboJenis.set(
        j ? j.id : "",
        d.category_name && d.category_name !== "-" ? d.category_name : "",
      );
      const u = cariByName(daftarSatuan, d.unit_name);
      if (u) d.unit_id = u.id;
      comboSatuan.set(
        u ? u.id : "",
        d.unit_name && d.unit_name !== "-" ? d.unit_name : "",
      );
      bangunDiskon();
      hitungTotal(row);
    };
    row._isi = isi;

    // ── Jumlah ──
    qtyInp.oninput = () => {
      row._detail.qty = parseFloat(qtyInp.value) || 0;
      hitungTotal(row);
      if (onChange) onChange();
    };

    // ── Harga Beli (format ribuan saat ketik, simpan ke supplier saat blur) ──
    beliInp.oninput = (e) => {
      e.target.value = toRibuan(toAngka(e.target.value));
      row._detail.buy_price = toAngka(e.target.value);
      hitungTotal(row);
      if (onChange) onChange();
    };
    beliInp.addEventListener("blur", () => {
      row._detail.buy_price = toAngka(beliInp.value);
      simpanSupplier(row, { harga_beli: row._detail.buy_price });
    });

    // ── Kolom "PPN Included (%)" — hanya tampil saat transaksi Include.
    //    Nilai disimpan ke setelan supplier saat blur; tipe ikut tipe transaksi.
    taxInp.addEventListener("blur", () => {
      const val = parseFloat(taxInp.value) || 0;
      const tipeTransaksi =
        (typeof getTipePpn === "function" ? getTipePpn() : "") || "";
      row._detail.ppn = val;
      row._detail.ppn_type =
        tipeTransaksi === "exclude" ? "excluded" : "included";
      row._detail.ppn_percent = val;
      simpanSupplier(row, {
        ppn_type: row._detail.ppn_type,
        ppn_percent: val,
      });
      if (onChange) onChange();
    });

    detailBtn.onclick = () => {
      if (!row._detail.item_id) {
        showToast("Pilih barang dulu sebelum membuka detail", "warning");
        return;
      }
      // sinkronkan qty & diskon terbaru dari input
      row._detail.qty = parseFloat(qtyInp.value) || 0;
      bacaDiskon();
      const idx = Array.from(body.children).indexOf(row);
      if (onDetail) onDetail(idx, row._detail);
    };

    delBtn.onclick = () => {
      row.remove();
      renumber();
      if (onChange) onChange();
    };

    if (row._detail.item_id) {
      combo.set(row._detail.item_id, row._detail.name);
    }
    isi();
    renumber();
    return row;
  };

  initialItems.forEach((it) => addRow(it));
  if (initialItems.length === 0) addRow();

  // Muat daftar Jenis & Satuan sekali, lalu segarkan combo baris yang sudah ada.
  (async () => {
    if (typeof api !== "function") return;
    try {
      [daftarJenis, daftarSatuan] = await Promise.all([
        api("GET", "/items/categories"),
        api("GET", "/items/units"),
      ]);
    } catch (e) {
      console.error("Gagal memuat kategori/satuan:", e);
      return;
    }
    Array.from(body.children).forEach((row) => {
      if (row._comboJenis) row._comboJenis.updateData(daftarJenis);
      if (row._comboSatuan) row._comboSatuan.updateData(daftarSatuan);
      if (row._isi) row._isi();
    });
  })();

  const methods = {
    addRow,
    updateDataSource: (newData) => {
      currentData = newData;
      Array.from(body.children).forEach((row) => {
        if (row._combo) row._combo.updateData(newData);
      });
    },
    // bentuk data SAMA dengan createPurchaseGrid agar simpanPembelian tetap jalan
    getData: () => {
      const data = [];
      Array.from(body.children).forEach((row) => {
        const d = row._detail;
        if (!d.item_id) return;
        const neto = hitungNeto(d);
        const discs = d.discs || [];
        data.push({
          item_id: parseInt(d.item_id),
          qty: d.qty || 0,
          qty_ordered: d.qty || 0,
          qty_received: d.qty || 0,
          buy_price: d.buy_price || 0,
          disc1: discs[0] || 0,
          disc2: discs[1] || 0,
          disc3: discs[2] || 0,
          disc4: discs[3] || 0,
          discount: (d.buy_price || 0) - neto,
          sell_price: d.sell_price || 0,
          profit_margin: d.profit_margin || 0,
          total: (d.qty || 0) * neto,
          // Tarif PPN baris (kolom "PPN Include" mode Included). null → backend mundur
          // ke Item.ppn_percent / tarif toko.
          ppn_percent: d.ppn_percent ?? d.ppn ?? null,
        });
      });
      return data;
    },
    // snapshot seluruh baris (termasuk state detail) untuk round-trip ke detail_item
    getDetailRows: () =>
      Array.from(body.children).map((row) => ({ ...row._detail })),
    // pulihkan satu baris (dipakai saat kembali dari detail_item)
    setRowDetail: (index, detail) => {
      const row = body.children[index];
      if (!row) return;
      row._detail = { ...detail };
      if (detail.item_id && row._combo)
        row._combo.set(detail.item_id, detail.name);
      if (row._isi) row._isi();
    },
    // bangun ulang seluruh baris dari snapshot (dipakai saat pulihkan form)
    loadRows: (rows) => {
      body.innerHTML = "";
      (rows || []).forEach((r) => addRow(r));
      if (body.children.length === 0) addRow();
    },
    clear: () => {
      body.innerHTML = "";
      addRow();
      if (onChange) onChange();
    },
  };
  target._grid = methods;
  return methods;
}

/**
 * createStandardSelect
 */
function createStandardSelect(container, data, config = {}) {
  const target =
    typeof container === "string"
      ? document.getElementById(container)
      : container;
  const {
    valField = "id",
    labField = "name",
    placeholder = "Semua",
    onSelect = null,
  } = config;
  target.innerHTML = `<select class="input-control"><option value="">${placeholder}</option>${data.map((d) => `<option value="${d[valField]}">${d[labField]}</option>`).join("")}</select>`;
  const sel = target.querySelector("select");
  sel.onchange = () => {
    if (onSelect)
      onSelect(
        data.find((d) => d[valField] == sel.value) || { id: "", name: "" },
      );
  };
  const methods = {
    val: () => sel.value,
    set: (v) => {
      sel.value = v;
    },
    updateData: (newData) => {
      const currentVal = sel.value;
      sel.innerHTML =
        `<option value="">${placeholder}</option>` +
        newData
          .map((d) => `<option value="${d[valField]}">${d[labField]}</option>`)
          .join("");
      sel.value = currentVal;
    },
  };
  target._combo = methods;
  return methods;
}

/**
 * createFilterBar
 */
function createFilterBar(container, config = {}) {
  const target =
    typeof container === "string"
      ? document.getElementById(container)
      : container;
  const {
    onFilter = null,
    statusOptions = [],
    entities = [],
    entityPlaceholder = "Cari...",
    useEntitySelect = false,
  } = config;
  target.className = "filter-bar";
  target.style.display = "flex";
  target.style.flexWrap = "wrap";
  target.style.gap = "12px";
  target.style.marginBottom = "20px";
  target.style.alignItems = "center";
  let entityHtml = useEntitySelect
    ? `<div class="f-entity-container" style="flex:1; min-width:140px"></div>`
    : `<div class="f-entity-container" style="flex:1.5; min-width:200px"></div>`;
  target.innerHTML = `<input type="date" class="input-control f-start" style="flex:1; min-width:140px" /><input type="date" class="input-control f-end" style="flex:1; min-width:140px" /><select class="input-control f-status" style="flex:1; min-width:140px"><option value="">Semua Status</option>${statusOptions.map((o) => `<option value="${o.value}">${o.label}</option>`).join("")}</select>${entityHtml}`;
  const startInp = target.querySelector(".f-start"),
    endInp = target.querySelector(".f-end"),
    statusInp = target.querySelector(".f-status"),
    entityCont = target.querySelector(".f-entity-container");
  const now = new Date();
  startInp.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
  endInp.value = now.toISOString().split("T")[0];
  let selectedId = "";
  const cb = {
    placeholder: entityPlaceholder,
    onSelect: (sel) => {
      selectedId = sel.id;
      if (onFilter) onFilter();
    },
  };
  let combo = useEntitySelect
    ? createStandardSelect(entityCont, entities, cb)
    : createPremiumCombo(
        entityCont,
        [{ id: "", name: "Semua" }, ...entities],
        cb,
      );
  const trigger = () => {
    if (onFilter) onFilter();
  };
  startInp.onchange = trigger;
  endInp.onchange = trigger;
  statusInp.onchange = trigger;
  const methods = {
    getValues: () => ({
      start_date: startInp.value,
      end_date: endInp.value,
      status: statusInp.value,
      entity_id: selectedId || combo.val(),
    }),
    updateEntities: (data) => {
      if (useEntitySelect) combo.updateData(data);
      else combo.updateData([{ id: "", name: "Semua" }, ...data]);
    },
    updateStatusOptions: (options) => {
      statusInp.innerHTML =
        '<option value="all">Semua Status</option>' +
        options
          .map((o) => `<option value="${o.value}">${o.label}</option>`)
          .join("");
    },
  };
  target._filter = methods;
  return methods;
}

/**
 * createPaymentModal
 */
function createPaymentModal(config = {}) {
  const { type = "ap", onSuccess = null } = config;
  const modalId = `mPayShared_${type}_${Math.floor(Math.random() * 1000)}`;
  const overlay = document.createElement("div");
  overlay.id = modalId;
  overlay.className = "modal-overlay";
  document.body.appendChild(overlay);
  const title =
    type === "ap" ? "Bayar Hutang Supplier" : "Terima Pembayaran Piutang";
  const apiPath = type === "ap" ? "/purchases" : "/sales";
  overlay.innerHTML = `<div class="modal-box" style="width:min(92vw, 480px)"><div class="modal-hdr"><h2>💸 ${title}</h2><button class="btn-x">×</button></div><input type="hidden" class="p-id" /><input type="hidden" class="p-remaining" /><div class="p-info" style="background:var(--bg-color); border-radius:var(--radius-sm); padding:0.875rem; margin-bottom:var(--space-md); font-size:0.9375rem;"></div><div class="input-group" style="margin-bottom:16px"><label>Metode Pembayaran *</label><select class="input-control p-method"><option value="cash">Kas</option><option value="bank">Bank</option><option value="mix">Gabungan</option></select></div><div class="p-balances" style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:16px;"><div style="background:var(--bg-color); border-radius:10px; padding:12px"><div>Saldo Kas</div><div class="p-cash-balance" style="font-weight:800">Rp 0</div></div><div style="background:var(--bg-color); border-radius:10px; padding:12px"><div>Saldo Bank</div><div class="p-bank-balance" style="font-weight:800">Rp 0</div></div></div><div class="row2" style="margin-bottom:16px"><div class="input-group p-cash-group"><label>Dari Kas</label><input type="text" class="input-control p-cash-amt" placeholder="0" /></div><div class="input-group p-bank-group"><label>Dari Bank</label><input type="text" class="input-control p-bank-amt" placeholder="0" /></div></div><div style="background:rgba(16,185,129,0.08); border:1px solid rgba(16,185,129,0.2); border-radius:12px; padding:14px; margin-bottom:16px;"><div style="display:flex; justify-content:space-between;"><span>Total Bayar</span><b class="p-total-label" style="font-size:1.125rem; color:#10b981">Rp 0</b></div></div><div class="input-group"><label>Catatan</label><input type="text" class="input-control p-note" /></div><div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:16px;"><button class="btn p-cancel">Batal</button><button class="btn btn-primary p-submit">✓ Konfirmasi</button></div></div>`;
  const idInp = overlay.querySelector(".p-id"),
    remInp = overlay.querySelector(".p-remaining"),
    infoDiv = overlay.querySelector(".p-info"),
    methodSel = overlay.querySelector(".p-method"),
    cashGroup = overlay.querySelector(".p-cash-group"),
    bankGroup = overlay.querySelector(".p-bank-group"),
    cashInp = overlay.querySelector(".p-cash-amt"),
    bankInp = overlay.querySelector(".p-bank-amt"),
    totalLabel = overlay.querySelector(".p-total-label"),
    cashBalDiv = overlay.querySelector(".p-cash-balance"),
    bankBalDiv = overlay.querySelector(".p-bank-balance"),
    noteInp = overlay.querySelector(".p-note"),
    submitBtn = overlay.querySelector(".p-submit"),
    cancelBtn = overlay.querySelector(".p-cancel"),
    closeBtn = overlay.querySelector(".btn-x");
  const fmtRp = (n) => "Rp " + Math.round(n).toLocaleString("id-ID");
  const parseNum = (s) =>
    parseFloat(String(s).replace(/\./g, "").replace(",", ".")) || 0;
  const formatNum = (n) => (n || 0).toLocaleString("id-ID");
  const syncTotal = () => {
    totalLabel.textContent = fmtRp(
      parseNum(cashInp.value) + parseNum(bankInp.value),
    );
  };
  const applyMethod = (reset = false) => {
    const method = methodSel.value;
    const rem = parseNum(remInp.value);
    cashGroup.style.display = method === "bank" ? "none" : "block";
    bankGroup.style.display = method === "cash" ? "none" : "block";
    if (reset) {
      if (method === "cash") {
        cashInp.value = formatNum(rem);
        bankInp.value = "";
      } else if (method === "bank") {
        bankInp.value = formatNum(rem);
        cashInp.value = "";
      } else {
        cashInp.value = "";
        bankInp.value = "";
      }
    }
    syncTotal();
  };
  cashInp.oninput = (e) => {
    e.target.value = formatNum(parseNum(e.target.value));
    syncTotal();
  };
  bankInp.oninput = (e) => {
    e.target.value = formatNum(parseNum(e.target.value));
    syncTotal();
  };
  methodSel.onchange = () => applyMethod(true);
  const close = () => {
    overlay.style.display = "none";
    document.body.style.overflow = "";
  };
  cancelBtn.onclick = close;
  closeBtn.onclick = close;
  submitBtn.onclick = async () => {
    const id = idInp.value;
    const cash = parseNum(cashInp.value);
    const bank = parseNum(bankInp.value);
    if (cash + bank <= 0) return showToast("Masukkan nominal", "error");
    try {
      showLoading("Memproses...");
      await api("POST", `${apiPath}/${id}/pay`, {
        amount: cash + bank,
        cash_amount: cash,
        bank_amount: bank,
        notes: noteInp.value,
      });
      hideLoading();
      showToast("Berhasil ✓");
      close();
      if (onSuccess) onSuccess();
    } catch (ex) {
      hideLoading();
      showToast(ex.message, "error");
    }
  };
  const methods = {
    open: async (data) => {
      idInp.value = data.id;
      const remaining = data.total - data.paid;
      remInp.value = remaining;
      infoDiv.innerHTML = `<div style="font-weight:700; color:var(--primary)">${data.name || ""}</div><div style="display:flex;justify-content:space-between"><span>Tagihan</span><b>${fmtRp(data.total)}</b></div><div style="display:flex;justify-content:space-between"><span>Sisa</span><b style="color:#ef4444">${fmtRp(remaining)}</b></div>`;
      noteInp.value = "";
      methodSel.value = "cash";
      applyMethod(true);
      overlay.style.display = "flex";
      document.body.style.overflow = "hidden";
      try {
        const bal = await api("GET", "/accounting/liquid-balances");
        cashBalDiv.textContent = fmtRp(bal.cash_balance);
        bankBalDiv.textContent = fmtRp(bal.bank_balance);
      } catch (e) {}
    },
    close,
  };
  overlay._modal = methods;
  return methods;
}

/**
 * createOrderManager
 * A unified component that manages Supplier Selection, Item Grid, and Grand Total Summary.
 */
async function createOrderManager(containerId, config = {}) {
  const target = document.getElementById(containerId);
  const {
    type = "purchase",
    initialData = null,
    onChange = null,
    isBranchRequest = false,
    isSplitFulfillment = false,
    from_po = false,
    allowNameEdit = false,
    readonlySelector = false,
  } = config;

  let itemsGrid = null;
  let supplierCombo = null;

  target.innerHTML = `
        <div class="order-manager-wrap ${isBranchRequest ? "" : "om-grid"}" style="${type === "po" ? "display:none;" : ""} margin-bottom: 24px">
            <div class="card" style="padding:24px">
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px">
                    <div class="input-group" style="${isBranchRequest || isSplitFulfillment || (from_po && readonlySelector) ? "display:none" : ""}">
                        <label>Supplier / Vendor *</label>

                        <div id="om-supplier-combo"></div>
                    </div>
                    <div class="input-group">
                        <label>Tanggal *</label>
                        <input type="date" id="om-date" class="input-control" value="${new Date().toISOString().split("T")[0]}" />
                    </div>
                </div>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 16px">
                    <div class="input-group">
                        <label>No. Referensi / Faktur</label>
                        <input id="om-number" class="input-control" placeholder="Otomatis..." />
                    </div>
                    <div class="input-group">
                        <label>Jatuh Tempo (Opsional)</label>
                        <input type="date" id="om-due-date" class="input-control" />
                    </div>
                </div>
            </div>
            <div class="card" style="padding:24px; background: var(--primary-light); border: 1px solid var(--primary); display: flex; flex-direction: column; justify-content: center">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px">
                    <label style="font-weight:700">Status PPN</label>
                    <div style="display:flex; background:var(--bg-color); padding:4px; border-radius:50px; border:1px solid var(--border-color)">
                        <input type="radio" name="om-tax-type" id="om-tax-inc" value="include" checked style="display:none" />
                        <label for="om-tax-inc" style="padding:4px 12px; border-radius:50px; cursor:pointer; font-size:11px">Include</label>
                        <input type="radio" name="om-tax-type" id="om-tax-exc" value="exclude" style="display:none" />
                        <label for="om-tax-exc" style="padding:4px 12px; border-radius:50px; cursor:pointer; font-size:11px">Exclude</label>
                    </div>
                </div>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px">
                    <div class="input-group">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px">
                            <label style="margin:0">Diskon Global (%)</label>
                            <div id="om-toggle-disc" style="width:80px"></div>
                        </div>
                        <input type="number" id="om-global-disc" class="input-control" value="0" />
                    </div>
                    <div class="input-group">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px">
                            <label style="margin:0">PPN (%)</label>
                            <div id="om-toggle-tax" style="width:80px"></div>
                        </div>
                        <input type="number" id="om-global-tax" class="input-control" value="0" />
                    </div>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid rgba(0,0,0,0.1); padding-top:16px">
                    <span style="font-weight:700; color:var(--text-main); flex-shrink:0">GRAND TOTAL</span>
                    <span id="om-total-label" style="font-size:clamp(1.25rem,6vw,1.75rem); font-weight:900; color:var(--primary); min-width:0; text-align:right; overflow-wrap:anywhere">Rp 0</span>
                </div>
            </div>
        </div>
        <div class="card" style="padding:24px">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px">
                <h3>Daftar Barang</h3>
                ${readonlySelector ? "" : '<button class="btn btn-primary" id="om-btn-add">+ Tambah Barang</button>'}
            </div>
            <div id="om-grid-container"></div>
        </div>
    `;

  const fmtRp = (n) =>
    new Intl.NumberFormat("id-ID", {
      style: "currency",
      currency: "IDR",
      minimumFractionDigits: 0,
    }).format(n || 0);
  const recalc = () => {
    if (!itemsGrid) return;
    const items = itemsGrid.getData();
    const subtotal = items.reduce((acc, it) => {
      let hargaNeto =
        it.buy_price * (1 - it.disc1 / 100) * (1 - it.disc2 / 100);
      return acc + it.qty * hargaNeto;
    }, 0);
    const discInput = document.getElementById("om-global-disc");
    const taxInput = document.getElementById("om-global-tax");
    const disc = discInput ? parseFloat(discInput.value) || 0 : 0;
    const tax = taxInput ? parseFloat(taxInput.value) || 0 : 0;
    const grand = subtotal * (1 - disc / 100) * (1 + tax / 100);
    const totalLabel = document.getElementById("om-total-label");
    if (totalLabel) totalLabel.textContent = fmtRp(grand);
    if (onChange) onChange(grand);
  };

  const suppliers = await api("GET", "/suppliers/?limit=1000&active_only=true");
  supplierCombo = createPremiumCombo("om-supplier-combo", suppliers, {
    onSelect: async (sel) => {
      const items = await api("GET", `/purchases/items/?supplier_id=${sel.id}`);
      itemsGrid.updateDataSource(items);
    },
  });

  const isFulfillment = type === "purchase";
  const itemsGridContainer = "om-grid-container";

  itemsGrid = createPurchaseGrid(itemsGridContainer, {
    isFulfillment,
    isBranchRequest,
    showSupplierColumn: isSplitFulfillment,
    allowNameEdit: allowNameEdit || !!from_po,
    readonlySelector: readonlySelector,
    initialItems:
      initialData?.items?.map((it) => ({
        id: it.item_id,
        name: it.item?.name,
        code: it.item?.code,
        qty_ordered: it.qty_ordered || it.qty,
        qty_received: it.qty_received || it.qty,
        buy_price: it.buy_price,
        sell_price: it.item?.sell_price,
        profit_margin: it.item?.profit_margin,
        disc1: it.disc1,
        disc2: it.disc2,
        supplier_id: it.item?.suppliers?.[0]?.id, // Default to first supplier if available
        suppliers: it.item?.suppliers || [],
        supplier_details: it.item?.supplier_details || [],
      })) || [],
    itemDataSource: [], // Will be updated
    onChange: recalc,
  });

  if (isBranchRequest || isSplitFulfillment) {
    const allItems = await api("GET", "/items/?limit=1000");
    itemsGrid.updateDataSource(allItems);
  }

  const btnAdd = document.getElementById("om-btn-add");
  if (btnAdd) {
    btnAdd.onclick = () => itemsGrid.addRow();
  }

  const discInput = document.getElementById("om-global-disc");
  if (discInput) discInput.oninput = recalc;
  const taxInput = document.getElementById("om-global-tax");
  if (taxInput) taxInput.oninput = recalc;

  // Initialize Toggle Buttons for Global Discount and PPN
  createToggleButton("om-toggle-disc", {
    targetId: "om-global-disc",
    activeLabel: "Kunci",
    inactiveLabel: "Buka",
    isActive: true, // Default Terkunci
  });
  createToggleButton("om-toggle-tax", {
    targetId: "om-global-tax",
    activeLabel: "Kunci",
    inactiveLabel: "Buka",
    isActive: true, // Default Terkunci
  });

  if (initialData) {
    document.getElementById("om-date").value = initialData.date;
    if (type !== "po" && !config.from_po) {
      document.getElementById("om-number").value = initialData.number || "";
    }
    const discPct =
      initialData.subtotal > 0
        ? (initialData.discount / initialData.subtotal) * 100
        : initialData.discount || 0;
    const taxPct =
      initialData.tax_percent !== undefined && initialData.tax_percent !== 0
        ? initialData.tax_percent
        : initialData.subtotal - initialData.discount > 0
          ? (initialData.tax / (initialData.subtotal - initialData.discount)) *
            100
          : initialData.tax || 0;

    const fmtPct = (v) =>
      v % 1 === 0 ? v.toString() : parseFloat(v.toFixed(2)).toString();

    if (discInput) discInput.value = fmtPct(discPct);
    if (taxInput) taxInput.value = fmtPct(taxPct);

    if (initialData.supplier_id && supplierCombo)
      supplierCombo.set(
        initialData.supplier_id,
        initialData.supplier?.name || "Supplier",
      );

    if (initialData.is_tax_included === false) {
      document.getElementById("om-tax-exc").checked = true;
    } else {
      document.getElementById("om-tax-inc").checked = true;
    }
    recalc(); // Ensure grand total is updated after loading percentages
  }

  return {
    getData: () => ({
      supplier_id:
        isBranchRequest || isSplitFulfillment ? null : supplierCombo.val(),
      date: document.getElementById("om-date").value,
      number: document.getElementById("om-number").value,
      due_date: document.getElementById("om-due-date").value || null,
      is_tax_included:
        document.querySelector('input[name="om-tax-type"]:checked').value ===
        "include",
      tax_percent:
        parseFloat(document.getElementById("om-global-tax").value) || 0,
      discount:
        isBranchRequest || isSplitFulfillment
          ? 0
          : parseFloat(document.getElementById("om-global-disc").value) || 0,
      tax:
        isBranchRequest || isSplitFulfillment
          ? 0
          : parseFloat(document.getElementById("om-global-tax").value) || 0,
      items: itemsGrid.getData(),
    }),
    itemsGrid,
    supplierCombo,
  };
}

/**
 * createItemChangeModal
 * Digunakan untuk konfirmasi perubahan nama barang (cloning) saat terima barang.
 */
function createItemChangeModal(nameChanges) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.style.display = "flex";

    const box = document.createElement("div");
    box.className = "modal-box";
    box.style.maxWidth = "500px";

    const hdr = `<div class="modal-hdr">
      <h2 style="color:var(--primary)">📝 Perubahan Nama Barang</h2>
      <button class="btn-x">×</button>
    </div>`;

    const body = `
      <p style="margin-bottom:16px; font-size:14px; color:var(--text-muted)">
        Anda mengubah nama beberapa barang. Sistem akan membuat variasi barang baru (cloning) dengan nama tersebut untuk stok cabang Anda.
      </p>
      <div style="background:var(--bg-color); border-radius:12px; padding:12px; margin-bottom:20px; border:1px solid var(--border-color)">
        <table style="width:100%; font-size:13px; border-collapse:collapse">
          <thead>
            <tr style="text-align:left; color:var(--text-muted); border-bottom:1px solid var(--border-color)">
              <th style="padding:8px">Nama Asli</th>
              <th style="padding:8px">Nama Baru</th>
            </tr>
          </thead>
          <tbody>
            ${nameChanges
              .map(
                (nc) => `
              <tr style="border-bottom:1px solid var(--border-color)">
                <td style="padding:8px; color:var(--text-main)">${nc.oldName}</td>
                <td style="padding:8px; color:var(--primary); font-weight:700">${nc.newName}</td>
              </tr>
            `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px">
        <button class="btn btn-cancel" style="padding:12px">Batal</button>
        <button class="btn btn-primary btn-confirm" style="padding:12px">Ya, Buat Barang Baru</button>
      </div>
    `;

    box.innerHTML = hdr + body;
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    const close = () => {
      document.body.removeChild(overlay);
    };

    box.querySelector(".btn-x").onclick = () => {
      close();
      resolve(false);
    };
    box.querySelector(".btn-cancel").onclick = () => {
      close();
      resolve(false);
    };
    box.querySelector(".btn-confirm").onclick = () => {
      close();
      resolve(true);
    };
  });
}

/**
 * createToggleButton
 * Button serbaguna untuk mengaktifkan/mengunci fitur tertentu (misal: Diskon Global, PPN).
 * Config: { label, targetId, activeLabel, inactiveLabel, onToggle }
 */
function createToggleButton(container, config = {}) {
  const target =
    typeof container === "string"
      ? document.getElementById(container)
      : container;
  const {
    targetId = null,
    activeLabel = "🔒 Kunci",
    inactiveLabel = "🔓 Aktifkan",
    onToggle = null,
    isActive = false,
  } = config;

  target.innerHTML = `
    <button class="btn btn-toggle-component" style="padding: 4px 10px; font-size: 11px; width: 100%; display: flex; align-items: center; justify-content: center; gap: 6px; transition: all 0.2s ease;">
      <span class="toggle-icon">${isActive ? "🔒" : "🔓"}</span>
      <span class="toggle-text">${isActive ? activeLabel : inactiveLabel}</span>
    </button>
  `;

  const btn = target.querySelector(".btn-toggle-component");
  const iconEl = btn.querySelector(".toggle-icon");
  const textEl = btn.querySelector(".toggle-text");
  const linkedInput = targetId ? document.getElementById(targetId) : null;

  let state = isActive;

  const updateUI = () => {
    if (state) {
      btn.style.background = "var(--primary)";
      btn.style.color = "#fff";
      iconEl.textContent = "🔒";
      textEl.textContent = activeLabel;
      if (linkedInput) {
        linkedInput.readOnly = true;
        linkedInput.style.background = "rgba(0,0,0,0.05)";
        linkedInput.style.cursor = "not-allowed";
      }
    } else {
      btn.style.background = "var(--bg-color)";
      btn.style.color = "var(--text-main)";
      btn.style.border = "1px solid var(--border-color)";
      iconEl.textContent = "🔓";
      textEl.textContent = inactiveLabel;
      if (linkedInput) {
        linkedInput.readOnly = false;
        linkedInput.style.background = "";
        linkedInput.style.cursor = "text";
      }
    }
  };

  btn.onclick = (e) => {
    e.preventDefault();
    state = !state;
    updateUI();
    if (onToggle) onToggle(state);
  };

  updateUI();

  return {
    isOn: () => state,
    toggle: (s) => {
      state = s;
      updateUI();
    },
  };
}

/**
 * setupBarcodeScanner (Global Scanner Hook)
 * Mendeteksi ketikan cepat (khas scanner) dan memicu callback.
 * Memperbaiki bug perhitungan waktu dari today() menjadi Date.now().
 */
function setupBarcodeScanner(onScan, config = {}) {
  const { minLength = 2, interval = 50 } = config;
  let buffer = "";
  let lastKeyTime = Date.now();

  document.addEventListener("keydown", (e) => {
    // Abaikan jika tombol fungsi atau navigasi
    if (e.key.length > 1 && e.key !== "Enter") return;

    const currentTime = Date.now();
    const timeDiff = currentTime - lastKeyTime;
    lastKeyTime = currentTime;

    // Jika jeda terlalu lama, berarti input manual (manusia), reset buffer
    if (timeDiff > interval) {
      buffer = "";
    }

    if (e.key === "Enter") {
      if (buffer.length >= minLength) {
        e.preventDefault();
        const scanned = buffer.trim();
        buffer = "";
        if (onScan) onScan(scanned);
      } else {
        buffer = ""; // Reset jika Enter ditekan tapi buffer pendek
      }
      return;
    }

    if (e.key.length === 1) {
      buffer += e.key;
    }
  });
}

/**
 * aktifkanTabelResponsif (Tabel → Kartu di HP)
 * --------------------------------------------
 * Membuat tabel data (.tbl / .tbl-input) bisa tampil sebagai KARTU bertumpuk
 * di layar kecil (<=640px). Caranya: menyalin teks judul kolom dari <thead>
 * ke atribut data-label pada tiap <td> di kolom yang sama. CSS-lah yang
 * menampilkan label itu (lewat td::before) hanya di layar HP — tampilan
 * desktop tidak berubah sama sekali.
 *
 * Fungsi ini:
 *  - Berjalan OTOMATIS di setiap halaman yang memuat components.js.
 *  - Memantau isi <tbody> yang dimuat belakangan (async) lewat MutationObserver,
 *    jadi tidak perlu dipanggil ulang manual tiap kali data dimuat.
 */
function aktifkanTabelResponsif() {
  // Beri data-label ke semua sel <td> dalam satu tabel sesuai judul kolomnya.
  const beriLabelTabel = (tabel) => {
    const header = tabel.tHead && tabel.tHead.rows[0];
    if (!header || !header.cells.length) return;
    const judul = Array.from(header.cells).map((th) => th.textContent.trim());
    Array.from(tabel.tBodies).forEach((tbody) => {
      Array.from(tbody.rows).forEach((baris) => {
        Array.from(baris.cells).forEach((sel, i) => {
          // Hanya isi kalau belum ada label & ada judul kolomnya
          if (judul[i] && !sel.hasAttribute("data-label")) {
            sel.setAttribute("data-label", judul[i]);
          }
        });
      });
    });
  };

  const SELEKTOR = "table.tbl, table.tbl-input";

  // Telusuri node yang baru ditambahkan dan beri label tabel yang relevan.
  const prosesNode = (node) => {
    if (node.nodeType !== 1) return; // hanya Element
    if (node.matches && node.matches(SELEKTOR)) beriLabelTabel(node);
    if (node.querySelectorAll)
      node.querySelectorAll(SELEKTOR).forEach(beriLabelTabel);
    // Baris/sel yang ditambahkan ke tabel yang SUDAH ada (kasus paling umum:
    // <tbody> diisi JS setelah halaman jalan).
    const tabelInduk = node.closest && node.closest(SELEKTOR);
    if (tabelInduk) beriLabelTabel(tabelInduk);
  };

  // 1) Label tabel yang sudah ada saat halaman dimuat.
  document.querySelectorAll(SELEKTOR).forEach(beriLabelTabel);

  // 2) Pantau penambahan node berikutnya (data async, tab di-switch, dll).
  const pengamat = new MutationObserver((daftarMutasi) => {
    daftarMutasi.forEach((m) => m.addedNodes.forEach(prosesNode));
  });
  pengamat.observe(document.body, { childList: true, subtree: true });
}

// Jalankan otomatis di setiap halaman yang memuat components.js.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", aktifkanTabelResponsif);
} else {
  aktifkanTabelResponsif();
}
