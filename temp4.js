
      requireAuth();

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


      let allSuppliers = [];
      let currentSupplierItems = [];
      let barisIdx = 0;
      let activeCombobox = null;
      let resizeObserver = null;
      let latestLiquidBalances = { cash_balance: 0, bank_balance: 0 };

      function formatNum(n) {
        return new Intl.NumberFormat("id-ID").format(n || 0);
      }
      function parseNum(s) {
        return parseFloat(String(s).replace(/\./g, "").replace(",", ".")) || 0;
      }
      function normalizeComboboxText(value) {
        return String(value || "")
          .trim()
          .toLowerCase();
      }
      function formatSplitPaymentInput(input) {
        const raw = parseNum(input.value);
        const pos = input.selectionStart;
        const oldLen = input.value.length;
        input.value = formatNum(raw);
        const newLen = input.value.length;
        input.setSelectionRange(
          pos + (newLen - oldLen),
          pos + (newLen - oldLen),
        );
        syncBayarTotal();
      }
      function syncBayarTotal() {
        const cash = parseNum(
          document.getElementById("bayarCashAmt")?.value || 0,
        );
        const bank = parseNum(
          document.getElementById("bayarBankAmt")?.value || 0,
        );
        document.getElementById("bayarTotalLabel").textContent = fmtRp(
          cash + bank,
        );
      }
      function applyBayarMethod(resetValues = false) {
        const method = document.getElementById("bayarMethod")?.value || "cash";
        const remaining = parseNum(
          document.getElementById("bayarRemaining")?.value || 0,
        );
        const cashGroup = document.getElementById("bayarCashGroup");
        const bankGroup = document.getElementById("bayarBankGroup");
        const cashInput = document.getElementById("bayarCashAmt");
        const bankInput = document.getElementById("bayarBankAmt");
        if (!cashGroup || !bankGroup || !cashInput || !bankInput) return;

        cashGroup.style.display = method === "bank" ? "none" : "block";
        bankGroup.style.display = method === "cash" ? "none" : "block";

        if (method === "cash") {
          bankInput.value = "";
          if (resetValues)
            cashInput.value = remaining ? formatNum(remaining) : "";
        } else if (method === "bank") {
          cashInput.value = "";
          if (resetValues)
            bankInput.value = remaining ? formatNum(remaining) : "";
        } else if (resetValues) {
          const cashPart = Math.min(
            remaining,
            latestLiquidBalances?.cash_balance || 0,
          );
          const bankPart = Math.max(0, remaining - cashPart);
          cashInput.value = cashPart ? formatNum(cashPart) : "";
          bankInput.value = bankPart ? formatNum(bankPart) : "";
        }

        syncBayarTotal();
      }
      function goToFundingKas() {
        const note = encodeURIComponent(
          "Setoran modal pemilik / dana pemodal untuk pembayaran hutang supplier",
        );
        location.href = `/accounting?tab=kas&openKas=1&type=income&account_code=3-1100&desc=${note}`;
      }
      async function loadLiquidBalances() {
        try {
          latestLiquidBalances = await api(
            "GET",
            "/accounting/liquid-balances",
          );
        } catch (e) {
          latestLiquidBalances = { cash_balance: 0, bank_balance: 0 };
        }
        document.getElementById("bayarCashBalance").textContent = fmtRp(
          latestLiquidBalances.cash_balance || 0,
        );
        document.getElementById("bayarBankBalance").textContent = fmtRp(
          latestLiquidBalances.bank_balance || 0,
        );
        return latestLiquidBalances;
      }

      // GAYA BADGE STATUS MENYESUAIKAN MANAJEMEN CABANG
      const sb = (s) => {
        if (s === "paid")
          return '<span style="background:rgba(16,185,129,0.15);color:#10b981;padding:4px 10px;border-radius:6px;font-weight:700;font-size:12px;">✓ Lunas</span>';
        if (s === "unpaid")
          return '<span style="background:rgba(239,68,68,0.15);color:#ef4444;padding:4px 10px;border-radius:6px;font-weight:700;font-size:12px;">✗ Belum Lunas</span>';
        if (s === "partial")
          return '<span style="background:rgba(245,158,11,0.15);color:#f59e0b;padding:4px 10px;border-radius:6px;font-weight:700;font-size:12px;">~ Sebagian</span>';
        return `<span style="background:rgba(148,163,184,0.15);color:#94a3b8;padding:4px 10px;border-radius:6px;font-weight:700;font-size:12px;">${s}</span>`;
      };

      function today() {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      }
      
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

      function fmtRp(n) {
        return new Intl.NumberFormat("id-ID", {
          style: "currency",
          currency: "IDR",
          minimumFractionDigits: 0,
        }).format(n || 0);
      }
      function fmtDate(d) {
        if (!d) return "-";
        const dt = new Date(d + "T00:00:00");
        return dt.toLocaleDateString("id-ID", {
          day: "2-digit",
          month: "short",
          year: "numeric",
        });
      }

      function positionDropdown(inputEl, dropdownEl) {
        const rect = inputEl.getBoundingClientRect();
        const viewportHeight = window.innerHeight;
        const dropdownHeight = 240;
        let top = rect.bottom + window.scrollY + 4;
        if (rect.bottom + dropdownHeight > viewportHeight - 20)
          top = rect.top + window.scrollY - dropdownHeight - 4;
        const isMobile = window.innerWidth <= 700;
        dropdownEl.style.top = `${Math.max(8, top)}px`;
        dropdownEl.style.left = isMobile
          ? "50%"
          : `${rect.left + window.scrollX}px`;
        dropdownEl.style.transform = isMobile ? "translateX(-50%)" : "none";
        dropdownEl.style.width = isMobile
          ? "calc(100vw - 32px)"
          : `${Math.min(rect.width, 500)}px`;
      }

      function createComboboxDynamic(container, options) {
        const {
          placeholder = "Ketik untuk mencari...",
          data = [],
          valueField = "id",
          labelField = "name",
          onSelect = null,
        } = options;
        container.innerHTML = `<input type="text" class="combobox-input" placeholder="${placeholder}" autocomplete="off" /><div class="combobox-dropdown"></div><input type="hidden" class="combobox-value" />`;
        const input = container.querySelector(".combobox-input");
        const dropdown = container.querySelector(".combobox-dropdown");
        const hidden = container.querySelector(".combobox-value");
        let items = data,
          highlightedIndex = -1;

        const render = (searchText = "") => {
          const search = searchText.toLowerCase();
          const filtered = items.filter(
            (item) =>
              String(item[labelField]).toLowerCase().includes(search) ||
              (item.code && item.code.toLowerCase().includes(search)) ||
              (item.barcode && String(item.barcode).includes(search)),
          );
          if (filtered.length === 0) {
            dropdown.innerHTML = `<div class="combobox-empty">Tidak ditemukan</div>`;
            dropdown.classList.add("show");
            return;
          }
          dropdown.innerHTML = filtered
            .map(
              (item, idx) =>
                `<div class="combobox-item ${idx === highlightedIndex ? "highlight" : ""}" data-id="${item[valueField]}" data-label="${item[labelField]}"><span>${item[labelField]}</span>${item.code ? `<span class="item-code">[${item.code}]</span>` : ""}</div>`,
            )
            .join("");
          dropdown.classList.add("show");
          dropdown.querySelectorAll(".combobox-item").forEach((el) => {
            el.addEventListener("click", () => {
              const id = el.dataset.id,
                label = el.dataset.label;
              input.value = label;
              hidden.value = id;
              dropdown.classList.remove("show");
              if (onSelect)
                onSelect({
                  id,
                  label,
                  item: items.find((i) => i[valueField] == id),
                });
            });
          });
        };
        const showDropdown = () => {
          render(input.value);
          positionDropdown(input, dropdown);
          activeCombobox = {
            container,
            input,
            dropdown,
            hidden,
            render,
            items,
            highlightedIndex,
          };
        };
        input.addEventListener("focus", showDropdown);
        input.addEventListener("input", () => {
          highlightedIndex = -1;
          render(input.value);
          positionDropdown(input, dropdown);
        });
        input.addEventListener("keydown", (e) => {
          if (!dropdown.classList.contains("show")) return;
          const itemsList = dropdown.querySelectorAll(".combobox-item");
          if (e.key === "ArrowDown") {
            e.preventDefault();
            highlightedIndex = Math.min(
              highlightedIndex + 1,
              itemsList.length - 1,
            );
            render(input.value);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            highlightedIndex = Math.max(highlightedIndex - 1, -1);
            render(input.value);
          } else if (e.key === "Enter") {
            e.preventDefault();
            if (highlightedIndex >= 0 && itemsList[highlightedIndex])
              itemsList[highlightedIndex].click();
          } else if (e.key === "Escape") dropdown.classList.remove("show");
        });
        document.addEventListener("click", (e) => {
          if (!container.contains(e.target)) dropdown.classList.remove("show");
        });
        window.addEventListener("resize", () => {
          if (activeCombobox?.dropdown?.classList.contains("show"))
            positionDropdown(activeCombobox.input, activeCombobox.dropdown);
        });
        return {
          setItems: (newItems) => {
            items = newItems;
            if (dropdown.classList.contains("show")) render(input.value);
          },
          getValue: () => hidden.value,
          clear: () => {
            hidden.value = "";
            input.value = "";
          },
        };
      }

      function createSupplierCombobox(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return null;
        container.innerHTML = `<input type="text" class="combobox-input" placeholder="Cari supplier..." autocomplete="off" /><div class="combobox-dropdown"></div><input type="hidden" class="combobox-value" />`;
        const input = container.querySelector(".combobox-input");
        const dropdown = container.querySelector(".combobox-dropdown");
        const hidden = container.querySelector(".combobox-value");
        let highlightedIndex = -1;

        const getFilteredSuppliers = (searchText = "") => {
          const search = normalizeComboboxText(searchText);
          return allSuppliers.filter((s) => {
            const name = normalizeComboboxText(s.name);
            const code = normalizeComboboxText(s.code);
            return (
              !search ||
              name.includes(search) ||
              (code && code.includes(search))
            );
          });
        };

        const selectSupplier = async (supplier) => {
          if (!supplier) return false;
          const nextId = parseInt(supplier.id);
          const prevId = hidden.value ? parseInt(hidden.value) : null;
          input.value = supplier.name;
          hidden.value = nextId;
          highlightedIndex = -1;
          dropdown.classList.remove("show");
          if (prevId !== nextId) await loadSupplierItems(nextId);
          return true;
        };

        const commitTypedSupplier = async () => {
          const typed = normalizeComboboxText(input.value);
          if (!typed) {
            hidden.value = "";
            return false;
          }

          const exactMatch = allSuppliers.find((s) => {
            const name = normalizeComboboxText(s.name);
            const code = normalizeComboboxText(s.code);
            return name === typed || (code && code === typed);
          });
          if (exactMatch) return selectSupplier(exactMatch);

          const filtered = getFilteredSuppliers(input.value);
          if (filtered.length === 1) return selectSupplier(filtered[0]);

          hidden.value = "";
          return false;
        };

        const render = (searchText = "") => {
          const filtered = getFilteredSuppliers(searchText);
          if (filtered.length === 0) {
            dropdown.innerHTML = `<div class="combobox-empty">Tidak ditemukan</div>`;
            dropdown.classList.add("show");
            return;
          }
          const visibleSuppliers = filtered.slice(0, 100);
          dropdown.innerHTML = visibleSuppliers
            .map(
              (s, idx) =>
                `<div class="combobox-item ${idx === highlightedIndex ? "highlight" : ""}" data-id="${s.id}" data-name="${s.name}">${s.name}</div>`,
            )
            .join("");
          if (filtered.length > visibleSuppliers.length) {
            dropdown.innerHTML += `<div class="combobox-empty">Menampilkan ${visibleSuppliers.length} dari ${filtered.length} supplier. Lanjutkan mengetik agar lebih spesifik.</div>`;
          }
          dropdown.classList.add("show");
          dropdown.querySelectorAll(".combobox-item").forEach((el) => {
            el.addEventListener("click", async () => {
              const id = parseInt(el.dataset.id);
              const supplier = allSuppliers.find((s) => s.id === id);
              await selectSupplier(supplier);
            });
          });
        };
        const showDropdown = () => {
          render(input.value);
          positionDropdown(input, dropdown);
          activeCombobox = { input, dropdown };
        };
        input.addEventListener("focus", showDropdown);
        input.addEventListener("input", () => {
          if (
            hidden.value &&
            normalizeComboboxText(input.value) !==
              normalizeComboboxText(
                allSuppliers.find((s) => s.id === parseInt(hidden.value))?.name,
              )
          ) {
            hidden.value = "";
          }
          highlightedIndex = -1;
          render(input.value);
          positionDropdown(input, dropdown);
        });
        input.addEventListener("keydown", (e) => {
          if (!dropdown.classList.contains("show")) return;
          const itemsList = dropdown.querySelectorAll(".combobox-item");
          if (e.key === "ArrowDown") {
            e.preventDefault();
            highlightedIndex = Math.min(
              highlightedIndex + 1,
              itemsList.length - 1,
            );
            render(input.value);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            highlightedIndex = Math.max(highlightedIndex - 1, -1);
            render(input.value);
          } else if (e.key === "Enter") {
            e.preventDefault();
            if (highlightedIndex >= 0 && itemsList[highlightedIndex]) {
              itemsList[highlightedIndex].click();
            } else {
              commitTypedSupplier();
            }
          } else if (e.key === "Escape") dropdown.classList.remove("show");
        });
        input.addEventListener("blur", () => {
          setTimeout(() => {
            if (!container.contains(document.activeElement)) {
              commitTypedSupplier();
              dropdown.classList.remove("show");
            }
          }, 120);
        });
        document.addEventListener("click", (e) => {
          if (!container.contains(e.target)) dropdown.classList.remove("show");
        });
        window.addEventListener("resize", () => {
          if (dropdown.classList.contains("show"))
            positionDropdown(input, dropdown);
        });
        return {
          getValue: () => (hidden.value ? parseInt(hidden.value) : null),
          clear: () => {
            hidden.value = "";
            input.value = "";
            currentSupplierItems = [];
          },
        };
      }

      async function loadSupplierItems(supplierId) {
        try {
          currentSupplierItems = await api(
            "GET",
            `/purchases/items/?supplier_id=${supplierId}`,
          );
          document
            .querySelectorAll("#barisContainer .item-combobox-container")
            .forEach((container) => {
              const combo = container._combobox;
              if (combo) combo.setItems(currentSupplierItems);
            });
          if (document.querySelectorAll("#barisContainer > div").length === 0)
            addBaris();
          updateScrollLogic();
        } catch (e) {
          showToast("Gagal memuat barang supplier: " + e.message, "error");
          currentSupplierItems = [];
        }
      }

      async function load() {
        const s = document.getElementById("fStart").value,
          e = document.getElementById("fEnd").value,
          st = document.getElementById("fStatus").value;
        const supId = document.getElementById("fSupplierFilter")?.value;
        let url = "/purchases/?limit=200";
        if (s) url += `&start_date=${s}`;
        if (e) url += `&end_date=${e}`;
        if (st) url += `&status=${st}`;
        if (supId) url += `&supplier_id=${supId}`;
        try {
          const d = await api("GET", url);
          const tbody = document.getElementById("tblBody");
          tbody.innerHTML = d.length
            ? d
                .map(
                  (p) => `<tr>
            <td style="font-family:monospace;font-size:13px;font-weight:700;color:var(--primary)">${p.number}</td>
            <td>${fmtDate(p.date)}</td>
            <td style="font-weight:600">${p.supplier?.name || "-"}</td>
            <td style="font-weight:700">${fmtRp(p.total)}</td>
            <td style="color:#10b981;font-weight:600">${fmtRp(p.paid)}</td>
            <td>${sb(p.status)}</td>
            
          <td style="white-space:nowrap">
              ${p.status === "draft" ? `<button class="bsm bp" style="padding:4px 8px;border-radius:6px;font-size:12px;cursor:pointer;background:var(--primary);color:#fff;border:none;" onclick="loadDraft(${p.id})">Proses</button>` : ''}
              <button class="bsm bo" style="padding:4px 8px;border-radius:6px;font-size:12px;cursor:pointer;background:transparent;border:1px solid #f59e0b;color:#f59e0b;" onclick="showDetail(${p.id})">Detail</button>
          </td>

          </tr>`,
                )
                .join("")
            : '<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--text-muted)">Tidak ada data</td></tr>';
        } catch (ex) {
          showToast(ex.message, "error");
        }
      }

      function updateScrollLogic() {
        const modal = document.getElementById("modalBuatContent");
        const tableWrapper = document.getElementById("tableResponsiveWrapper");
        if (!modal || !tableWrapper) return;
        const viewportWidth = window.innerWidth,
          contentWidth = tableWrapper.scrollWidth;
        if (contentWidth > viewportWidth - 40) {
          modal.style.width = "calc(100% - 32px)";
          modal.classList.add("scroll-enabled");
          tableWrapper.classList.add("scroll-enabled");
        } else {
          modal.style.width = "auto";
          modal.style.minWidth = "90%";
          modal.classList.remove("scroll-enabled");
          tableWrapper.classList.remove("scroll-enabled");
        }
      }

      function setupResizeObserver() {
        const tableWrapper = document.getElementById("tableResponsiveWrapper");
        if (!tableWrapper) return;
        if (resizeObserver) resizeObserver.disconnect();
        resizeObserver = new ResizeObserver(() => {
          clearTimeout(window._scrollLogicTimeout);
          window._scrollLogicTimeout = setTimeout(updateScrollLogic, 50);
        });
        resizeObserver.observe(tableWrapper);
      }

      

      function addBaris(existingItem = null) {
        const idx = barisIdx++;
        const container = document.createElement("div");
        container.className = "purchase-row";
        container.id = `baris${idx}`;
        const comboContainer = document.createElement("div");
        comboContainer.className = "combobox-container item-combobox-container";
        comboContainer.innerHTML = '<span class="field-label">Barang</span>';
        const qtyWrapper = document.createElement("div");
        qtyWrapper.className = "input-wrapper";
        qtyWrapper.innerHTML = `<span class="field-label">Qty</span><input type="text" inputmode="numeric" class="input-control qty-input" value="${existingItem ? existingItem.qty : 1}" min="1" style="margin:0;padding:10px 12px;font-size:14px;text-align:center">`;
        const qtyInp = qtyWrapper.querySelector("input");
        qtyInp.oninput = () => {
          const raw = parseNum(qtyInp.value),
            pos = qtyInp.selectionStart,
            oldLen = qtyInp.value.length;
          qtyInp.value = formatNum(raw);
          
          qtyInp.setSelectionRange(
            pos + (qtyInp.value.length - oldLen),
            pos + (qtyInp.value.length - oldLen),
          );
          if (currentFormMode === 'purchase') {
              const qti = container.querySelector(".qty-terima-input");
              if (qti) qti.value = qtyInp.value;
          }

          hitungTotal();
          updateScrollLogic();
        };

        
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

        const hargaWrapper = document.createElement("div");
        hargaWrapper.className = "input-wrapper";
        hargaWrapper.innerHTML = `<span class="field-label">Harga Beli</span><input type="text" inputmode="numeric" class="input-control baris-harga" value="0" style="margin:0;padding:10px 12px;font-size:14px;text-align:right">`;
        const hargaInp = hargaWrapper.querySelector("input");

        const marginWrapper = document.createElement("div");
        marginWrapper.className = "input-wrapper";
        marginWrapper.innerHTML = `<span class="field-label">Margin (%)</span><input type="number" step="0.01" class="input-control baris-margin" value="0" style="margin:0;padding:10px 12px;font-size:14px;text-align:center">`;
        const marginInp = marginWrapper.querySelector("input");

        const jualWrapper = document.createElement("div");
        jualWrapper.className = "input-wrapper";
        jualWrapper.innerHTML = `<span class="field-label">Harga Jual</span><input type="text" inputmode="numeric" class="input-control baris-jual" value="0" style="margin:0;padding:10px 12px;font-size:14px;text-align:right">`;
        const jualInp = jualWrapper.querySelector("input");

        const calcJual = () => {
          const hb = parseNum(hargaInp.value) || 0;
          const m = parseFloat(marginInp.value) || 0;
          const hj = hb + (hb * m) / 100;
          jualInp.value = formatNum(hj);
        };
        const calcMargin = () => {
          const hb = parseNum(hargaInp.value) || 0;
          const hj = parseNum(jualInp.value) || 0;
          if (hb > 0) {
            const m = ((hj - hb) / hb) * 100;
            marginInp.value = m.toFixed(2).replace(/\.00$/, "");
          } else {
            marginInp.value = "0";
          }
        };

        hargaInp.oninput = () => {
          const raw = parseNum(hargaInp.value),
            pos = hargaInp.selectionStart,
            oldLen = hargaInp.value.length;
          hargaInp.value = formatNum(raw);
          hargaInp.setSelectionRange(
            pos + (hargaInp.value.length - oldLen),
            pos + (hargaInp.value.length - oldLen),
          );
          calcJual();
          hitungTotal();
          updateScrollLogic();
        };

        marginInp.oninput = () => {
          calcJual();
          updateScrollLogic();
        };

        jualInp.oninput = () => {
          const raw = parseNum(jualInp.value),
            pos = jualInp.selectionStart,
            oldLen = jualInp.value.length;
          jualInp.value = formatNum(raw);
          jualInp.setSelectionRange(
            pos + (jualInp.value.length - oldLen),
            pos + (jualInp.value.length - oldLen),
          );
          calcMargin();
          updateScrollLogic();
        };

        const discWrapper = document.createElement("div");
        discWrapper.className = "input-wrapper diskon-wrapper";
        const addDiscBtn = document.createElement("button");
        addDiscBtn.type = "button";
        addDiscBtn.innerHTML = "＋";
        addDiscBtn.title = "Tambah Diskon";
        addDiscBtn.style.cssText =
          "height:34px;width:34px;border-radius:50%;border:none;background:var(--primary);color:#fff;font-weight:bold;cursor:pointer;flex-shrink:0;transition:transform 0.2s;";
        addDiscBtn.onmousedown = () =>
          (addDiscBtn.style.transform = "scale(0.85)");
        addDiscBtn.onmouseup = () => (addDiscBtn.style.transform = "scale(1)");
        addDiscBtn.onclick = () => {
          appendDiscountBlock(discWrapper, addDiscBtn);
          hitungTotal();
          updateScrollLogic();
        };

        const hargaPerQtyWrapper = document.createElement("div");
        hargaPerQtyWrapper.className = "input-wrapper";
        hargaPerQtyWrapper.innerHTML = `<span class="field-label">Harga/Qty</span><div class="baris-harga-per-qty">Rp 0</div>`;
        const nettoWrapper = document.createElement("div");
        nettoWrapper.className = "input-wrapper";
        nettoWrapper.innerHTML = `<span class="field-label">Total</span><div class="baris-netto">Rp 0</div>`;
        const delBtn = document.createElement("button");
        delBtn.innerHTML = "✕";
        delBtn.className = "btn-hapus-baris";
        delBtn.style.cssText =
          "width:36px;height:36px;border-radius:50%;background:rgba(239,68,68,.15);color:#ef4444;border:none;cursor:pointer;font-size:16px;font-weight:700;flex-shrink:0;display:flex;align-items:center;justify-content:center";
        delBtn.onclick = () => {
          container.remove();
          hitungTotal();
          updateScrollLogic();
        };

        container.appendChild(comboContainer);
        container.appendChild(qtyWrapper);
        container.appendChild(qtyTerimaWrapper);
        container.appendChild(hargaWrapper);
        container.appendChild(marginWrapper);
        container.appendChild(jualWrapper);
        container.appendChild(discWrapper);
        container.appendChild(hargaPerQtyWrapper);
        container.appendChild(nettoWrapper);
        container.appendChild(delBtn);
        document.getElementById("barisContainer").appendChild(container);
        discWrapper.appendChild(addDiscBtn);

        const combo = createComboboxDynamic(comboContainer, {
          placeholder: "Pilih barang...",
          data: currentSupplierItems,
          valueField: "id",
          labelField: "name",
          onSelect: (selected) => {
            if (selected.item) {
              const hb = selected.item.buy_price || 0;
              const hj = selected.item.sell_price || 0;
              let m = selected.item.profit_margin || 0;

              // Generate margin jika di database masih 0 tapi ada harga
              if (m === 0 && hb > 0 && hj > 0) {
                m = ((hj - hb) / hb) * 100;
              }

              container.querySelector(".baris-harga").value = formatNum(hb);
              container.querySelector(".baris-margin").value = m
                .toFixed(2)
                .replace(/\.00$/, "");
              container.querySelector(".baris-jual").value = formatNum(hj);
            }
            hitungTotal();
            updateScrollLogic();
          },
        });
        comboContainer._combobox = combo;
        setTimeout(updateScrollLogic, 50);
      }

      function appendDiscountBlock(container, btn) {
        const block = document.createElement("div");
        block.className = "disc-block";
        const inp = document.createElement("input");
        inp.type = "number";
        inp.className = "input-control baris-disc";
        inp.value = "0";
        inp.min = "0";
        inp.max = "100";
        inp.onclick = () => inp.select(); // Langsung select saat diklik
        inp.oninput = () => {
          hitungTotal();
          updateScrollLogic();
        };
        const pct = document.createElement("span");
        pct.textContent = "%";
        pct.style.cssText =
          "font-size:11px;font-weight:bold;color:var(--text-muted);flex-shrink:0;";
        const arrow = document.createElement("span");
        arrow.textContent = "→";
        arrow.style.cssText =
          "color:var(--primary);font-size:12px;flex-shrink:0;margin:0 2px;";
        const lblAfter = document.createElement("div");
        lblAfter.className = "after-disc-label";
        lblAfter.textContent = "Rp 0";
        const del = document.createElement("button");
        del.type = "button";
        del.innerHTML = "&times;";
        del.style.cssText =
          "background:none;border:none;color:#ef4444;cursor:pointer;font-size:24px;line-height:0.8;font-weight:bold;padding:0 2px;outline:none;flex-shrink:0;margin-left:4px;";
        del.title = "Hapus diskon ini";
        del.onclick = () => {
          block.classList.remove("show");
          setTimeout(() => {
            block.remove();
            hitungTotal();
            updateScrollLogic();
          }, 350);
        };

        block.appendChild(inp);
        block.appendChild(pct);
        block.appendChild(arrow);
        block.appendChild(lblAfter);
        block.appendChild(del);
        container.insertBefore(block, btn);
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            block.classList.add("show");
            inp.focus();
            inp.select();
            updateScrollLogic();
          });
        });
      }

      function hitungTotal() {
        const discGlobal =
          parseFloat(document.getElementById("bDisc").value) || 0;
        const taxGlobal =
          parseFloat(document.getElementById("bTax").value) || 0;
        let sub = 0;
        document
          .querySelectorAll("#barisContainer > .purchase-row")
          .forEach((row) => {
            const qtyInp = row.querySelector(".qty-input"),
              hargaInp = row.querySelector(".baris-harga"),
              hargaPerQtyLabel = row.querySelector(".baris-harga-per-qty"),
              nettoLabel = row.querySelector(".baris-netto");
            
            if (qtyInp && hargaInp) {
              let qtyDipesan = parseNum(qtyInp.value) || 0;
              let qtyTerima = row.querySelector(".qty-terima-input") ? parseNum(row.querySelector(".qty-terima-input").value) : qtyDipesan;
              let qtyCalc = currentFormMode === 'purchase' ? qtyTerima : qtyDipesan;
              
              let hargaAwal = parseNum(hargaInp.value) || 0,

                hargaSekarang = hargaAwal;
              row.querySelectorAll(".disc-block").forEach((block) => {
                const discVal =
                  parseFloat(block.querySelector(".baris-disc").value) || 0;
                hargaSekarang = hargaSekarang * (1 - discVal / 100);
                const labelAfter = block.querySelector(".after-disc-label");
                if (labelAfter) labelAfter.textContent = fmtRp(hargaSekarang);
              });
              const barisTotal = hargaSekarang * qtyCalc;
              sub += barisTotal;
              if (hargaPerQtyLabel)
                hargaPerQtyLabel.textContent = fmtRp(hargaSekarang);
              if (nettoLabel) nettoLabel.textContent = fmtRp(barisTotal);
            }
          });
        const discAmt = sub * (discGlobal / 100),
          taxAmt = (sub - discAmt) * (taxGlobal / 100);
        document.getElementById("totalLabel").textContent = fmtRp(
          sub - discAmt + taxAmt,
        );
      }

      
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
          let msg = "Deteksi perubahan harga beli pada barang berikut:\n";
          priceChanges.forEach((c) => { msg += `- ${c.name}: ${fmtRp(c.old)} ➜ ${fmtRp(c.new)}\n`; });
          msg += "\nSimpan dengan harga baru ini?";
          if (!(await showConfirm(msg))) return;
        }
        
        let createNewPO = false;
        if (hasMissingItems) {
            if (await showConfirm("Ada barang yang kurang dari pesanan (Qty Diterima < Qty Dipesan).\n\nApakah Anda ingin membuat DRAFT PESANAN BARU otomatis untuk sisa barang yang kurang?")) {
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


      async function showDetail(id) {
        try {
          const p = await api("GET", `/purchases/${id}`);
          document.getElementById("mDetailTitle").textContent =
            `Faktur ${p.number}`;
          document.getElementById("mDetailBody").innerHTML =
            `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px">
            <div><div style="font-size:13px;color:var(--text-muted)">Tanggal</div><div style="font-weight:700">${fmtDate(p.date)}</div></div>
            <div><div style="font-size:13px;color:var(--text-muted)">Supplier</div><div style="font-weight:700">${p.supplier?.name || "-"}</div></div>
            <div><div style="font-size:13px;color:var(--text-muted)">Status</div>${sb(p.status)}</div>
            <div><div style="font-size:13px;color:var(--text-muted)">Catatan</div><div>${p.notes || "-"}</div></div>
          </div>
          <table class="tbl" style="margin-bottom:16px">
            <thead><tr><th>Barang</th><th style="text-align:right">Qty</th><th style="text-align:right">Harga Beli</th><th style="text-align:right">Total Disc%</th><th style="text-align:right">Total</th></tr></thead>
            <tbody>${(p.items || []).map((i) => `<tr><td>${i.item?.name || "-"}</td><td style="text-align:right">${i.qty}</td><td style="text-align:right">${fmtRp(i.buy_price)}</td><td style="text-align:right">${i.discount || 0}%</td><td style="text-align:right;font-weight:700">${fmtRp(i.total)}</td></tr>`).join("")}</tbody>
          </table>
          <div style="background:var(--bg-color);border-radius:12px;padding:16px">
            <div style="display:flex;justify-content:space-between;margin-bottom:8px"><span>Subtotal</span><span>${fmtRp(p.subtotal)}</span></div>
            ${p.discount > 0 ? `<div style="display:flex;justify-content:space-between;margin-bottom:8px"><span>Diskon Global</span><span>-${fmtRp(p.subtotal * (p.discount / 100))}</span></div>` : ""}
            ${p.tax > 0 ? `<div style="display:flex;justify-content:space-between;margin-bottom:8px"><span>PPN</span><span>+${fmtRp((p.subtotal - p.subtotal * (p.discount / 100)) * (p.tax / 100))}</span></div>` : ""}
            <div style="display:flex;justify-content:space-between;font-size:20px;font-weight:900;border-top:1px solid var(--border-color);padding-top:10px;margin-top:6px"><span>TOTAL</span><span style="color:var(--primary)">${fmtRp(p.total)}</span></div>
            <div style="display:flex;justify-content:space-between;margin-top:8px"><span>Sudah Dibayar</span><span style="color:#10b981;font-weight:700">${fmtRp(p.paid)}</span></div>
            <div style="display:flex;justify-content:space-between;margin-top:4px;font-weight:800;color:#ef4444"><span>Sisa Hutang</span><span>${fmtRp(p.total - p.paid)}</span></div>
          </div>
          <div style="margin-top:16px;display:flex;gap:12px">
            ${p.status !== "paid" ? `<button onclick="openBayar(${p.id},${p.total},${p.paid})" class="btn btn-primary">💸 Bayar Hutang</button>` : ""}
            <button onclick="hapus(${p.id})" class="btn btn-danger">🗑 Batalkan</button>
          </div>`;
          openModal("mDetail");
        } catch (ex) {
          showToast(ex.message, "error");
        }
      }

      async function openBayar(id, total, paid) {
        closeModal("mDetail");
        document.getElementById("bayarId").value = id;
        const sisa = total - paid;
        document.getElementById("bayarRemaining").value = sisa;
        document.getElementById("bayarInfo").innerHTML =
          `<div style="display:flex;justify-content:space-between;margin-bottom:6px"><span>Total Tagihan</span><b>${fmtRp(total)}</b></div><div style="display:flex;justify-content:space-between;margin-bottom:6px"><span>Sudah Dibayar</span><b style="color:#10b981">${fmtRp(paid)}</b></div><div style="display:flex;justify-content:space-between;font-size:18px;font-weight:800;border-top:1px solid var(--border-color);padding-top:8px"><span>Sisa Hutang</span><b style="color:#ef4444">${fmtRp(sisa)}</b></div>`;
        await loadLiquidBalances();
        document.getElementById("bayarMethod").value = "cash";
        applyBayarMethod(true);
        document.getElementById("bayarNote").value = "";
        setTimeout(() => openModal("mBayar"), 250);
      }

      async function doBayar() {
        const id = document.getElementById("bayarId").value;
        const method = document.getElementById("bayarMethod").value;
        const cashAmt =
          method === "bank"
            ? 0
            : parseNum(document.getElementById("bayarCashAmt").value) || 0;
        const bankAmt =
          method === "cash"
            ? 0
            : parseNum(document.getElementById("bayarBankAmt").value) || 0;
        const amt = cashAmt + bankAmt;
        if (!amt)
          return showToast("Isi pembayaran kas, bank, atau keduanya", "error");
        try {
          showLoading("Memproses...");
          await api("POST", `/purchases/${id}/pay`, {
            amount: amt,
            cash_amount: cashAmt,
            bank_amount: bankAmt,
            notes: document.getElementById("bayarNote").value,
          });
          hideLoading();
          showToast("Pembayaran berhasil ✓");
          closeModal("mBayar");
          load();
        } catch (ex) {
          hideLoading();
          if (ex.message.includes("Saldo kas tidak cukup")) {
            if (
              await showConfirm(
                `${ex.message}\n\nBuka Buku Kas untuk catat setoran modal / dana pemodal sekarang?`,
              )
            ) {
              goToFundingKas();
              return;
            }
          } else if (ex.message.includes("Saldo bank tidak cukup")) {
            showToast(
              `${ex.message} Lakukan setoran/transfer ke rekening usaha lebih dulu atau ubah metode ke gabungan.`,
              "error",
            );
            return;
          }
          showToast(ex.message, "error");
        }
      }

      async function hapus(id) {
        if (
          !(await showConfirm(
            "Batalkan pembelian ini? Stok akan dikurangi kembali.",
          ))
        )
          return;
        try {
          showLoading();
          await api("POST", `/purchases/${id}/cancel`);
          hideLoading();
          showToast("Dibatalkan ✓");
          closeModal("mDetail");
          load();
        } catch (ex) {
          hideLoading();
          showToast(ex.message, "error");
        }
      }

      window.addEventListener("resize", () => {
        clearTimeout(window._scrollLogicTimeout);
        window._scrollLogicTimeout = setTimeout(updateScrollLogic, 100);
      });
      const originalCloseModal = closeModal;
      closeModal = function (modalId) {
        if (modalId === "mBuat" && resizeObserver) {
          resizeObserver.disconnect();
          resizeObserver = null;
        }
        originalCloseModal(modalId);
      };
      document.getElementById("fStart").value = today().slice(0, 7) + "-01";
      document.getElementById("fEnd").value = today();

      (async () => {
        try {
          allSuppliers = await api("GET", "/suppliers/?limit=5000&active_only=true");
          const fSup = document.getElementById("fSupplierFilter");
          if (fSup) {
              fSup.innerHTML = '<option value="">Semua Supplier</option>' + allSuppliers.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
          }
          load();
        } catch (e) {
          console.error(e);
        }
      })();
    