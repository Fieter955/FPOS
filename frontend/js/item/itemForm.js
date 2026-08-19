      // Load Components — dipanggil saat idle (lihat init di bawah) agar tidak berebut
      // bandwidth dengan daftar barang saat pertama buka. Komponen ini (modal & tab)
      // baru dibutuhkan saat user klik Tambah/Edit atau pindah tab.
      const components = [
        { url: "/item/popUp.html", id: "popUpContainer" },
        { url: "/item/kategori.html", id: "katContainer" },
        { url: "/item/merek.html", id: "merekContainer" },
        { url: "/item/units.html", id: "unitsContainer" },
      ];

      // Memoized: fetch markup modal/tab HANYA sekali. Kembalikan Promise agar pemanggil
      // (mis. "Buat Barang Baru") bisa `await` sampai form benar-benar siap, lalu buka
      // otomatis — tanpa perlu klik dua kali.
      let componentsPromise = null;
      function loadComponents() {
        if (componentsPromise) return componentsPromise;
        componentsPromise = Promise.all(
          components.map((c) =>
            fetch(c.url)
              .then((r) => r.text())
              .then((html) => {
                document.getElementById(c.id).innerHTML = html;
                // Setelah markup modal ada di DOM: pasang listener pencarian supplier &
                // isi dropdown grup diskon (jika data grup sudah lebih dulu termuat).
                if (c.id === "popUpContainer") {
                  setupSupSearch();
                  setupItemFormKeyboardNavigation();
                  setupAdvancedDeleteModal();
                  populateDiscountGroupSelect();
                  // Modal baru saja masuk DOM: isi fKat/fMerek/fSat yang belum
                  // sempat terisi saat refreshSelects() awal (pakai cache, tanpa request baru).
                  refreshSelects();
                }
              }),
          ),
        );
        return componentsPromise;
      }

      let currentAdvancedType = null;
      let currentPotonganType = null;

      // Cache markup tabel harga lanjutan (Satuan/Level Harga/Level Jumlah/Potongan).
      // Template ini STATIS — cukup diunduh dari server sekali per sesi, lalu dipakai ulang
      // dari memori. Sebelumnya tiap klik mengunduh ulang (pakai cache-buster ?_t=), jadi
      // terasa "loading" tiap kali. Sekarang klik ke-2 dst langsung instan.
      const advTemplateCache = {};
      // Cache markup tabel yang SUDAH di-ekstrak (siap tempel) → DOMParser cukup sekali per tipe.
      const advHtmlCache = {};

      // Hangatkan cache di latar belakang (saat idle) supaya klik tab PERTAMA pun instan.
      // Fire-and-forget & aman diulang (yang sudah ada dilewati).
      function prefetchAdvancedTemplates() {
        ["satuan", "levelHarga", "levelJumlah", "potonganHargaJual"].forEach(
          (type) => {
            if (advTemplateCache[type] != null) return;
            fetch(`/item/${type}.html`)
              .then((r) => (r.ok ? r.text() : null))
              .then((t) => {
                if (t != null) advTemplateCache[type] = t;
              })
              .catch(() => {});
          },
        );
      }

      // Warnai tombol tab harga lanjutan secara SINKRON (instan saat diklik), tanpa
      // menunggu tabel selesai dirender. type=null → tak ada yang aktif (semua abu).
      function setAdvBtnActive(type, isPotongan) {
        const typeMap = {
          levelHarga: "advharga",
          levelJumlah: "advjumlah",
          satuan: "advsatuan",
        };
        document.querySelectorAll(".adv-btn").forEach((b) => {
          const btnId = b.id.toLowerCase();
          const isPotBtn = btnId.includes("potonganhargajual");
          // Hanya sentuh tombol di kelompok yang sama (potongan vs level/satuan).
          if (isPotongan !== isPotBtn) return;
          let isCurrent = false;
          if (type)
            isCurrent = isPotongan
              ? true // hanya ada satu tombol potongan
              : btnId.includes(typeMap[type] || type.toLowerCase());
          b.style.background = isCurrent ? "var(--primary)" : "var(--card-bg)";
          b.style.color = isCurrent ? "#fff" : "var(--text-main)";
          b.style.borderColor = isCurrent
            ? "var(--primary)"
            : "var(--border-color)";
        });
      }

      async function loadAdvancedContent(type) {
        const isPotongan = type === "potonganHargaJual";
        const targetContainer = document.getElementById(
          isPotongan ? "contentPotonganHargaJual" : "advancedContent",
        );
        if (!targetContainer) return;
        const relevantState = isPotongan
          ? currentPotonganType
          : currentAdvancedType;

        // Jika tombol sumber sudah disabled, jangan lakukan apa-apa.
        const sourceBtn = document.getElementById(
          isPotongan
            ? "btnAdvPotonganHargaJual"
            : "btnAdv" +
                (type === "levelHarga"
                  ? "Harga"
                  : type === "levelJumlah"
                    ? "Jumlah"
                    : "Satuan"),
        );
        if (sourceBtn && sourceBtn.classList.contains("disabled")) return;

        // Toggle: klik tipe yang sama → tutup panel & kembalikan tombol ke abu.
        if (relevantState === type) {
          targetContainer.style.display = "none";
          if (isPotongan) currentPotonganType = null;
          else currentAdvancedType = null;
          setAdvBtnActive(null, isPotongan);
          return;
        }

        // 1) FEEDBACK INSTAN: set state + warnai tombol jadi oranye LEBIH DULU,
        //    sebelum kerja berat (parse + render tabel).
        if (isPotongan) currentPotonganType = type;
        else currentAdvancedType = type;
        setAdvBtnActive(type, isPotongan);

        // 2) Beri browser 1 frame untuk MENGGAMBAR tombol oranye dulu, baru render
        //    tabel. Tanpa jeda ini semuanya jalan sekaligus (memblokir layar), jadi
        //    tombol baru berubah warna setelah tabel selesai → terasa nge-lag.
        await new Promise((r) =>
          requestAnimationFrame(() => requestAnimationFrame(r)),
        );

        try {
          // Markup tabel di-parse & di-ekstrak SEKALI per tipe lalu di-cache. <style>
          // template disuntik ke <head> sekali saja (hindari re-parse CSS tiap klik).
          let contentHtml = advHtmlCache[type];
          if (contentHtml == null) {
            let text = advTemplateCache[type];
            if (text == null) {
              const res = await fetch(`/item/${type}.html`);
              if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);
              text = await res.text();
              advTemplateCache[type] = text;
            }
            const doc = new DOMParser().parseFromString(text, "text/html");
            const tableWrap = doc.querySelector(".tbl-wrap");
            if (!tableWrap)
              throw new Error(
                "Elemen .tbl-wrap tidak ditemukan di " + type + ".html",
              );
            doc.querySelectorAll("style").forEach((style, i) => {
              const sid = `advStyle-${type}-${i}`;
              if (!document.getElementById(sid)) {
                const s = document.createElement("style");
                s.id = sid;
                s.textContent = style.textContent;
                document.head.appendChild(s);
              }
            });
            contentHtml = `<div class="advanced-container">${tableWrap.outerHTML}</div>`;
            advHtmlCache[type] = contentHtml;
          }

          targetContainer.innerHTML = contentHtml;
          targetContainer.style.display = "block";

          if (!isPotongan) {
            ensureEmptySatuanRow();
            renderAdvancedGrid();
          } else {
            // Pastikan daftar grup pelanggan sudah dimuat SEBELUM grid dirender, agar
            // <select> grup terisi & baris potongan tersimpan tidak hilang saat dibaca ulang.
            if (!allGroups.length) await loadCustomerGroups();
            initPotonganHargaJualUI();
          }
        } catch (e) {
          console.error("loadAdvancedContent Error:", e);
          showToast("Gagal memuat: " + e.message, "error");
          // Batalkan state & tombol bila gagal agar tetap konsisten.
          if (isPotongan) currentPotonganType = null;
          else currentAdvancedType = null;
          setAdvBtnActive(null, isPotongan);
        }
      }

      requireAuth();
      let editItemId = null,
        fotoBaru = null, // Blob WebP hasil kecilkanGambar, menunggu di-upload saat simpan
        hapusFotoFlag = false, // true = user minta hapus foto (panggil DELETE /image saat simpan)
        editKatId = null,
        editMerekId = null,
        editSatId = null,
        fromItemModal = false,
        curTab = "brg",
        allGroups = [],
        groupPrices = {}, // { groupName: price }
        initialGroupPrices = {},
        initialBuyPrice = 0,
        initialSellPrice = 0,
        supplierSettingsMap = {},
        generalBuyPrice = 0,
        generalBarcode = "",
        generalPpnType = "none",
        generalPpnPercent = 0,
        sharedFieldNotified = false,
        currentSupplierContext = "",
        satuanRows = [],
        advancedDeleteModalResolver = null,
        advancedDeleteModalReturnFocus = null,
        masterUnits = [],
        groupDiscounts = [], // Potongan Harga Jual per grup: [{group_id, disc1..disc4}]
        lastPpnStatus = "none";

      function formatInputRibuan(input) {
        if (typeof formatDesimal === "function") {
          formatDesimal(input);
        } else {
          let val = input.value.replace(/[^0-9]/g, "");
          input.value = val === "" ? "0" : parseInt(val).toLocaleString("id-ID");
        }
      }

      function toRibuan(num) {
        if (typeof toDesimal === "function") return toDesimal(num);
        if (!num) return "0";
        return parseInt(num).toLocaleString("id-ID");
      }

      function toAngka(str) {
        if (typeof parseDesimal === "function") return parseDesimal(str);
        if (!str) return 0;
        return parseFloat(str.toString().replace(/\./g, "")) || 0;
      }

      function parseInputPersen(value) {
        return typeof parseDesimal === "function"
          ? parseDesimal(value)
          : Number.parseFloat(String(value).replace(",", ".")) || 0;
      }

      function toPersen(value) {
        return typeof toDesimal === "function"
          ? toDesimal(value)
          : parseInputPersen(value).toFixed(2).replace(".", ",");
      }

      function formatInputPersen(input) {
        if (typeof formatDesimal === "function") formatDesimal(input);
      }

      let barcodeBuffer = "";
      let lastKeyTime = Date.now();

      const ITEM_FORM_CONTROL_SELECTOR = [
        "input:not([type='hidden']):not([disabled])",
        "select:not([disabled])",
        "textarea:not([disabled])",
        "[contenteditable='true']",
        "[data-item-photo-focus]:not([aria-disabled='true'])",
        "[data-item-advanced-toggle]:not([disabled])",
        "button[type='submit']:not([disabled])",
      ].join(",");

      function getItemFormControls(root) {
        return Array.from(root.querySelectorAll(ITEM_FORM_CONTROL_SELECTOR)).filter(
          (control) => {
            if (control.getAttribute("tabindex") === "-1") return false;
            const style = window.getComputedStyle(control);
            return (
              style.display !== "none" &&
              style.visibility !== "hidden" &&
              control.getClientRects().length > 0
            );
          },
        );
      }

      function focusItemFormControl(control) {
        if (!control) return false;
        control.focus();
        if (
          control instanceof HTMLInputElement &&
          ["", "text", "number", "search", "tel", "url", "email"].includes(
            control.type,
          )
        )
          control.select();
        return true;
      }

      function openItemPhotoPicker() {
        document.getElementById("fFotoInput")?.click();
      }

      function isPrintableItemFormKey(event) {
        return (
          typeof event.key === "string" &&
          event.key.length === 1 &&
          !event.ctrlKey &&
          !event.altKey &&
          !event.metaKey
        );
      }

      function openNativeSelectPicker(select) {
        if (
          !(select instanceof HTMLSelectElement) ||
          select.disabled ||
          select.getClientRects().length === 0
        )
          return;

        try {
          if (typeof select.showPicker === "function") {
            select.showPicker();
            return;
          }
        } catch (_) {
          // Browser dapat menolak showPicker() pada kondisi tertentu. Lanjutkan
          // ke fallback click() agar tetap berfungsi di browser lama/berbeda.
        }

        try {
          select.click();
        } catch (_) {}
      }

      function moveItemFormFocus(form, target, direction) {
        const controls = getItemFormControls(form);
        const currentIndex = controls.indexOf(target);
        if (currentIndex < 0) return false;
        return focusItemFormControl(controls[currentIndex + direction]);
      }

      function jumpItemFormSection(form, target, direction) {
        const currentSection = target.closest("[data-item-keyboard-section]");
        if (!currentSection) return false;

        const sections = Array.from(
          form.querySelectorAll("[data-item-keyboard-section]"),
        ).filter((section) => getItemFormControls(section).length > 0);
        const currentIndex = sections.indexOf(currentSection);
        if (currentIndex < 0) return false;

        const destinationSection = sections[currentIndex + direction];
        if (!destinationSection) return false;
        return focusItemFormControl(getItemFormControls(destinationSection)[0]);
      }

      function isCaretAtArrowBoundary(target, key) {
        if (!(target instanceof HTMLInputElement)) return false;
        if (typeof target.selectionStart !== "number") return false;
        if (target.selectionStart !== target.selectionEnd) {
          // Fokus hasil Enter menyeleksi seluruh nilai. Dalam kondisi ini panah dapat
          // langsung dipakai untuk lanjut/mundur; seleksi sebagian tetap diedit normal.
          return (
            target.selectionStart === 0 &&
            target.selectionEnd === target.value.length
          );
        }
        return key === "ArrowLeft"
          ? target.selectionStart === 0
          : target.selectionEnd === target.value.length;
      }

      function setupItemFormKeyboardNavigation() {
        const form = document.getElementById("fBarang");
        if (!form || form.dataset.keyboardNavigationReady === "true") return;

        form.dataset.keyboardNavigationReady = "true";
        setupAdvancedGridKeyboardNavigation();
        form.addEventListener("keydown", handleItemFormKeyboardNavigation);
      }

      const ADVANCED_GRID_CONTROL_SELECTOR =
        "input:not([type='hidden']):not([disabled]), select:not([disabled]), textarea:not([disabled])";

      function getAdvancedGridBody(target) {
        return target?.closest?.("#advancedContent tbody") || null;
      }

      function getAdvancedGridControls(row) {
        return row
          ? Array.from(row.querySelectorAll(ADVANCED_GRID_CONTROL_SELECTOR))
          : [];
      }

      function getAdvancedGridControlLocation(control) {
        const row = control?.closest?.(
          "#advancedContent tbody tr[data-advanced-row]",
        );
        const cell = control?.closest?.("td");
        if (!row || !cell) return null;

        const cellControls = Array.from(
          cell.querySelectorAll(ADVANCED_GRID_CONTROL_SELECTOR),
        );
        const cellIndex = Array.from(row.cells).indexOf(cell);
        const controlIndex = cellControls.indexOf(control);
        if (cellIndex < 0 || controlIndex < 0) return null;

        const rows = Array.from(
          row.parentElement?.querySelectorAll("tr[data-advanced-row]") || [],
        );
        return {
          rowKey: row.dataset.advancedRow ?? "",
          rowIndex: rows.indexOf(row),
          cellIndex,
          controlIndex,
        };
      }

      function getAdvancedGridCellControl(row, cellIndex, controlIndex = 0) {
        const cell = row?.cells?.[cellIndex];
        if (!cell) return null;
        return (
          Array.from(
            cell.querySelectorAll(ADVANCED_GRID_CONTROL_SELECTOR),
          )[controlIndex] || null
        );
      }

      function findAdvancedGridRow(body, location) {
        const rows = Array.from(
          body?.querySelectorAll("tr[data-advanced-row]") || [],
        );
        return (
          rows.find((row) => row.dataset.advancedRow === location.rowKey) ||
          rows[location.rowIndex] ||
          null
        );
      }

      function focusAdvancedGridLocation(body, location) {
        const row = findAdvancedGridRow(body, location);
        if (!row) return false;

        const control = getAdvancedGridCellControl(
          row,
          location.cellIndex,
          location.controlIndex,
        );
        if (!control) return false;
        return focusItemFormControl(control);
      }

      function queueAdvancedGridFocus(body, target, destination) {
        // Moving focus immediately can fire the current input's onchange. That
        // callback rerenders the grid and detaches the destination element. Blur
        // first, then resolve the destination from the freshly rendered row/cell.
        if (document.activeElement === target) target.blur();

        const restore = () => focusAdvancedGridLocation(body, destination);
        if (typeof requestAnimationFrame === "function") requestAnimationFrame(restore);
        else setTimeout(restore, 0);
      }

      function handleAdvancedGridKeyboardNavigation(event) {
        if (event.defaultPrevented || event.isComposing) return;

        const target = event.target;
        if (
          !(target instanceof HTMLElement) ||
          !target.matches(ADVANCED_GRID_CONTROL_SELECTOR)
        )
          return;

        const body = getAdvancedGridBody(target);
        const location = getAdvancedGridControlLocation(target);
        if (!body || !location) return;

        if (event.key === "ArrowUp" || event.key === "ArrowDown") {
          // Panah atas/bawah pada select tetap dipakai untuk memilih opsi satuan.
          if (
            target instanceof HTMLSelectElement ||
            event.shiftKey ||
            event.ctrlKey ||
            event.altKey ||
            event.metaKey
          )
            return;

          const rows = Array.from(
            body.querySelectorAll("tr[data-advanced-row]"),
          );
          const row = rows[location.rowIndex];
          const destinationRow = rows[
            location.rowIndex + (event.key === "ArrowDown" ? 1 : -1)
          ];
          const destination = destinationRow
            ? getAdvancedGridCellControl(
                destinationRow,
                location.cellIndex,
                location.controlIndex,
              )
            : null;
          if (!row || !destination) return;

          event.preventDefault();
          event.stopPropagation();
          queueAdvancedGridFocus(
            body,
            target,
            getAdvancedGridControlLocation(destination),
          );
          return;
        }

        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey)
          return;

        // In a text input, retain native caret movement until the caret reaches
        // the edge. A select has no text caret, so its horizontal arrows always
        // navigate to the adjacent grid field.
        if (
          target instanceof HTMLInputElement &&
          !isCaretAtArrowBoundary(target, event.key)
        )
          return;

        const controls = getAdvancedGridControls(
          target.closest("tr[data-advanced-row]"),
        );
        const currentIndex = controls.indexOf(target);
        const destination =
          currentIndex < 0
            ? null
            : controls[currentIndex + (event.key === "ArrowRight" ? 1 : -1)];

        // Keep the event inside the grid at its horizontal edge instead of
        // falling through to the form-wide navigation and losing the grid focus.
        event.preventDefault();
        event.stopPropagation();
        if (destination) {
          queueAdvancedGridFocus(
            body,
            target,
            getAdvancedGridControlLocation(destination),
          );
        }
      }

      function setupAdvancedGridKeyboardNavigation() {
        const container = document.getElementById("advancedContent");
        if (!container || container.dataset.keyboardNavigationReady === "true")
          return;

        container.dataset.keyboardNavigationReady = "true";
        container.addEventListener(
          "keydown",
          handleAdvancedGridKeyboardNavigation,
          true,
        );
      }

      function handleItemFormKeyboardNavigation(e) {
        const target = e.target;
        const isPhotoFocus =
          target instanceof HTMLElement &&
          target.matches("[data-item-photo-focus]");
        const isAdvancedToggle =
          target instanceof HTMLElement &&
          target.matches("[data-item-advanced-toggle]");
        const isPrintableKey = isPrintableItemFormKey(e);

        if (
          (!["Enter", "ArrowLeft", "ArrowRight"].includes(e.key) &&
            !(e.key === " " && isPhotoFocus) &&
            !(isPrintableKey && (target instanceof HTMLSelectElement || isAdvancedToggle))) ||
          e.isComposing ||
          e.defaultPrevented
        )
          return;

        if (
          !(target instanceof HTMLElement) ||
          !target.matches(
            "input, select, textarea, [contenteditable='true'], [data-item-photo-focus], [data-item-advanced-toggle]",
          )
        )
          return;

        const form = target.closest("form");
        if (!form) return;

        if (target instanceof HTMLSelectElement && isPrintableKey) {
          openNativeSelectPicker(target);
          return;
        }

        if (
          isAdvancedToggle &&
          (e.key === "Enter" || e.key === " " || (isPrintableKey && !e.repeat))
        ) {
          e.preventDefault();
          e.stopPropagation();
          target.click();
          return;
        }

        if (isPhotoFocus && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          e.stopPropagation();
          openItemPhotoPicker();
          return;
        }

        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          // Shift+panah tetap untuk menyeleksi teks; Alt/Meta tidak diambil alih.
          if (e.shiftKey || e.altKey || e.metaKey) return;

          const direction = e.key === "ArrowRight" ? 1 : -1;
          const moved = e.ctrlKey
            ? jumpItemFormSection(form, target, direction)
            : isPhotoFocus
              ? moveItemFormFocus(form, target, direction)
              : isCaretAtArrowBoundary(target, e.key) &&
                moveItemFormFocus(form, target, direction);
          if (!moved) return;

          e.preventDefault();
          e.stopPropagation();
          return;
        }

        // Shift+Enter tetap membuat baris baru di Catatan Tambahan.
        if (target.matches("textarea") && e.shiftKey) return;

        // Scanner mengirim rangkaian tombol dengan sangat cepat lalu Enter. Biarkan
        // listener scanner di document menyelesaikan scan dan menahan submit form.
        if (target.id === "fBarcode" && barcodeBuffer.length > 2) return;

        e.preventDefault();
        e.stopPropagation();

        // Pertahankan kalkulasi margin sebelum fokus berpindah ke Harga Jual.
        if (target.id === "fMargin") onMarginInput();
        if (target.id === "fBarcode") barcodeBuffer = "";
        moveItemFormFocus(form, target, e.shiftKey ? -1 : 1);
      }

      document.addEventListener("keydown", (e) => {
        const modal = document.getElementById("mBarang");
        if (modal && modal.style.display === "flex") {
          const currentTime = Date.now();
          const target = e.target;
          const isEditableTarget =
            target instanceof HTMLElement &&
            (target.matches("input, textarea, select") ||
              target.isContentEditable);

          // Input form biasa tidak boleh ikut membentuk buffer scanner. fBarcode
          // dikecualikan agar scan di kolom barcode tetap menahan submit dari Enter.
          if (isEditableTarget && target.id !== "fBarcode") {
            barcodeBuffer = "";
            lastKeyTime = currentTime;
            return;
          }
          if (currentTime - lastKeyTime > 50) barcodeBuffer = "";
          if (e.key === "Enter" && barcodeBuffer.length > 2) {
            e.preventDefault();
            document.getElementById("autoBarcode").checked = false;
            toggleBarcodeUI();
            document.getElementById("fBarcode").value = barcodeBuffer;
            showToast("Barcode terscan otomatis: " + barcodeBuffer, "success");
            barcodeBuffer = "";
            return;
          } else if (e.key.length === 1) barcodeBuffer += e.key;
          lastKeyTime = currentTime;
        }
      });

      function toggleBarcodeUI() {
        const autoEl = document.getElementById("autoBarcode");
        const barcodeEl = document.getElementById("fBarcode");
        const scannerEl = document.getElementById("lblScanner");
        if (barcodeEl) barcodeEl.style.display = "block";
        if (scannerEl && autoEl)
          scannerEl.style.display = autoEl.checked ? "block" : "none";
      }

      function togglePpnPercent() {
        const ppnSelect = document.getElementById("fPpn");
        const ppnGroup = document.getElementById("ppnPercentGroup");
        const hBeliEl = document.getElementById("fHBeli");
        const ppnInput = document.getElementById("fPpnPercent");
        if (!(ppnSelect && ppnGroup && hBeliEl && ppnInput)) return;

        const currentPpnStatus = ppnSelect.value;

        // Mode "none" (Tanpa PPN): barang bebas PPN → tarif tak relevan. Sembunyikan
        // field tarif, jangan konversi harga modal, dan keluar lebih awal.
        if (currentPpnStatus === "none") {
          ppnGroup.style.display = "none";
          lastPpnStatus = "none";
          updatePpnModeHint();
          updatePpnBreakdown();
          return;
        }

        // Tarif PPN SELALU tampil (apa pun modenya): pengguna tetap mengisi berapa persen
        // PPN-nya, baik harga modal sudah maupun belum termasuk PPN.
        ppnGroup.style.display = "";

        // Default tarif bila kosong: ikut supplier terpilih → tarif toko → fallback 11.
        if (!(parseDesimal(ppnInput) > 0)) {
          const contextVal = document.getElementById("fSupplierContext")?.value;
          let targetPpn = PKP_ITEM.tarif > 0 ? PKP_ITEM.tarif : 11;
          if (contextVal && contextVal !== "") {
            const supplierData = allSups.find(
              (sup) => String(sup.id) === String(contextVal),
            );
            if (supplierData && supplierData.PpnSupplier != null)
              targetPpn = supplierData.PpnSupplier;
          }
          ppnInput.value = targetPpn;
        }

        // Konversi angka Harga Modal saat ganti mode agar NILAI EKONOMIS (total bayar ke
        // supplier) TETAP. Konvensi standar — sinkron dengan grid pembelian (components.js):
        //   included = angka SUDAH termasuk PPN (gross) · excluded = angka BELUM termasuk (net).
        const ppnPercent = parseDesimal(ppnInput);
        let hBeli = toAngka(hBeliEl.value);
        if (lastPpnStatus === "included" && currentPpnStatus === "excluded") {
          // gross → net: kupas PPN dari dalam (angka TURUN)
          hBeli = Math.round(hBeli / (1 + ppnPercent / 100));
          hBeliEl.value = toRibuan(hBeli);
          calcMarginFromHJual();
        } else if (
          lastPpnStatus === "excluded" &&
          currentPpnStatus === "included"
        ) {
          // net → gross: lebur PPN ke dalam (angka NAIK)
          hBeli = Math.round(hBeli * (1 + ppnPercent / 100));
          hBeliEl.value = toRibuan(hBeli);
          calcMarginFromHJual();
        }

        lastPpnStatus = currentPpnStatus;
        updatePpnModeHint();
        updatePpnBreakdown();
      }

      // Penjelasan singkat (bahasa awam) arti mode PPN yang sedang dipilih.
      function updatePpnModeHint() {
        const el = document.getElementById("ppnModeHint");
        if (!el) return;
        const mode = document.getElementById("fPpn")?.value || "included";
        if (mode !== "none" && !PKP_ITEM.is_pkp) {
          el.textContent =
            "Setelan PPN barang disimpan, tetapi Accounting masih non-PKP: kasir belum memungut PPN dan PPN pembelian masih melebur ke modal.";
          return;
        }
        el.textContent =
          mode === "none"
            ? "Barang ini tidak dikenakan PPN (Non-PPN). Tidak ada PPN saat beli maupun jual."
            : mode === "included"
              ? "Angka Harga Modal sudah termasuk PPN (seperti di nota supplier). PPN-nya dikupas otomatis sebagai PPN Masukan."
              : "Angka Harga Modal belum termasuk PPN. PPN ditambahkan di atasnya saat membeli (jadi PPN Masukan).";
      }

      // Saat tarif PPN diketik ulang: cukup segarkan rincian (harga modal/jual tidak diubah).
      function onPpnPercentInput() {
        updatePpnBreakdown();
      }

      function switchTab(t) {
        loadComponents(); // pastikan markup tab (kat/merek/satuan) sudah/mulai dimuat
        curTab = t;
        const panels = ["pnlBrg", "pnlKat", "pnlMerek", "pnlSat"];
        // Guard: jika elemen panel tidak ada (mis. assembly.html), skip seluruh logika tab
        if (panels.some((id) => !document.getElementById(id))) return;
        panels.forEach(
          (id) => (document.getElementById(id).style.display = "none"),
        );
        const map = {
          brg: "pnlBrg",
          kat: "pnlKat",
          merek: "pnlMerek",
          sat: "pnlSat",
        };
        document.getElementById(map[t]).style.display = "block";
        ["tbBarang", "tbKat", "tbMerek", "tbSat"].forEach((id) => {
          const el = document.getElementById(id);
          if (!el) return;
          const active =
            id ===
            "tb" + { brg: "Barang", kat: "Kat", merek: "Merek", sat: "Sat" }[t];
          el.style.background = active ? "var(--primary)" : "transparent";
          el.style.borderColor = active
            ? "var(--primary)"
            : "var(--border-color)";
          el.style.color = active ? "#fff" : "var(--text-muted)";
        });
        if (t === "kat") loadKat();
        if (t === "merek") loadMerek();
        if (t === "sat") loadSat();
      }

      async function refreshSelects() {
        // Data master jarang berubah → cache per-sesi (di-invalidasi saat tambah/edit/hapus).
        const [cats, brands, units] = await Promise.all([
          cachedApi("/items/categories"),
          cachedApi("/items/brands"),
          cachedApi("/items/units"),
        ]);
        masterUnits = units || [];
        // Catatan: filterKat statis di items.html, sedangkan fKat/fMerek/fSat ada di
        // popUp.html (di-inject loadComponents saat idle). refreshSelects() bisa berjalan
        // sebelum modal termuat, jadi setiap select dijaga null-safe.
        const catOptions = (cats || [])
          .map((c) => `<option value="${c.id}">${c.name}</option>`)
          .join("");
        const fk = document.getElementById("filterKat");
        if (fk)
          fk.innerHTML =
            '<option value="">Semua Kategori</option>' + catOptions;
        const fKat = document.getElementById("fKat");
        const selectedCategory = fKat?.value || "";
        if (fKat) {
          fKat.innerHTML =
            '<option value="">-- Pilih Jenis --</option>' + catOptions;
          fKat.value = selectedCategory;
        }
        const fMerek = document.getElementById("fMerek");
        const selectedBrand = fMerek?.value || "";
        if (fMerek) {
          fMerek.innerHTML =
            '<option value="">-- Pilih Merek --</option>' +
            (brands || [])
              .map((b) => `<option value="${b.id}">${b.name}</option>`)
              .join("");
          fMerek.value = selectedBrand;
        }
        const fSat = document.getElementById("fSat");
        const selectedUnit = fSat?.value || "";
        if (fSat) {
          fSat.innerHTML =
            '<option value="">-- Pilih Satuan --</option>' +
            (units || [])
              .map(
                (u) =>
                  `<option value="${u.id}">${u.name}${u.abbreviation ? " (" + u.abbreviation + ")" : ""}</option>`,
              )
              .join("");
          fSat.value = selectedUnit;
        }
      }

      // Token render: tiap loadItems menaikkannya; render bertahap berhenti bila tokennya basi
      // (mis. user mengetik pencarian baru saat baris lama belum selesai disusun).
      let _itemsRenderToken = 0;
      async function loadItems() {
        const q = document.getElementById("srchBarang").value;
        const kat = document.getElementById("filterKat").value;
        const inaktif = document.getElementById("showInaktif").checked;
        // lite=1: payload ringan untuk tabel (tanpa suppliers/group_discounts) → backend ~4x cepat.
        let url = `/items/?limit=500&lite=1&active_only=${!inaktif}`;
        if (q) url += `&search=${encodeURIComponent(q)}`;
        if (kat) url += `&category_id=${kat}`;
        const myToken = ++_itemsRenderToken; // batalkan render bertahap dari panggilan sebelumnya
        try {
          const items = await api("GET", url);
          if (myToken !== _itemsRenderToken) return; // sudah ada loadItems yang lebih baru
          const tbody = document.getElementById("tblBarang");
          if (!items.length) {
            tbody.innerHTML =
              '<tr><td colspan="8" style="text-align:center;padding:24px;color:var(--text-muted)">Tidak ada barang</td></tr>';
            return;
          }
          const rows = items.map((i) => {
            let diskon = "-";
            if (i.prices?.length) {
              const dPrice = i.prices.find((p) => p.name === "Harga Diskon");
              if (dPrice) diskon = fmtRp(dPrice.price);
            }
            const manageUrl = i.is_virtual_variant
              ? `/unit_conversion?source_item_id=${i.parent_item_id || i.id}&child_item_id=${i.id}`
              : `/unit_conversion?source_item_id=${i.id}`;
            return `<tr><td><b>${i.name}</b>${i.is_virtual_variant ? '<span class="bi" style="margin-left:8px">Virtual</span>' : ""}${i.barcode ? `<div style="font-family:monospace;font-size:12px;color:var(--text-muted)">${i.barcode}</div>` : ""}${i.is_virtual_variant ? `<div style="font-size:12px;color:var(--text-muted)">Stok terikat ke barang induk</div>` : ""}</td><td style="font-size:14px">${i.category?.name || "-"}</td><td style="font-size:14px">${i.unit?.name || "-"}</td><td>${fmtRp(i.buy_price)}</td><td style="color:var(--primary);font-weight:700">${fmtRp(i.sell_price)}</td><td style="color:#f59e0b;font-weight:700">${diskon}</td><td><span style="font-weight:700;color:${i.stock <= i.min_stock ? "#f59e0b" : "var(--text-main)"}">${
              Number.isInteger(Number(i.stock || 0))
                ? Number(i.stock || 0)
                : Number(i.stock || 0)
                    .toFixed(4)
                    .replace(/\.?0+$/, "")
            }</span></td><td style="white-space:nowrap">${i.is_virtual_variant ? "" : `<button class="bsm be" onclick='editItem(${i.id})'>Edit</button>`}<button class="bsm bp" style="display:none" onclick="location.href='${manageUrl}'">${i.is_virtual_variant ? "Kelola Unit" : "Multi Satuan"}</button>${i.is_virtual_variant ? "" : `<button class="bsm bd" onclick="toggleItem(${i.id},${i.is_active})">${i.is_active ? "Nonaktif" : "Aktifkan"}</button>`}</td></tr>`;
          });
          // Render bertahap: 80 baris pertama langsung (tabel muncul instan),
          // sisanya disusul per-frame agar paint pertama tidak terblok ribuan baris.
          const CHUNK = 80;
          tbody.innerHTML = rows.slice(0, CHUNK).join("");
          let idx = CHUNK;
          const renderNext = () => {
            if (myToken !== _itemsRenderToken) return; // dibatalkan loadItems yang lebih baru
            tbody.insertAdjacentHTML(
              "beforeend",
              rows.slice(idx, idx + CHUNK).join(""),
            );
            idx += CHUNK;
            if (idx < rows.length) requestAnimationFrame(renderNext);
          };
          if (rows.length > CHUNK) requestAnimationFrame(renderNext);
        } catch (e) {
          if (myToken === _itemsRenderToken) showToast(e.message, "error");
        }
      }

      function onGroupSelectChange() {
        const select = document.getElementById("fDiscountGroupSelect");
        const priceInput = document.getElementById("fGroupPriceInput");
        const percentInput = document.getElementById("fGroupPercentInput");
        const hint = document.getElementById("groupHint");
        const selOpt = select.options[select.selectedIndex];
        if (!selOpt || !selOpt.value) {
          priceInput.value = "";
          percentInput.value = "0,00";
          hint.textContent = "Pilih grup untuk mengatur harga khusus";
          return;
        }
        const groupName = selOpt.getAttribute("data-name");
        const defaultDisc = parseFloat(selOpt.getAttribute("data-disc")) || 0;
        hint.textContent = `Mengatur harga untuk grup ${groupName} (Default: ${defaultDisc}%)`;
        const hjualInput = document.getElementById("fHJual");
        const hjualDiskonInput = document.getElementById("fHJualDiskon");
        const checkbox = document.getElementById("fIsDiscountable");
        let basePrice =
          toAngka(hjualDiskonInput.value) > 0 && checkbox.checked
            ? toAngka(hjualDiskonInput.value)
            : toAngka(hjualInput.value);
        const key = groupName.toLowerCase();
        if (groupPrices[key] !== undefined) {
          let p = groupPrices[key];
          priceInput.value = toRibuan(p);
          let pct = basePrice > 0 ? ((basePrice - p) / basePrice) * 100 : 0;
          percentInput.value = toPersen(pct);
        } else {
          percentInput.value = toPersen(defaultDisc);
          let finalPrice = Math.round(
            basePrice - (basePrice * defaultDisc) / 100,
          );
          priceInput.value = toRibuan(finalPrice);
        }
      }

      function onGroupPercentInput() {
        const select = document.getElementById("fDiscountGroupSelect");
        const priceInput = document.getElementById("fGroupPriceInput");
        const percentInput = document.getElementById("fGroupPercentInput");
        const selOpt = select.options[select.selectedIndex];
        if (!selOpt || !selOpt.value) return;
        const pct = parseInputPersen(percentInput.value);
        const hjualInput = document.getElementById("fHJual");
        const hjualDiskonInput = document.getElementById("fHJualDiskon");
        const checkbox = document.getElementById("fIsDiscountable");
        let basePrice =
          toAngka(hjualDiskonInput.value) > 0 && checkbox.checked
            ? toAngka(hjualDiskonInput.value)
            : toAngka(hjualInput.value);
        let finalPrice = Math.round(basePrice - (basePrice * pct) / 100);
        priceInput.value = toRibuan(finalPrice);
        groupPrices[selOpt.getAttribute("data-name").toLowerCase()] =
          finalPrice;
        syncGroupPricesFromMain();
      }

      function onGroupPriceInput() {
        const select = document.getElementById("fDiscountGroupSelect");
        const priceInput = document.getElementById("fGroupPriceInput");
        const percentInput = document.getElementById("fGroupPercentInput");
        const selOpt = select.options[select.selectedIndex];
        if (!selOpt || !selOpt.value) return;
        const finalPrice = toAngka(priceInput.value);
        const hjualInput = document.getElementById("fHJual");
        const hjualDiskonInput = document.getElementById("fHJualDiskon");
        const checkbox = document.getElementById("fIsDiscountable");
        let basePrice =
          toAngka(hjualDiskonInput.value) > 0 && checkbox.checked
            ? toAngka(hjualDiskonInput.value)
            : toAngka(hjualInput.value);
        let pct =
          basePrice > 0 ? ((basePrice - finalPrice) / basePrice) * 100 : 0;
        percentInput.value = toPersen(pct);
        groupPrices[selOpt.getAttribute("data-name").toLowerCase()] =
          finalPrice;
        syncGroupPricesFromMain();
      }

      function syncGroupPricesFromMain() {
        if (satuanRows.length > 0) {
          const base = satuanRows[0];

          // Sinkronisasi field utama ke baris dasar (base row)
          if (base.is_base) {
            base.buy_price_auto =
              toAngka(document.getElementById("fHBeli")?.value) || 0;
            base.sell_price =
              toAngka(document.getElementById("fHJual")?.value) || 0;
            base.margin_percent = parseInputPersen(
              document.getElementById("fMargin")?.value,
            );
            base.child_unit_id = document.getElementById("fSat")?.value || "";
          }

          // Sinkronkan SEMUA grup dari state global groupPrices ke base row (ID-based)
          if (allGroups.length > 0) {
            allGroups.forEach((g, i) => {
              const key = g.name.toLowerCase();
              if (i === 0) {
                // Grup pertama (Umum) selalu sinkron dengan HJual utama
                groupPrices[key] = base.sell_price;
                base.group_prices[g.id] = base.sell_price;
              } else if (groupPrices[key] !== undefined) {
                // Grup lain sinkron dengan apa yang ada di state groupPrices (hasil load dari DB)
                base.group_prices[g.id] = groupPrices[key];
              }
            });
          }

          // Forward Sync: Recalculate all child rows
          for (let i = 1; i < satuanRows.length; i++) {
            if (!satuanRows[i].is_draft) {
              recalcAdvancedRow(i, "base");
            }
          }

          if (currentAdvancedType) renderAdvancedGrid();
        }
      }

      function updateDiscountPreview() {
        onGroupSelectChange();
        updatePpnBreakdown();
      }

      // Status PKP toko untuk rincian PPN di kolom harga. Default MATI → panel disembunyikan,
      // tampilan kembali persis seperti sebelum ada fitur ini (nol perubahan saat non-PKP).
      var PKP_ITEM = { is_pkp: false, tarif: 0 };

      async function loadPkpStatusItem() {
        try {
          const st = await api("GET", "/accounting/pkp-status");
          PKP_ITEM.is_pkp = !!st.is_pkp;
          PKP_ITEM.tarif = parseFloat(st.tarif_ppn) || 0;
          updatePpnModeHint();
          updatePpnBreakdown(); // segarkan bila modal kebetulan sudah terbuka
        } catch (e) {
          /* diam: bila gagal, panel tetap tersembunyi (perilaku lama) */
        }
      }

      // Rincian PPN beli & jual dalam satu panel. Tujuannya pengguna paham (1) beda "sudah" vs
      // "belum" termasuk PPN lewat angka nyata (DPP / PPN / total), dan (2) hubungan PPN Masukan
      // (saat beli) ↔ PPN Keluaran (saat jual) ↔ yang disetor ke negara. Harga jual dianggap
      // SUDAH termasuk PPN (konvensi PKP) → dikupas mundur. Tampil saat tarif PPN > 0 & modal terisi.
      function updatePpnBreakdown() {
        const box = document.getElementById("ppnBreakdown");
        if (!box) return;
        if (!PKP_ITEM.is_pkp) {
          box.style.display = "none"; // Accounting non-PKP: belum ada PPN Masukan/Keluaran
          return;
        }
        const t = parseDesimal(document.getElementById("fPpnPercent"));
        const mode = document.getElementById("fPpn")?.value || "included";
        const angkaBeli = toAngka(document.getElementById("fHBeli")?.value) || 0;
        const labelJual = toAngka(document.getElementById("fHJual")?.value) || 0;
        if (mode === "none") {
          box.style.display = "none"; // barang bebas PPN → tak ada yang dirinci
          return;
        }
        if (!(t > 0) || angkaBeli <= 0) {
          box.style.display = "none"; // tarif 0 / belum ada modal → tak ada yang dirinci
          return;
        }

        // Sisi beli: pecah harga modal jadi DPP + PPN Masukan + total bayar, sesuai mode.
        let dpp, ppnMasuk, totalBeli;
        if (mode === "included") {
          totalBeli = angkaBeli; // angka diketik = total (sudah termasuk PPN)
          dpp = angkaBeli / (1 + t / 100);
          ppnMasuk = totalBeli - dpp;
        } else {
          dpp = angkaBeli; // angka diketik = harga dasar (belum termasuk PPN)
          ppnMasuk = angkaBeli * (t / 100);
          totalBeli = dpp + ppnMasuk;
        }

        const baris = (kiri, kanan, tebal = false) =>
          `<div style="display:flex;justify-content:space-between;${tebal ? "font-weight:700;color:var(--primary)" : ""}"><span style="color:var(--text-muted)">${kiri}</span><span>${kanan}</span></div>`;
        const garis = `<hr style="border:none;border-top:1px dashed var(--border-color);margin:6px 0" />`;
        const judul = (txt) =>
          `<div style="font-weight:700;margin:2px 0 4px">${txt}</div>`;

        // Tebalkan baris yang = angka diketik pengguna (DPP saat "belum", Total saat "sudah").
        let html = judul("🧾 Saat beli (bayar ke supplier)");
        html += baris("Harga dasar (DPP)", fmtRp(Math.round(dpp)), mode === "excluded");
        html += baris(`PPN ${t}% (PPN Masukan)`, fmtRp(Math.round(ppnMasuk)));
        html += baris("Total bayar ke supplier", fmtRp(Math.round(totalBeli)), mode === "included");

        if (labelJual > 0) {
          const jualBersih = labelJual / (1 + t / 100); // pendapatan asli (sebelum PPN)
          const ppnKeluar = labelJual - jualBersih;
          const untung = jualBersih - dpp; // bandingkan bersih vs bersih (DPP)
          const ppnSetor = ppnKeluar - ppnMasuk;
          const rugi = untung < -0.5;
          const minimal = Math.round(dpp * (1 + t / 100)); // harga jual impas
          html += garis + judul("💰 Saat jual (terima dari pembeli)");
          html += baris("Harga bersih (masuk kantong)", fmtRp(Math.round(jualBersih)));
          html += baris(`PPN ${t}% (PPN Keluaran)`, fmtRp(Math.round(ppnKeluar)));
          html += `<div style="display:flex;justify-content:space-between;font-weight:700;color:${rugi ? "#dc2626" : "#16a34a"}"><span>${rugi ? "⛔ RUGI" : "✅ Untung bersih"}</span><span>${rugi ? "−" : "+"}${fmtRp(Math.abs(Math.round(untung)))}</span></div>`;
          html += garis;
          html += baris("PPN disetor ke negara", fmtRp(Math.round(ppnSetor)), true);
          html += `<div style="margin-top:2px;color:var(--text-muted)">= PPN saat jual − PPN saat beli</div>`;
          if (rugi)
            html += `<div style="margin-top:6px;color:#dc2626">⚠️ Di bawah harga jual minimal ${fmtRp(minimal)} — toko yang menanggung PPN, bukan pembeli.</div>`;
        }

        box.innerHTML = html;
        box.style.display = "";
      }

      // Isi <select> grup diskon dari allGroups. Dipisah dari pemuatan data agar bisa dipanggil
      // ulang setelah popUp.html termuat (urutan load grup vs komponen tidak menentukan hasil).
      function populateDiscountGroupSelect() {
        const select = document.getElementById("fDiscountGroupSelect");
        if (!select || !allGroups.length) return;
        select.innerHTML =
          '<option value="">Pilih Grup (Set Harga)</option>' +
          allGroups
            .map(
              (g) =>
                `<option value="${g.id}" data-name="${g.name}" data-disc="${g.discount_percent}">${g.name}</option>`,
            )
            .join("");
      }

      async function loadCustomerGroups() {
        try {
          allGroups = await api("GET", "/customers/groups");
          populateDiscountGroupSelect();
        } catch (ex) {
          console.error("Gagal load grup:", ex);
        }
      }

      let allSups = [];
      let selectedSups = new Map();
      let cariSupTimeout;
      const SUPPLIER_CONTEXT_GENERAL_LABEL =
        "-- Harga Umum (Default Semua Supplier) --";
      let supplierContextOptions = [];
      let supplierContextHighlightedIndex = 0;
      let supSearchHighlightedIndex = -1;
      let cariSupHighlightedIndex = -1;

      async function loadSuppliers() {
        try {
          const res = await api("GET", "/suppliers/?limit=500");
          allSups = Array.isArray(res) ? res : res.data || [];
          if (document.getElementById("fSupplierContext"))
            updateSupplierContextDropdown();
        } catch (e) {
          console.error("Gagal load suppliers:", e);
        }
      }

      function getSupplierContextCandidates() {
        const suppliers =
          editItemId === null ? allSups : Array.from(selectedSups.values());
        return [
          {
            value: "",
            name: SUPPLIER_CONTEXT_GENERAL_LABEL,
            code: "",
            searchText: "harga umum default semua supplier umum",
          },
          ...suppliers.map((supplier) => ({
            value: String(supplier.id),
            name: supplier.name || "-",
            code: supplier.code || "",
            searchText: `${supplier.name || ""} ${supplier.code || ""}`,
          })),
        ];
      }

      function syncSupplierContextCombobox() {
        const select = document.getElementById("fSupplierContext");
        const input = document.getElementById("fSupplierContextSearch");
        if (!select || !input) return;

        const selectedValue = String(select.value || "");
        const selectedSupplier =
          allSups.find((supplier) => String(supplier.id) === selectedValue) ||
          selectedSups.get(Number(selectedValue));
        input.value = selectedValue
          ? selectedSupplier?.name ||
            select.selectedOptions[0]?.textContent?.trim() ||
            ""
          : SUPPLIER_CONTEXT_GENERAL_LABEL;
        input.dataset.selectedValue = selectedValue;
      }

      function closeSupplierContextDropdown(restoreSelection = true) {
        const input = document.getElementById("fSupplierContextSearch");
        const dropdown = document.getElementById("supplierContextDropdown");
        if (!input || !dropdown) return;
        dropdown.classList.remove("show");
        input.setAttribute("aria-expanded", "false");
        input.removeAttribute("aria-activedescendant");
        supplierContextOptions = [];
        supplierContextHighlightedIndex = 0;
        if (restoreSelection) syncSupplierContextCombobox();
      }

      function updateSupplierContextHighlight(nextIndex) {
        const input = document.getElementById("fSupplierContextSearch");
        const dropdown = document.getElementById("supplierContextDropdown");
        const optionElements = Array.from(
          dropdown?.querySelectorAll(".supplier-context-option") || [],
        );
        if (!input || optionElements.length === 0) {
          input?.removeAttribute("aria-activedescendant");
          return;
        }

        supplierContextHighlightedIndex =
          (nextIndex + optionElements.length) % optionElements.length;
        optionElements.forEach((option, index) => {
          const highlighted = index === supplierContextHighlightedIndex;
          option.classList.toggle("highlighted", highlighted);
          option.setAttribute("aria-selected", String(highlighted));
        });
        const activeOption = optionElements[supplierContextHighlightedIndex];
        input.setAttribute("aria-activedescendant", activeOption.id);
        activeOption.scrollIntoView({ block: "nearest" });
      }

      function renderSupplierContextDropdown(
        searchText = "",
        resetHighlight = true,
      ) {
        const input = document.getElementById("fSupplierContextSearch");
        const dropdown = document.getElementById("supplierContextDropdown");
        if (!input || !dropdown) return;

        const search = String(searchText || "").toLowerCase().trim();
        const matches = getSupplierContextCandidates().filter((option) =>
          option.searchText.toLowerCase().includes(search),
        );
        supplierContextOptions = matches.slice(0, 50);
        if (resetHighlight) supplierContextHighlightedIndex = 0;
        dropdown.replaceChildren();

        if (supplierContextOptions.length === 0) {
          const empty = document.createElement("div");
          empty.className = "supplier-context-empty";
          empty.textContent = "Supplier tidak ditemukan";
          dropdown.appendChild(empty);
        } else {
          supplierContextOptions.forEach((option, index) => {
            const optionElement = document.createElement("div");
            optionElement.id = `supplier-context-option-${option.value || "general"}`;
            optionElement.className = "supplier-context-option";
            optionElement.setAttribute("role", "option");
            optionElement.dataset.value = option.value;

            const name = document.createElement("span");
            name.textContent = option.name;
            optionElement.appendChild(name);
            if (option.code) {
              const code = document.createElement("small");
              code.textContent = `[${option.code}]`;
              optionElement.appendChild(code);
            }

            optionElement.addEventListener("mousedown", (event) =>
              event.preventDefault(),
            );
            optionElement.addEventListener("click", () =>
              chooseSupplierContext(option.value),
            );
            dropdown.appendChild(optionElement);
          });

          if (matches.length > supplierContextOptions.length) {
            const hint = document.createElement("div");
            hint.className = "supplier-context-hint";
            hint.textContent = `Menampilkan 50 dari ${matches.length} supplier. Lanjutkan mengetik.`;
            dropdown.appendChild(hint);
          }
        }

        dropdown.classList.add("show");
        input.setAttribute("aria-expanded", "true");
        updateSupplierContextHighlight(supplierContextHighlightedIndex);
      }

      function chooseSupplierContext(value) {
        const select = document.getElementById("fSupplierContext");
        if (!select) return;
        const normalizedValue = String(value || "");
        const optionExists = Array.from(select.options).some(
          (option) => option.value === normalizedValue,
        );
        if (!optionExists) return;

        select.value = normalizedValue;
        onSupplierContextChange();
        syncSupplierContextCombobox();
        closeSupplierContextDropdown(false);
      }

      function setupSupplierContextCombobox() {
        const input = document.getElementById("fSupplierContextSearch");
        const dropdown = document.getElementById("supplierContextDropdown");
        const wrapper = input?.closest(".supplier-context-combobox");
        if (!input || !dropdown || !wrapper) return;
        if (input.dataset.comboboxReady === "true") return;
        input.dataset.comboboxReady = "true";
        input.addEventListener("focus", () => {
          input.select();
          renderSupplierContextDropdown("", true);
        });
        input.addEventListener("click", () => {
          if (!dropdown.classList.contains("show")) {
            input.select();
            renderSupplierContextDropdown("", true);
          }
        });
        input.addEventListener("input", () => {
          renderSupplierContextDropdown(input.value, true);
        });
        input.addEventListener("keydown", (event) => {
          const isNextKey =
            event.key === "ArrowDown" || event.key === "ArrowRight";
          const isPreviousKey =
            event.key === "ArrowUp" || event.key === "ArrowLeft";
          if (isNextKey || isPreviousKey) {
            event.preventDefault();
            event.stopPropagation();
            if (!dropdown.classList.contains("show"))
              renderSupplierContextDropdown(input.value, true);
            const direction = isNextKey ? 1 : -1;
            updateSupplierContextHighlight(
              supplierContextHighlightedIndex + direction,
            );
            return;
          }
          if (event.key === "Enter" && dropdown.classList.contains("show")) {
            event.preventDefault();
            event.stopPropagation();
            const selectedOption =
              supplierContextOptions[supplierContextHighlightedIndex];
            if (selectedOption) chooseSupplierContext(selectedOption.value);
            return;
          }
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            closeSupplierContextDropdown(true);
          } else if (event.key === "Tab") {
            closeSupplierContextDropdown(true);
          }
        });
        input.addEventListener("blur", () => {
          setTimeout(() => {
            if (!wrapper.contains(document.activeElement))
              closeSupplierContextDropdown(true);
          }, 0);
        });
        document.addEventListener("click", (event) => {
          if (!wrapper.contains(event.target))
            closeSupplierContextDropdown(true);
        });

        updateSupplierContextDropdown();
        syncSupplierContextCombobox();
      }

      function setupSupSearch() {
        setupSupplierContextCombobox();
        const input = document.getElementById("supSearchInput");
        const dropdown = document.getElementById("supDropdown");
        if (input?.dataset.keyboardReady === "true") return;
        if (input) input.dataset.keyboardReady = "true";
        if (!input || !dropdown) return; // popUp.html belum termuat — dipanggil ulang setelah load
        input.addEventListener("focus", () => {
          renderSupDropdown(input.value);
          dropdown.classList.add("show");
        });
        input.addEventListener("input", () => {
          renderSupDropdown(input.value);
        });
        input.addEventListener("keydown", (event) => {
          const isNextKey =
            event.key === "ArrowDown" || event.key === "ArrowRight";
          const isPreviousKey =
            event.key === "ArrowUp" || event.key === "ArrowLeft";
          const isOpen = dropdown.classList.contains("show");

          if (isNextKey || isPreviousKey) {
            if (!isOpen) renderSupDropdown(input.value);
            const options = getSupplierSearchDropdownOptions(dropdown);
            if (!options.length) return;
            event.preventDefault();
            event.stopPropagation();
            highlightSupplierSearchOption(
              input,
              dropdown,
              supSearchHighlightedIndex + (isNextKey ? 1 : -1),
            );
            return;
          }

          if (event.key === "Enter") {
            event.preventDefault();
            event.stopPropagation();
            if (!dropdown.classList.contains("show"))
              renderSupDropdown(input.value);
            chooseSupplierSearchOption(input, dropdown, false);
            return;
          }

          if (event.key === "Tab" && isOpen) {
            chooseSupplierSearchOption(input, dropdown, true);
            return;
          }

          if (event.key === "Escape" && isOpen) {
            event.preventDefault();
            event.stopPropagation();
            dropdown.classList.remove("show");
            supSearchHighlightedIndex = -1;
            input.removeAttribute("aria-activedescendant");
          }
        });
        document.addEventListener("click", (e) => {
          if (!input.contains(e.target) && !dropdown.contains(e.target))
            dropdown.classList.remove("show");
        });
      }

      function getSupplierSearchDropdownOptions(dropdown) {
        return Array.from(
          dropdown?.querySelectorAll(".sup-dropdown-item[data-id]") || [],
        );
      }

      function highlightSupplierSearchOption(input, dropdown, nextIndex) {
        const options = getSupplierSearchDropdownOptions(dropdown);
        if (!options.length) {
          supSearchHighlightedIndex = -1;
          input.removeAttribute("aria-activedescendant");
          return;
        }

        supSearchHighlightedIndex =
          (nextIndex + options.length) % options.length;
        options.forEach((option, index) => {
          const active = index === supSearchHighlightedIndex;
          option.classList.toggle("highlighted", active);
          option.setAttribute("aria-selected", String(active));
        });
        const activeOption = options[supSearchHighlightedIndex];
        input.setAttribute("aria-activedescendant", activeOption.id);
        activeOption.scrollIntoView({ block: "nearest" });
      }

      function chooseSupplierSearchOption(input, dropdown, highlightedOnly) {
        const options = getSupplierSearchDropdownOptions(dropdown);
        const index = highlightedOnly
          ? supSearchHighlightedIndex
          : supSearchHighlightedIndex >= 0
            ? supSearchHighlightedIndex
            : 0;
        const option = options[index];
        if (!option) {
          if (highlightedOnly) dropdown.classList.remove("show");
          return false;
        }
        const addButton = option.querySelector(".sup-add-btn");
        (addButton || option).click();
        return true;
      }

      function renderSupDropdown(searchText = "") {
        const dropdown = document.getElementById("supDropdown");
        const input = document.getElementById("supSearchInput");
        if (!dropdown) return;
        supSearchHighlightedIndex = -1;
        input?.removeAttribute("aria-activedescendant");
        const search = searchText.toLowerCase().trim();
        const available = allSups.filter(
          (s) =>
            !selectedSups.has(s.id) &&
            (s.name.toLowerCase().includes(search) ||
              (s.code && s.code.toLowerCase().includes(search))),
        );
        if (available.length === 0) {
          dropdown.innerHTML = `<div class="sup-dropdown-empty">${search ? "Tidak ditemukan" : "Semua supplier sudah ditambahkan"}</div>`;
          dropdown.classList.add("show");
          return;
        }
        dropdown.innerHTML = available
          .slice(0, 50)
          .map(
            (s) =>
              `<div id="sup-option-${s.id}" role="option" aria-selected="false" class="sup-dropdown-item" data-id="${s.id}"><span>${s.name}${s.code ? ` <small>[${s.code}]</small>` : ""}</span><button type="button" class="sup-add-btn" onclick="addSupToSelectionById(${s.id})">+</button></div>`,
          )
          .join("");
        dropdown.classList.add("show");
      }

      window.addSupToSelectionById = function (id) {
        const s = allSups.find((x) => x.id === id);
        if (s) {
          selectedSups.set(s.id, { id: s.id, name: s.name, code: s.code });
          renderSelectedSups();
          document.getElementById("supSearchInput").value = "";
          document.getElementById("supDropdown").classList.remove("show");
          supSearchHighlightedIndex = -1;
        }
      };

      function removeSupFromSelection(sId) {
        selectedSups.delete(sId);
        renderSelectedSups();
      }

      function onSupplierContextChange() {
        const select = document.getElementById("fSupplierContext");
        const nextSupplierId = select.value;

        // Cek SEBELUM perubahan apa pun: apakah supplier ini sudah pernah diatur?
        const alreadyConfigured =
          nextSupplierId !== "" &&
          supplierSettingsMap[nextSupplierId] !== undefined;

        // Otomatis tambahkan ke selectedSups (chip) jika dipilih lewat dropdown konteks
        if (nextSupplierId !== "") {
          const supIdInt = parseInt(nextSupplierId);
          if (!selectedSups.has(supIdInt)) {
            addSupToSelectionById(supIdInt);
          }
        }

        // 1) Simpan setelan konteks LAMA (harga beli, barcode, PPN)
        const oldContext = currentSupplierContext;
        const currentHBeli = toAngka(document.getElementById("fHBeli").value);
        const currentBarcode = document.getElementById("fBarcode").value;
        const isAuto = document.getElementById("autoBarcode").checked;
        const finalBarcode = isAuto ? "" : currentBarcode;
        const currentPpnType = document.getElementById("fPpn").value;
        // Mode "none" (Tanpa PPN) → tarif disimpan 0 agar konsisten dengan tipe.
        const currentPpnPercent =
          currentPpnType === "none"
            ? 0
            : parseDesimal(document.getElementById("fPpnPercent"));
        if (oldContext === "") {
          generalBuyPrice = currentHBeli;
          generalBarcode = finalBarcode;
          generalPpnType = currentPpnType;
          generalPpnPercent = currentPpnPercent;
        } else {
          supplierSettingsMap[oldContext] = {
            buy_price: currentHBeli,
            barcode: finalBarcode,
            ppn_type: currentPpnType,
            ppn_percent: currentPpnPercent,
          };
        }

        // 2) Muat setelan konteks BARU
        let targetHBeli, targetBarcode, targetPpnType, targetPpnPercent;
        if (nextSupplierId === "") {
          targetHBeli = generalBuyPrice;
          targetBarcode = generalBarcode;
          targetPpnType = generalPpnType;
          targetPpnPercent = generalPpnPercent;
        } else {
          const s = supplierSettingsMap[nextSupplierId];
          const supplierData = allSups.find(
            (sup) => String(sup.id) === nextSupplierId,
          );
          const defPpn =
            supplierData && supplierData.PpnSupplier != null
              ? supplierData.PpnSupplier
              : 11;
          if (s) {
            targetHBeli = s.buy_price;
            targetBarcode = s.barcode;
            targetPpnType = s.ppn_type || "included";
            targetPpnPercent = s.ppn_percent || 0;
          } else {
            // Supplier baru mengikuti setelan eksplisit supplier. Jika supplier belum
            // memiliki setelan PPN, pertahankan default form (Tanpa PPN).
            targetHBeli = generalBuyPrice;
            targetBarcode = generalBarcode;
            targetPpnType = supplierData?.ppn_type || generalPpnType;
            targetPpnPercent = targetPpnType === "none" ? 0 : defPpn;
          }
        }

        // 3) Terapkan ke form (PPN di-set langsung TANPA konversi harga otomatis)
        document.getElementById("fHBeli").value = toRibuan(targetHBeli);
        document.getElementById("fBarcode").value = targetBarcode || "";
        document.getElementById("autoBarcode").checked = !(
          targetBarcode && targetBarcode.trim() !== ""
        );
        const ppnSelect = document.getElementById("fPpn");
        const ppnGroup = document.getElementById("ppnPercentGroup");
        const ppnInput = document.getElementById("fPpnPercent");
        if (ppnSelect) ppnSelect.value = targetPpnType;
        // Mode "none": sembunyikan tarif (bebas PPN); selain itu tarif selalu tampil.
        if (ppnGroup)
          ppnGroup.style.display = targetPpnType === "none" ? "none" : "";
        if (ppnInput)
          ppnInput.value =
            parseFloat(targetPpnPercent) > 0
              ? targetPpnPercent
              : PKP_ITEM.tarif > 0
                ? PKP_ITEM.tarif
                : 11;
        lastPpnStatus = targetPpnType; // sinkronkan agar toggle PPN berikutnya benar
        updatePpnModeHint();
        updatePpnBreakdown();

        currentSupplierContext = nextSupplierId;
        sharedFieldNotified = false; // izinkan lagi notif "berlaku semua supplier"
        toggleBarcodeUI();
        calcMarginFromHJual(); // harga jual tetap (shared) -> margin menyesuaikan
        syncGroupPricesFromMain();
        syncSupplierContextCombobox();

        // 4) Notifikasi status supplier yang dipilih
        if (nextSupplierId !== "") {
          if (alreadyConfigured) {
            showToast(
              "Ini setelan harga beli untuk supplier yang Anda pilih.",
              "info",
            );
          } else {
            showToast(
              "Silakan input data harga beli baru untuk supplier ini.",
              "info",
            );
          }
        }
      }

      function updateSupplierContextDropdown() {
        const select = document.getElementById("fSupplierContext");
        if (!select) return;
        const oldVal = select.value;
        let html =
          '<option value="">-- Harga Umum (Default Semua Supplier) --</option>';
        const listToShow =
          editItemId === null ? allSups : Array.from(selectedSups.values());
        listToShow.forEach((s) => {
          html += `<option value="${s.id}">🚚 ${s.name}</option>`;
        });
        select.innerHTML = html;
        const validIds = listToShow.map((s) => String(s.id)).concat([""]);
        if (validIds.includes(oldVal)) select.value = oldVal;
        else {
          select.value = "";
          if (currentSupplierContext !== "") onSupplierContextChange();
        }
        syncSupplierContextCombobox();
      }

      function renderSelectedSups() {
        const container = document.getElementById("selectedSupsContainer");
        if (selectedSups.size === 0) {
          container.innerHTML = `<span style="color:var(--text-muted);font-size:13px;padding:4px;">Belum ada supplier dipilih</span>`;
          updateSupplierContextDropdown();
          return;
        }
        container.innerHTML = Array.from(selectedSups.values())
          .map(
            (s) =>
              `<span class="sup-chip"><span class="chip-name">${s.name}</span><button type="button" class="chip-remove" onclick="removeSupFromSelection(${s.id})">×</button></span>`,
          )
          .join("");
        updateSupplierContextDropdown();
      }

      function calcHJualFromMargin() {
        const hBeli = toAngka(document.getElementById("fHBeli").value) || 0;
        const margin = parseInputPersen(
          document.getElementById("fMargin").value,
        );
        const hJual = Math.round(hBeli + (hBeli * margin) / 100);
        document.getElementById("fHJual").value = toRibuan(hJual);
        updateDiscountPreview();
        syncGroupPricesFromMain();
      }

      function calcMarginFromHJual() {
        const hBeli = toAngka(document.getElementById("fHBeli").value) || 0;
        const hJual = toAngka(document.getElementById("fHJual").value) || 0;
        if (hJual <= 0) {
          updateDiscountPreview();
          syncGroupPricesFromMain();
          return;
        }
        document.getElementById("fMargin").value = toPersen(
          hBeli > 0 ? ((hJual - hBeli) / hBeli) * 100 : 0,
        );
        updateDiscountPreview();
        syncGroupPricesFromMain();
      }

      // Harga beli dihitung dari harga jual (shared) & margin — dipakai di konteks supplier
      function calcHBeliFromMargin() {
        const margin = parseInputPersen(
          document.getElementById("fMargin").value,
        );
        const hJual = toAngka(document.getElementById("fHJual").value) || 0;
        if (hJual <= 0) {
          updateDiscountPreview();
          syncGroupPricesFromMain();
          return;
        }
        const hBeli =
          margin > -100 ? Math.round(hJual / (1 + margin / 100)) : 0;
        document.getElementById("fHBeli").value = toRibuan(hBeli);
        updateDiscountPreview();
        syncGroupPricesFromMain();
      }

      function inSupplierContext() {
        return currentSupplierContext && currentSupplierContext !== "";
      }

      // Input handlers sadar-konteks. Di konteks supplier, HARGA JUAL adalah patokan
      // bersama (tetap); yang menyesuaikan adalah harga beli / margin supplier itu.
      function onBuyPriceInput() {
        if (inSupplierContext()) calcMarginFromHJual();
        else calcHJualFromMargin();
      }
      function onMarginInput() {
        if (inSupplierContext()) calcHBeliFromMargin();
        else calcHJualFromMargin();
      }
      function onSellPriceInput() {
        calcMarginFromHJual();
        notifySharedFieldChange();
      }

      // Beritahu (sekali per pemilihan supplier) bahwa field ini berlaku untuk SEMUA supplier.
      function notifySharedFieldChange() {
        if (!inSupplierContext() || sharedFieldNotified) return;
        sharedFieldNotified = true;
        showToast(
          "Catatan: Harga Jual, Level Harga & Potongan berlaku untuk SEMUA supplier. Hanya Harga Beli & PPN yang khusus per supplier.",
          "info",
        );
      }

      // ─── FOTO BARANG (input di popUp.html) ───
      // Kecilkan & kompres gambar di browser (WebP) sebelum upload → foto ~20–60 KB.
      // Inline di sini (bukan components.js) karena items.html tidak memuat components.js.
      async function kecilkanGambar(file, maksPx = 800, mutu = 0.8) {
        try {
          const bitmap = await createImageBitmap(file);
          let width = bitmap.width;
          let height = bitmap.height;
          const skala = Math.min(1, maksPx / Math.max(width, height));
          width = Math.max(1, Math.round(width * skala));
          height = Math.max(1, Math.round(height * skala));
          const canvas = document.createElement("canvas");
          canvas.width = width;
          canvas.height = height;
          canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
          if (bitmap.close) bitmap.close();
          const blob = await new Promise((resolve) =>
            canvas.toBlob(resolve, "image/webp", mutu),
          );
          return blob || file;
        } catch (e) {
          console.warn("kecilkanGambar gagal, pakai file asli:", e);
          return file;
        }
      }

      // Saat file dipilih → kecilkan di browser (WebP), simpan Blob di `fotoBaru`, tampilkan preview.
      async function onPilihFoto(e) {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        fotoBaru = await kecilkanGambar(file, 800, 0.8);
        hapusFotoFlag = false;
        const img = document.getElementById("fFotoPreview");
        const ph = document.getElementById("fFotoPlaceholder");
        const btn = document.getElementById("fFotoHapus");
        if (img) {
          img.src = URL.createObjectURL(fotoBaru);
          img.style.display = "block";
        }
        if (ph) ph.style.display = "none";
        if (btn) btn.style.display = "block";
      }

      // Tombol "Hapus Foto": batalkan foto baru & tandai agar saveItem memanggil DELETE /image.
      function hapusFotoBarang() {
        fotoBaru = null;
        hapusFotoFlag = true;
        const inp = document.getElementById("fFotoInput");
        if (inp) inp.value = "";
        const img = document.getElementById("fFotoPreview");
        const ph = document.getElementById("fFotoPlaceholder");
        const btn = document.getElementById("fFotoHapus");
        if (img) {
          img.removeAttribute("src");
          img.style.display = "none";
        }
        if (ph) ph.style.display = "";
        if (btn) btn.style.display = "none";
      }

      // Selaraskan kotak foto dengan `imagePath` (atau kosong) + reset state upload.
      function resetFotoUI(imagePath) {
        fotoBaru = null;
        hapusFotoFlag = false;
        const inp = document.getElementById("fFotoInput");
        if (inp) inp.value = "";
        const img = document.getElementById("fFotoPreview");
        const ph = document.getElementById("fFotoPlaceholder");
        const btn = document.getElementById("fFotoHapus");
        if (imagePath) {
          if (img) {
            img.src = imagePath;
            img.style.display = "block";
          }
          if (ph) ph.style.display = "none";
          if (btn) btn.style.display = "block";
        } else {
          if (img) {
            img.removeAttribute("src");
            img.style.display = "none";
          }
          if (ph) ph.style.display = "";
          if (btn) btn.style.display = "none";
        }
      }

      function openItemModal(item = null, editMode = false) {
        loadComponents(); // pastikan markup modal (popUp) sudah/mulai dimuat
        editItemId = null;
        document.getElementById("btnHargaLanjutan").style.display = editMode
          ? "block"
          : "none";
        document.getElementById("mBarangTitle").textContent = "Tambah Barang";
        document.getElementById("fBarang").reset();
        resetFotoUI(null); // kosongkan foto (default barang baru; ditimpa di bawah bila edit)
        document.getElementById("autoBarcode").checked = true;
        toggleBarcodeUI();

        // Reset Advanced Tabs State
        currentAdvancedType = null;
        currentPotonganType = null;
        const advButtons = ["btnAdvSatuan", "btnAdvHarga", "btnAdvJumlah"];
        advButtons.forEach((id) => {
          const btn = document.getElementById(id);
          if (btn) {
            btn.classList.remove("disabled");
            btn.style.background = "var(--card-bg)";
            btn.style.color = "var(--text-main)";
            btn.style.borderColor = "var(--border-color)";
          }
        });
        const advContent = document.getElementById("advancedContent");
        if (advContent) {
          advContent.innerHTML = "";
          advContent.style.display = "none";
        }
        // Reset panel Potongan Harga Jual juga. Kalau tidak dikosongkan, grid
        // (baris grup + Pot.1–4) milik barang SEBELUMNYA tertinggal di DOM, lalu
        // ikut terbaca saat simpan → potongan barang lain bisa nyangkut ke barang ini.
        const potonganContent = document.getElementById(
          "contentPotonganHargaJual",
        );
        if (potonganContent) {
          potonganContent.innerHTML = "";
          potonganContent.style.display = "none";
        }
        const btnPotongan = document.getElementById("btnAdvPotonganHargaJual");
        if (btnPotongan) {
          btnPotongan.style.background = "var(--card-bg)";
          btnPotongan.style.color = "var(--text-main)";
          btnPotongan.style.borderColor = "var(--border-color)";
        }

        const setVal = (id, val) => {
          const el = document.getElementById(id);
          if (el) el.value = val;
        };
        setVal("fKodeItem", "");
        setVal("fHBeli", "0,00");
        setVal("fMargin", "0,00");
        setVal("fHJual", "0,00");
        setVal("fHJualDiskon", "0,00");
        setVal("fStokMin", "0,00");
        setVal("fPpn", "none");
        setVal("fPpnPercent", toDesimal(PKP_ITEM.tarif > 0 ? PKP_ITEM.tarif : 11));
        lastPpnStatus = "none";
        togglePpnPercent();
        updatePpnModeHint();
        document.getElementById("fIsDiscountable").checked = true;
        document.getElementById("fDiscountGroupSelect").selectedIndex = 0;
        setVal("fGroupPriceInput", "");
        setVal("fGroupPercentInput", "0,00");
        groupPrices = {};
        groupDiscounts = [];
        selectedSups.clear();
        supplierSettingsMap = {};
        generalBuyPrice = 0;
        generalBarcode = "";
        generalPpnType = "none";
        generalPpnPercent = 0;
        sharedFieldNotified = false;
        currentSupplierContext = "";
        renderSelectedSups();
        document.getElementById("fSupplierContext").value = "";
        syncSupplierContextCombobox();
        document.getElementById("supSearchInput").value = "";

        // Pra-isi Harga Modal dari "harga pokok minimum" bila dipanggil dari alur lain
        // (assembly.html men-set window.hppFloorBaru sebelum openItemModal, agar produk
        // rakitan baru punya basis biaya = total biaya komponennya).
        if (!item && window.hppFloorBaru > 0) {
          const elBeli = document.getElementById("fHBeli");
          if (elBeli) {
            elBeli.value = toRibuan(Math.round(window.hppFloorBaru));
            onBuyPriceInput();
          }
        }

        let detectedType = null;
        if (item) {
          editItemId = item.id;
          resetFotoUI(item.image_path || null); // tampilkan foto lama (jika ada)
          document.getElementById("mBarangTitle").textContent = "Edit Barang";
          document.getElementById("fKodeItem").value = item.code || "";
          document.getElementById("fNama").value = item.name;
          document.getElementById("fKat").value = item.category_id || "";
          document.getElementById("fMerek").value = item.brand_id || "";
          document.getElementById("fSat").value = item.unit_id || "";
          if (item.is_discountable !== undefined)
            document.getElementById("fIsDiscountable").checked =
              item.is_discountable;
          document.getElementById("fHBeli").value = toRibuan(item.buy_price);
          document.getElementById("fHJual").value = toRibuan(item.sell_price);
          // Barang lama tetap memakai status PPN tersimpannya saat diedit. Nilai null
          // adalah data lama yang mengikuti tarif toko; hanya barang BARU yang default
          // ke Tanpa PPN.
          if (item.ppn_percent != null) {
            const itemPpnPercent = parseFloat(item.ppn_percent) || 0;
            document.getElementById("fPpnPercent").value = item.ppn_percent;
            generalPpnPercent = itemPpnPercent;
            // ppn_percent = 0 EKSPLISIT (bukan null) → barang ditandai Tanpa PPN.
            if (itemPpnPercent === 0) {
              document.getElementById("fPpn").value = "none";
              generalPpnType = "none";
              lastPpnStatus = "none";
              const grp = document.getElementById("ppnPercentGroup");
              if (grp) grp.style.display = "none";
            } else {
              document.getElementById("fPpn").value = "included";
              generalPpnType = "included";
              lastPpnStatus = "included";
              const grp = document.getElementById("ppnPercentGroup");
              if (grp) grp.style.display = "";
            }
          } else {
            document.getElementById("fPpn").value = "included";
            generalPpnType = "included";
            lastPpnStatus = "included";
            const grp = document.getElementById("ppnPercentGroup");
            if (grp) grp.style.display = "";
          }
          updatePpnModeHint();
          updatePpnBreakdown();
          generalBuyPrice = parseFloat(item.buy_price) || 0;
          generalBarcode = item.barcode || "";
          initialBuyPrice = generalBuyPrice;
          initialSellPrice = parseFloat(item.sell_price) || 0;
          initialGroupPrices = {};
          document.getElementById("fMargin").value = toPersen(
            initialBuyPrice > 0
              ? ((initialSellPrice - initialBuyPrice) / initialBuyPrice) * 100
              : 0,
          );
          document.getElementById("fStokMin").value = toRibuan(item.min_stock);
          document.getElementById("fDesk").value = item.description || "";
          if (item.barcode?.trim()) {
            document.getElementById("autoBarcode").checked = false;
            toggleBarcodeUI();
            document.getElementById("fBarcode").value = item.barcode;
          }

          // Detect Pricing Type
          if (item.prices?.length) {
            const hasGrosir = item.prices.some(
              (p) => p.name === "Grosir" && p.min_qty > 1,
            );
            const hasLevelHarga = item.prices.some(
              (p) => p.name !== "Grosir" && p.name !== "Harga Diskon",
            );

            if (hasLevelHarga) detectedType = "levelHarga";
            else if (hasGrosir) detectedType = "levelJumlah";

            item.prices.forEach((p) => {
              if (p.name === "Harga Diskon")
                document.getElementById("fHJualDiskon").value = toRibuan(
                  p.price,
                );
              else if (p.name !== "Grosir") {
                const key = p.name.toLowerCase();
                groupPrices[key] = p.price;
                initialGroupPrices[key] = p.price;
              }
            });
          }

          if (!detectedType && item.is_virtual_variant === false) {
            // Check for multi units later after initSatuanGrid
          }

          if (item.group_discounts?.length) {
            groupDiscounts = item.group_discounts.map((gd) => ({
              group_id: gd.group_id,
              disc1: gd.disc1 || 0,
              disc2: gd.disc2 || 0,
              disc3: gd.disc3 || 0,
              disc4: gd.disc4 || 0,
            }));
          }
          if (item.suppliers?.length) {
            item.suppliers.forEach((s) => {
              selectedSups.set(s.id, {
                id: s.id,
                name: s.name,
                code: s.code || "",
              });
            });
          }
          if (item.supplier_details?.length) {
            item.supplier_details.forEach((d) => {
              supplierSettingsMap[d.supplier_id] = {
                buy_price: d.buy_price,
                barcode: d.barcode,
                ppn_type: d.ppn_type || "included",
                ppn_percent: d.ppn_percent || 0,
              };
            });
          }
          renderSelectedSups();
          // Auto-select jika cuma ada 1 supplier
          if (item.suppliers?.length === 1) {
            const firstSupId = item.suppliers[0].id;
            document.getElementById("fSupplierContext").value = firstSupId;
            onSupplierContextChange();
          }
        }

        initSatuanGrid(item).then(() => {
          // Detection part 2: Satuan
          if (item && !detectedType && satuanRows.length > 2) {
            // Index 0 is base, last one is draft. If length > 2, there's at least one real conversion.
            detectedType = "satuan";
          }

          if (detectedType) {
            loadAdvancedContent(detectedType);
            const activeId =
              "btnAdv" +
              (detectedType === "levelHarga"
                ? "Harga"
                : detectedType === "levelJumlah"
                  ? "Jumlah"
                  : "Satuan");
            advButtons.forEach((id) => {
              if (id !== activeId) {
                document.getElementById(id).classList.add("disabled");
              }
            });
          }
        });

        updateDiscountPreview();
        openModal("mBarang");
        // Form terbuka → besar kemungkinan user membuka tab harga lanjutan. Hangatkan
        // cache template-nya saat idle supaya klik tab pertama pun langsung muncul.
        if (window.requestIdleCallback)
          requestIdleCallback(prefetchAdvancedTemplates);
        else setTimeout(prefetchAdvancedTemplates, 300);
        setTimeout(() => document.getElementById("fNama").focus(), 100);
      }

      async function editItem(id) {
        try {
          showLoading("Memuat data...");
          const item = await api("GET", `/items/${id}`);
          hideLoading();
          openItemModal(item, (editMode = true));
        } catch (e) {
          hideLoading();
          showToast("Gagal memuat detail barang: " + e.message, "error");
        }
      }

      async function saveItem(e) {
        e.preventDefault();
        const lastSupplierId =
          document.getElementById("fSupplierContext").value;
        const lastBuyPrice = toAngka(document.getElementById("fHBeli").value);
        const lastBarcode = document.getElementById("fBarcode").value;
        const lastPpnType = document.getElementById("fPpn").value;
        // Mode "none" (Tanpa PPN) → tarif barang dipaksa 0 (bebas PPN). Override ini
        // mengalir ke setelan umum/supplier & ppn_percent item di payload.
        const lastPpnPercent =
          lastPpnType === "none"
            ? 0
            : parseDesimal(document.getElementById("fPpnPercent"));
        if (lastSupplierId === "" || lastSupplierId === "UNSELECTED") {
          generalBuyPrice = lastBuyPrice;
          generalBarcode = lastBarcode;
          generalPpnType = lastPpnType;
          generalPpnPercent = lastPpnPercent;
        } else {
          supplierSettingsMap[lastSupplierId] = {
            buy_price: lastBuyPrice,
            barcode: lastBarcode,
            ppn_type: lastPpnType,
            ppn_percent: lastPpnPercent,
          };
        }
        const sellPrice = toAngka(document.getElementById("fHJual").value);
        const normalizePrice = (value) =>
          Math.round(((Number(value) || 0) + Number.EPSILON) * 100) / 100;
        const buyPriceChanged =
          normalizePrice(generalBuyPrice) !== normalizePrice(initialBuyPrice);
        const sellPriceChanged =
          normalizePrice(sellPrice) !== normalizePrice(initialSellPrice);
        if (editItemId && (buyPriceChanged || sellPriceChanged)) {
          let msg = "Deteksi perubahan harga UTAMA:\n";
          if (buyPriceChanged)
            msg += `- Harga Beli: ${fmtRp(initialBuyPrice)} ➜ ${fmtRp(generalBuyPrice)}\n`;
          if (sellPriceChanged)
            msg += `- Harga Jual: ${fmtRp(initialSellPrice)} ➜ ${fmtRp(sellPrice)}\n`;
          msg += "\nSimpan perubahan?";
          if (
            !(await showConfirm(msg, {
              confirmText: "Setuju & Ubah",
              initialFocus: "confirm",
            }))
          )
            return;
        }
        let multi_prices = [];
        // Harga Diskon selalu disertakan jika ada, karena posisinya di luar tab Harga Lanjutan
        if (toAngka(document.getElementById("fHJualDiskon").value) > 0)
          multi_prices.push({
            name: "Harga Diskon",
            price: toAngka(document.getElementById("fHJualDiskon").value),
            min_qty: 1,
          });

        if (currentAdvancedType === "levelHarga") {
          for (const [lowerName, price] of Object.entries(groupPrices)) {
            if (price > 0 && lowerName !== "harga diskon") {
              const g = allGroups.find(
                (x) => x.name.toLowerCase() === lowerName,
              );
              multi_prices.push({
                name: g ? g.name : lowerName,
                price: price,
                min_qty: 1,
              });
            }
          }
        } else if (currentAdvancedType === "levelJumlah") {
          if (satuanRows.length > 0 && satuanRows[0].tier_prices) {
            satuanRows[0].tier_prices.forEach((t) => {
              if (t.min_qty > 0 && t.price > 0)
                multi_prices.push({
                  name: "Grosir",
                  price: t.price,
                  min_qty: t.min_qty,
                });
            });
          }
        }

        let settingsArr = [];
        selectedSups.forEach((sVal, sid) => {
          const s = supplierSettingsMap[sid] ||
            supplierSettingsMap[sid.toString()] || {
              buy_price: generalBuyPrice,
              barcode: generalBarcode,
              ppn_type: generalPpnType,
              ppn_percent: generalPpnPercent,
            };
          settingsArr.push({
            supplier_id: sid,
            buy_price: s.buy_price,
            barcode: s.barcode || null,
            ppn_type: s.ppn_type || "included",
            ppn_percent: s.ppn_percent || 0,
          });
        });
        readPotonganGrid();
        const p = {
          code: document.getElementById("fKodeItem").value || "AUTO",
          barcode: generalBarcode || "AUTO",
          name: document.getElementById("fNama").value,
          category_id: parseInt(document.getElementById("fKat").value) || null,
          brand_id: parseInt(document.getElementById("fMerek").value) || null,
          unit_id: parseInt(document.getElementById("fSat").value) || null,
          buy_price: generalBuyPrice,
          sell_price: sellPrice,
          profit_margin: parseInputPersen(
            document.getElementById("fMargin").value,
          ),
          // Tarif PPN barang (satu angka, dipakai beli & jual). Sumber: field tarif PPN.
          ppn_percent: lastPpnPercent,
          min_stock: toAngka(document.getElementById("fStokMin").value),
          description: document.getElementById("fDesk").value || null,
          is_discountable: document.getElementById("fIsDiscountable").checked,
          supplier_ids: Array.from(selectedSups.keys()),
          supplier_settings: settingsArr,
          prices: multi_prices,
          group_discounts: groupDiscounts.filter(
            (gd) => gd.disc1 || gd.disc2 || gd.disc3 || gd.disc4,
          ),
        };
        try {
          const isCreate = !editItemId; // simpan status sebelum editItemId ditimpa id baru
          let saved;
          if (editItemId) saved = await api("PUT", `/items/${editItemId}`, p);
          else saved = await api("POST", "/items/", p);
          editItemId = saved.id;

          // Foto barang: upload / hapus. Disimpan sebagai FILE di server (bukan di DB).
          // Dilakukan setelah barang tersimpan agar id-nya sudah pasti ada.
          try {
            if (fotoBaru) {
              const fd = new FormData();
              fd.append("file", fotoBaru, `item_${editItemId}.webp`);
              await apiForm(`/items/${editItemId}/image`, fd);
            } else if (hapusFotoFlag) {
              await api("DELETE", `/items/${editItemId}/image`);
            }
            fotoBaru = null;
            hapusFotoFlag = false;
          } catch (imgErr) {
            showToast(
              "Barang tersimpan, tapi foto gagal diproses: " + imgErr.message,
              "error",
            );
          }

          if (currentAdvancedType === "satuan") {
            await saveAllSatuanRows();
          }
          showToast("Barang disimpan ✓", "success");

          // Hook untuk halaman lain (mis. assembly.html) yang membuat barang dari dalam
          // alur mereka sendiri. Bila di-set & ini pembuatan barang BARU, serahkan barang
          // yang tersimpan ke callback lalu lewati alur daftar/clone (yang butuh #tblBarang).
          if (isCreate && typeof window.onItemCreated === "function") {
            const cb = window.onItemCreated;
            closeModal("mBarang");
            cb(saved);
            return;
          }

          if (
            await showConfirm(
              "Barang berhasil disimpan. Apakah ingin menambah lagi dengan data yang sama?",
            )
          ) {
            editItemId = null;
            resetFotoUI(null); // clone = barang baru → mulai tanpa foto
            document.getElementById("mBarangTitle").textContent =
              "Tambah Barang (Clone)";
            document.getElementById("fKodeItem").value = "AUTO";
            // Barcode direset ke AUTO agar tidak duplikat dengan yang baru disimpan
            document.getElementById("autoBarcode").checked = true;
            toggleBarcodeUI();
            document.getElementById("fBarcode").value = "AUTO";

            loadItems();
            setTimeout(() => document.getElementById("fNama").focus(), 100);
          } else {
            closeModal("mBarang");
            loadItems();
          }
        } catch (ex) {
          showToast(ex.message, "error");
        }
      }

      async function toggleItem(id, isActive) {
        if (!(await showConfirm(isActive ? "Nonaktifkan?" : "Aktifkan?")))
          return;
        try {
          if (isActive) await api("DELETE", `/items/${id}`);
          else await api("PUT", `/items/${id}`, { is_active: true });
          showToast("Ok");
          loadItems();
        } catch (ex) {
          showToast(ex.message, "error");
        }
      }

      function quickAddSat() {
        fromItemModal = true;
        closeModal("mBarang");
        openSatModal();
      }

      function loadModalEdit() {
        try {
          document.getElementById("editItemContainer").innerHTML = `
        <div class="modal-overlay" id="mEditHarga" style="z-index: 10000;">
          <div class="modal-box" style="max-width: 420px;">
            <div class="modal-hdr">
              <h2 style="margin: 0; font-size: 18px;">⚙️ Ganti Settingan Harga</h2>
              <button type="button" class="btn-x" onclick="closeModal('mEditHarga')">×</button>
            </div>

            <h3 style="margin: 4px 0 18px; font-size: 15px; color: var(--text-muted); font-weight: 600; line-height: 1.5;">
              Apakah Anda ingin mengganti settingan harga?
            </h3>

            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
              <button
                type="button"
                onclick="confirmEditHarga()"
                style="
                  padding: 13px;
                  border-radius: 10px;
                  border: none;
                  background: #10b981;
                  color: #fff;
                  font-size: 15px;
                  font-weight: 700;
                  cursor: pointer;
                  font-family: inherit;
                "
              >
                Iya
              </button>
              <button
                type="button"
                onclick="closeModal('mEditHarga')"
                style="
                  padding: 13px;
                  border-radius: 10px;
                  border: none;
                  background: #ef4444;
                  color: #fff;
                  font-size: 15px;
                  font-weight: 700;
                  cursor: pointer;
                  font-family: inherit;
                "
              >
                Tidak
              </button>
            </div>
          </div>
        </div>
      `;
          openModal("mEditHarga");
        } catch (e) {
          showToast("Gagal memuat form", "error");
        }
      }

      // Dipanggil saat user menekan "Iya" di modal Ganti Settingan Harga:
      // tutup modal lalu buka editor harga lanjutan berdasarkan satuan.
      function confirmEditHarga() {
        closeModal("mEditHarga");
        const advButtons = ["btnAdvSatuan", "btnAdvHarga", "btnAdvJumlah"];
        advButtons.forEach((id) => {
          const btn = document.getElementById(id);
          if (btn) {
            btn.classList.remove("disabled");
            btn.style.background = "var(--card-bg)";
            btn.style.color = "var(--text-main)";
            btn.style.borderColor = "var(--border-color)";
          }
        });

        // CLEAR DATA to start from scratch as requested
        groupPrices = {};
        initialGroupPrices = {};
        if (satuanRows.length > 0) {
          const base = satuanRows[0];
          // Reset tier prices in base row
          base.tier_prices = [];
          // Reset group prices in base row
          base.group_prices = {};
          satuanRows = [base];
          ensureEmptySatuanRow();
        }

        currentAdvancedType = null;
        const advContent = document.getElementById("advancedContent");
        if (advContent) {
          advContent.innerHTML = "";
          advContent.style.display = "none";
        }
        showToast(
          "Settingan harga direset. Silakan pilih kembali jenis pengaturan harga baru.",
          "info",
        );
      }
      async function addSupplier() {
        try {
          document.getElementById("addSupContainer").innerHTML = `
        <div class="modal-overlay" id="mPilihSup" style="z-index: 10000;">
          <div class="modal-box" style="max-width: 440px;">
            <div class="modal-hdr">
              <h2 style="margin: 0; font-size: 18px;">🚚 Pilih Supplier</h2>
              <button type="button" class="btn-x" onclick="closeModal('mPilihSup')">×</button>
            </div>

            <p style="margin: 0 0 14px; font-size: 13px; color: var(--text-muted); line-height: 1.45;">
              Cari supplier untuk mengatur <b>harga &amp; barcode khusus</b>, atau tambahkan supplier baru.
            </p>

            <div class="sup-search-box" style="max-width: none;">
              <span class="sup-search-icon">🔍</span>
              <input
                type="text"
                id="cariSupInput"
                class="sup-search-input"
                placeholder="Ketik nama / kode supplier..."
                autocomplete="off"
                oninput="cariSupplier()"
              />
            </div>

            <div
              id="cariSupResults"
              style="
                margin-top: 10px;
                max-height: 260px;
                overflow-y: auto;
                border: 1px solid var(--border-color);
                border-radius: 8px;
              "
            ></div>

            <button
              type="button"
              class="rcpt-btn"
              style="
                width: 100%;
                margin-top: 14px;
                background: #10b981;
                color: #fff;
                border: none;
              "
              onclick="quickAddSup()"
            >
              ➕ Tambah Supplier Baru
            </button>
          </div>
        </div>
          `;
          openModal("mPilihSup");
          const input = document.getElementById("cariSupInput");
          const results = document.getElementById("cariSupResults");
          if (input && results) setupCariSupplierKeyboard(input, results);
          if (input) input.focus();
          cariSupplier(); // tampilkan daftar awal (semua supplier)
        } catch (e) {
          showToast("Gagal memuat form", "error");
        }
      }

      // Pencarian live di dalam modal "Pilih Supplier". Tidak meng-exclude
      // selectedSups: user boleh memilih ulang supplier yang sudah ada untuk
      // melihat / mengubah setelan harga & barcode-nya.
      function cariSupplier() {
        const results = document.getElementById("cariSupResults");
        if (!results) return;
        clearTimeout(cariSupTimeout);
        cariSupTimeout = setTimeout(async () => {
          if (!allSups || allSups.length === 0) await loadSuppliers();
          const inputEl = document.getElementById("cariSupInput");
          const search = (inputEl ? inputEl.value : "").toLowerCase().trim();
          const matches = allSups.filter(
            (s) =>
              s.name.toLowerCase().includes(search) ||
              (s.code && s.code.toLowerCase().includes(search)),
          );
          cariSupHighlightedIndex = -1;
          inputEl?.removeAttribute("aria-activedescendant");
          if (matches.length === 0) {
            results.innerHTML = `<div class="sup-dropdown-empty">${
              search ? "Tidak ditemukan" : "Belum ada supplier"
            }</div>`;
            return;
          }
          results.innerHTML = matches
            .slice(0, 50)
            .map(
              (s) =>
                `<div id="cari-sup-option-${s.id}" role="option" aria-selected="false" class="sup-dropdown-item" data-id="${s.id}" style="cursor: pointer;" onclick="pilihSupplierKonteks(${s.id})"><span>${s.name}${
                  s.code ? ` <small>[${s.code}]</small>` : ""
                }</span><span class="sup-add-btn">+</span></div>`,
            )
            .join("");
        }, 150);
      }

      function setupCariSupplierKeyboard(input, results) {
        if (input.dataset.keyboardReady === "true") return;
        input.dataset.keyboardReady = "true";
        input.setAttribute("role", "combobox");
        input.setAttribute("aria-autocomplete", "list");
        input.setAttribute("aria-expanded", "true");
        results.setAttribute("role", "listbox");

        input.addEventListener("keydown", (event) => {
          const options = getSupplierSearchDropdownOptions(results);
          const isNextKey =
            event.key === "ArrowDown" || event.key === "ArrowRight";
          const isPreviousKey =
            event.key === "ArrowUp" || event.key === "ArrowLeft";

          if (isNextKey || isPreviousKey) {
            if (!options.length) return;
            event.preventDefault();
            event.stopPropagation();
            cariSupHighlightedIndex =
              (cariSupHighlightedIndex + (isNextKey ? 1 : -1) + options.length) %
              options.length;
            options.forEach((option, index) => {
              const active = index === cariSupHighlightedIndex;
              option.classList.toggle("highlighted", active);
              option.setAttribute("aria-selected", String(active));
            });
            const activeOption = options[cariSupHighlightedIndex];
            input.setAttribute("aria-activedescendant", activeOption.id);
            activeOption.scrollIntoView({ block: "nearest" });
            return;
          }

          if (event.key === "Enter") {
            const option =
              options[cariSupHighlightedIndex >= 0 ? cariSupHighlightedIndex : 0];
            if (!option) return;
            event.preventDefault();
            event.stopPropagation();
            option.click();
            return;
          }

          if (event.key === "Tab") {
            const option = options[cariSupHighlightedIndex];
            if (option) option.click();
            return;
          }

          if (event.key === "Escape" && options.length) {
            event.preventDefault();
            event.stopPropagation();
            results.replaceChildren();
            cariSupHighlightedIndex = -1;
            input.removeAttribute("aria-activedescendant");
          }
        });
      }

      // Saat hasil pencarian diklik: aktifkan supplier sebagai konteks setelan
      // harga & barcode (otomatis mengubah dropdown fSupplierContext).
      function pilihSupplierKonteks(id) {
        addSupToSelectionById(id);
        const select = document.getElementById("fSupplierContext");
        if (select) {
          select.value = String(id);
          onSupplierContextChange();
        }
        closeModal("mPilihSup");
      }
      async function quickAddSup() {
        try {
          const resp = await fetch("/supplier/tambahSuplier.html");
          const html = await resp.text();
          const parser = new DOMParser();
          const doc = parser.parseFromString(html, "text/html");
          const form = doc.querySelector("form");
          if (form) {
            document.getElementById("quickSupContainer").innerHTML =
              `<div class="modal-overlay" id="mQuickSup" style="z-index: 10001;"><div class="modal-box" style="max-width: 600px;"><div class="modal-hdr"><h2>Tambah Supplier</h2><button type="button" class="btn-x" onclick="closeModal('mQuickSup')">×</button></div><div class="modal-body" style="padding:0;">${form.outerHTML}</div></div></div>`;
            const modalBody = document.querySelector("#mQuickSup .modal-body");
            modalBody.querySelector("h2")?.remove();
            modalBody
              .querySelector("button[onclick*='history.back']")
              ?.remove();
            const injectedForm = modalBody.querySelector("form");
            if (injectedForm) injectedForm.onsubmit = (e) => saveQuickSup(e);
            openModal("mQuickSup");
          }
        } catch (e) {
          showToast("Gagal memuat form", "error");
        }
      }

      async function saveQuickSup(e) {
        if (e?.preventDefault) e.preventDefault();
        const name = document.querySelector("#mQuickSup #sNama").value.trim();
        if (!name) {
          showToast("Nama wajib diisi", "error");
          return;
        }
        const data = {
          name,
          phone: document.querySelector("#mQuickSup #sPhone")?.value || null,
          email: document.querySelector("#mQuickSup #sEmail")?.value || null,
          address: document.querySelector("#mQuickSup #sAlamat")?.value || null,
          PpnSupplier: parseDesimal(
            document.querySelector("#mQuickSup #PpnSupplier"),
          ),
          credit_limit: parseDesimal(
            document.querySelector("#mQuickSup #sCredit"),
          ),
          notes: document.querySelector("#mQuickSup #sNotes")?.value || null,
          item_ids: [],
        };
        try {
          const btn = document.querySelector("#mQuickSup #btnSimpan");
          if (btn) {
            btn.disabled = true;
            btn.textContent = "Menyimpan...";
          }
          const newSup = await api("POST", "/suppliers/", data);
          showToast("Berhasil!");
          closeModal("mQuickSup");
          await loadSuppliers();
          const select = document.getElementById("fSupplierContext");
          updateSupplierContextDropdown();
          select.value = newSup.id;
          onSupplierContextChange();
          setTimeout(() => select?.focus({ preventScroll: true }), 0);
        } catch (ex) {
          showToast(ex.message, "error");
          if (btn) {
            btn.disabled = false;
            btn.textContent = "✓ Simpan";
          }
        }
      }

      async function initSatuanGrid(item = null) {
        const gridBaseUnitSelect = document.getElementById("fSatuanDasarGrid");
        if (gridBaseUnitSelect) {
          gridBaseUnitSelect.innerHTML =
            document.getElementById("fSat").innerHTML;
          gridBaseUnitSelect.value = document.getElementById("fSat").value;
        }
        const baseUnitId =
          parseInt(document.getElementById("fSat")?.value) || 0;
        const baseRow = {
          is_base: true,
          child_unit_id: baseUnitId,
          conversion_factor: 1,
          buy_price_auto:
            toAngka(document.getElementById("fHBeli")?.value) || 0,
          sell_price: toAngka(document.getElementById("fHJual")?.value) || 0,
          margin_percent: parseInputPersen(
            document.getElementById("fMargin")?.value,
          ),
          group_prices: {},
          tier_prices: [],
        };

        if (item && item.prices) {
          const grosirs = item.prices
            .filter((p) => p.name === "Grosir")
            .sort((a, b) => a.min_qty - b.min_qty);
          baseRow.tier_prices = grosirs.map((g) => ({
            min_qty: g.min_qty,
            price: g.price,
          }));
        }

        satuanRows = [baseRow];
        syncGroupPricesFromMain();
        if (editItemId && !window._editFromBOM) {
          try {
            const data = await api(
              "GET",
              `/unit-conversion/config/${editItemId}`,
            );
            masterUnits = data.units || [];
            const existingConvs = (data.rows || []).map((row) => ({
              conversion_id: row.conversion_id,
              child_item_id: row.child_item_id,
              child_code: row.child_code || "",
              child_name: row.child_name || "",
              child_unit_id: row.child_unit_id,
              conversion_factor: row.conversion_factor || 1,
              buy_price_auto: row.buy_price_auto || 0,
              sell_price: row.sell_price || 0,
              margin_percent: row.margin_percent || 0,
              group_prices: row.group_prices || {},
              tier_prices: row.tier_prices || [],
            }));
            satuanRows = satuanRows.concat(existingConvs);
          } catch (e) {
            console.error("Gagal load multi satuan", e);
          }
        }
        ensureEmptySatuanRow();
        if (currentAdvancedType) renderAdvancedGrid();
      }

      function ensureEmptySatuanRow() {
        const last = satuanRows[satuanRows.length - 1];
        if (!last || last.child_unit_id || last.is_base) {
          satuanRows.push({
            is_draft: true,
            child_unit_id: "",
            conversion_factor: 0,
            buy_price_auto: 0,
            sell_price: 0,
            margin_percent: 0,
            group_prices: {},
            tier_prices: [],
          });
        }
      }

      function removeSatuanRow(idx) {
        satuanRows.splice(idx, 1);
        ensureEmptySatuanRow();
        if (currentAdvancedType) renderAdvancedGrid();
      }

      function finishAdvancedDeleteConfirmation(confirmed) {
        const resolve = advancedDeleteModalResolver;
        const returnFocus = advancedDeleteModalReturnFocus;
        advancedDeleteModalResolver = null;
        advancedDeleteModalReturnFocus = null;
        closeModal("mHapusKonversi");

        if (!confirmed && returnFocus?.isConnected) {
          setTimeout(() => returnFocus.focus({ preventScroll: true }), 0);
        }
        resolve?.(confirmed);
      }

      function setupAdvancedDeleteModal() {
        const modal = document.getElementById("mHapusKonversi");
        if (!modal || modal.dataset.ready === "true") return;

        const cancelButton = document.getElementById("btnBatalHapusKonversi");
        const closeButton = document.getElementById("btnTutupHapusKonversi");
        const confirmButton = document.getElementById(
          "btnKonfirmasiHapusKonversi",
        );
        if (!cancelButton || !closeButton || !confirmButton) return;

        modal.dataset.ready = "true";
        cancelButton.addEventListener("click", () =>
          finishAdvancedDeleteConfirmation(false),
        );
        closeButton.addEventListener("click", () =>
          finishAdvancedDeleteConfirmation(false),
        );
        confirmButton.addEventListener("click", () =>
          finishAdvancedDeleteConfirmation(true),
        );
        modal.addEventListener("click", (event) => {
          if (event.target === modal) finishAdvancedDeleteConfirmation(false);
        });
        document.addEventListener(
          "keydown",
          (event) => {
            if (
              event.key !== "Escape" ||
              !advancedDeleteModalResolver ||
              modal.style.display !== "flex"
            )
              return;
            event.preventDefault();
            event.stopPropagation();
            finishAdvancedDeleteConfirmation(false);
          },
          true,
        );
      }

      function requestAdvancedDeleteConfirmation(row) {
        setupAdvancedDeleteModal();
        const modal = document.getElementById("mHapusKonversi");
        const message = document.getElementById("mHapusKonversiMessage");
        const name = document.getElementById("mHapusKonversiName");
        const cancelButton = document.getElementById("btnBatalHapusKonversi");
        if (!modal || !message || !name || !cancelButton) return Promise.resolve(false);
        if (advancedDeleteModalResolver) return Promise.resolve(false);

        const isSaved = !!row.conversion_id;
        const rowName = String(
          row.child_name || row.child_unit_name || "Baris konversi ini",
        ).trim();
        message.textContent = isSaved
          ? "Baris ini beserta barang turunan terkait akan dinonaktifkan."
          : "Baris konversi baru ini akan dihapus dari formulir.";
        name.textContent = rowName || "Baris konversi ini";

        return new Promise((resolve) => {
          advancedDeleteModalResolver = resolve;
          advancedDeleteModalReturnFocus = document.activeElement;
          openModal("mHapusKonversi");
          setTimeout(() => {
            if (advancedDeleteModalResolver) {
              cancelButton.focus({ preventScroll: true });
            }
          }, 0);
        });
      }

      window.deleteAdvancedRow = async function (idx) {
        const row = satuanRows[idx];
        if (!row || row.is_base) return;

        const isSaved = !!row.conversion_id;
        if (!(await requestAdvancedDeleteConfirmation(row))) return;

        if (!isSaved) {
          removeSatuanRow(idx);
          showToast("Baris konversi dihapus", "success");
          return;
        }

        try {
          await api("DELETE", `/unit-conversion/variant/${row.conversion_id}`);
          removeSatuanRow(idx);
          showToast("Baris konversi dihapus", "success");
        } catch (e) {
          console.error("Gagal menghapus baris konversi", e);
          showToast(e?.message || "Gagal menghapus baris konversi", "error");
        }
      };

      document.addEventListener("click", (event) => {
        const target = event.target;
        const row = target?.closest?.(
          "#advancedContent tbody tr[data-advanced-row]",
        );
        if (!row) return;

        const control = target.closest?.(
          "input:not([disabled]), select:not([disabled]), textarea, button, a, [contenteditable='true']",
        );
        if (!control) row.focus({ preventScroll: true });
      });

      document.addEventListener(
        "keydown",
        (event) => {
          if (
            event.key !== "Delete" ||
            event.defaultPrevented ||
            event.ctrlKey ||
            event.altKey ||
            event.metaKey
          )
            return;

          const target = event.target;
          const row = target?.closest?.(
            "#advancedContent tbody tr[data-advanced-row]",
          );
          if (!row || row.classList.contains("base-row")) return;

          event.preventDefault();
          event.stopPropagation();
          void window.deleteAdvancedRow(Number(row.dataset.advancedRow));
        },
        true,
      );

      function captureAdvancedGridFocus(body) {
        const active = document.activeElement;
        if (!body || !active || !body.contains(active)) return null;

        const row = active.closest("tr");
        const cell = active.closest("td");
        if (!row || !cell) return null;

        const rows = Array.from(body.querySelectorAll("tr"));
        const rowControls = getAdvancedGridControls(row);
        const cellControls = Array.from(
          cell.querySelectorAll(ADVANCED_GRID_CONTROL_SELECTOR),
        );
        const controlIndex = cellControls.indexOf(active);
        if (controlIndex < 0) return null;

        return {
          rowKey: row.dataset.advancedRow ?? null,
          rowIndex: rows.indexOf(row),
          cellIndex: Array.from(row.cells).indexOf(cell),
          controlIndex,
          rowControlIndex: rowControls.indexOf(active),
          selectionStart:
            active instanceof HTMLInputElement ? active.selectionStart : null,
          selectionEnd:
            active instanceof HTMLInputElement ? active.selectionEnd : null,
        };
      }

      function restoreAdvancedGridFocus(body, focusState) {
        if (!body || !focusState || focusState.rowIndex < 0) return;

        const rows = Array.from(body.querySelectorAll("tr"));
        const row =
          rows.find(
            (candidate) =>
              focusState.rowKey != null &&
              candidate.dataset.advancedRow === focusState.rowKey,
          ) || rows[focusState.rowIndex];
        if (!row) return;

        const cell = row.cells?.[focusState.cellIndex];
        const cellControls = Array.from(
          cell?.querySelectorAll(ADVANCED_GRID_CONTROL_SELECTOR) || [],
        );
        const control =
          cellControls[focusState.controlIndex] ||
          getAdvancedGridControls(row)[focusState.rowControlIndex];
        if (!control) return;

        control.focus();
        if (
          control instanceof HTMLInputElement &&
          typeof focusState.selectionStart === "number" &&
          typeof focusState.selectionEnd === "number"
        ) {
          const max = control.value.length;
          control.setSelectionRange(
            Math.min(focusState.selectionStart, max),
            Math.min(focusState.selectionEnd, max),
          );
        }
      }

      function renderAdvancedGrid() {
        const body = document.querySelector("#advancedContent tbody");
        if (!body) return;

        const focusState = captureAdvancedGridFocus(body);

        // Render Header if levelHarga
        if (currentAdvancedType === "levelHarga") {
          const head = document.getElementById("satuanGridHead");
          if (head) {
            let headHtml = `<tr>
                        <th colspan="5" class="header-spacer"></th>
                        ${allGroups.map((g, i) => `<th colspan="2" class="group-label ${i > 0 ? "sep" : ""}">${i + 1}. ${g.name}</th>`).join("")}
                        <th rowspan="2" style="width: 44px">Aksi</th>
                      </tr>
                      <tr>
                        <th style="width: 30px"></th>
                        <th>Satuan</th>
                        <th>Jenis</th>
                        <th style="width: 60px" class="sep">Konv</th>
                        <th class="sep">H. Pokok</th>
                        ${allGroups.map((g) => `<th>% Mg</th><th class="sep">Harga</th>`).join("")}
                      </tr>`;
            head.innerHTML = headHtml;
          }
        } else if (currentAdvancedType === "levelJumlah") {
          const head = document.getElementById("satuanGridHead");
          if (head) {
            head.innerHTML = `<tr>
                        <th style="width: 30px"></th>
                        <th>Satuan</th>
                        <th>Jenis</th>
                        <th style="width: 60px" class="sep">Konv</th>
                        <th class="sep">H. Pokok</th>
                        <th colspan="4" class="tier-label sep">Tier 1</th>
                        <th colspan="4" class="tier-label sep">Tier 2</th>
                        <th colspan="4" class="tier-label sep">Tier 3</th>
                        <th colspan="4" class="tier-label">Tier 4</th>
                        <th rowspan="2" style="width: 44px">Aksi</th>
                      </tr>
                      <tr>
                        <th colspan="5"></th>
                        <th>Dari</th><th>Sampai</th><th>% Mg</th><th class="sep">Harga</th>
                        <th>Dari</th><th>Sampai</th><th>% Mg</th><th class="sep">Harga</th>
                        <th>Dari</th><th>Sampai</th><th>% Mg</th><th class="sep">Harga</th>
                        <th>Dari</th><th>Sampai</th><th>% Mg</th><th>Harga</th>
                      </tr>`;
          }
        }

        body.innerHTML = satuanRows
          .map((row, idx) => {
            const isBase = row.is_base,
              isDraft = row.is_draft;
            const canDelete = !isBase && (!isDraft || row.conversion_id);
            const unitOptions = (masterUnits || [])
              .map(
                (u) =>
                  `<option value="${u.id}" ${parseInt(row.child_unit_id) === u.id ? "selected" : ""}>${u.name}${u.abbreviation ? " (" + u.abbreviation + ")" : ""}</option>`,
              )
              .join("");
            let cols = `<td style="text-align:center; color:var(--primary); font-size:10px;">${isBase ? "▶" : isDraft ? "*" : "•"}</td><td><select class="input-control" onchange="updateAdvancedRowField(${idx}, 'child_unit_id', this.value)"><option value="">-- Pilih --</option>${unitOptions}</select></td><td><input class="input-control" value="${isBase ? "Dasar" : "Konversi"}" disabled></td><td class="sep"><input type="text" inputmode="decimal" data-input-desimal data-desimal-maks="4" data-min="0.0001" class="input-control" value="${row.conversion_factor || ""}" ${isBase ? "disabled" : ""} onchange="updateAdvancedRowField(${idx}, 'conversion_factor', this.value)" style="text-align:right;"></td><td class="sep"><input type="text" inputmode="decimal" data-input-desimal data-min="0" class="input-control" value="${toRibuan(row.buy_price_auto)}" oninput="formatInputRibuan(this)" onchange="updateAdvancedRowField(${idx}, 'buy_price_auto', this.value)" style="text-align:right;"></td>`;

            if (currentAdvancedType === "satuan") {
              cols += `<td><input type="text" inputmode="decimal" data-input-desimal data-min="0" class="input-control item-percent-input" value="${toPersen(row.margin_percent)}" oninput="formatInputPersen(this)" onchange="updateAdvancedRowField(${idx}, 'margin_percent', this.value)" style="text-align:right;"></td><td><input type="text" inputmode="decimal" data-input-desimal data-min="0" class="input-control" value="${toRibuan(row.sell_price)}" oninput="formatInputRibuan(this)" onchange="updateAdvancedRowField(${idx}, 'sell_price', this.value, 'sell')" style="text-align:right; font-weight:700; color:var(--primary)"></td>`;
            } else if (currentAdvancedType === "levelHarga") {
              allGroups.forEach((group, gIdx) => {
                let groupPrice = row.group_prices[group.id];
                const isUmum = gIdx === 0;

                // Hanya default-kan grup pertama (Umum) ke harga jual utama
                if (isUmum && (groupPrice === undefined || groupPrice === 0)) {
                  groupPrice = row.sell_price;
                  row.group_prices[group.id] = groupPrice;
                }

                const displayPrice = groupPrice > 0 ? toRibuan(groupPrice) : "";
                const groupMargin =
                  row.buy_price_auto > 0 && groupPrice > 0
                    ? ((groupPrice - row.buy_price_auto) / row.buy_price_auto) *
                      100
                    : 0;
                const displayMargin =
                  groupPrice > 0 ? groupMargin.toFixed(2) : "";

                cols += `<td><input type="text" inputmode="decimal" data-input-desimal data-min="0" class="input-control item-percent-input" value="${toPersen(displayMargin)}" oninput="formatInputPersen(this)" onchange="updateAdvancedRowField(${idx}, 'group_margin', this.value, '${group.id}')" style="text-align:right;"></td><td class="sep"><input type="text" inputmode="decimal" data-input-desimal data-min="0" class="input-control" value="${displayPrice}" oninput="formatInputRibuan(this)" onchange="updateAdvancedRowField(${idx}, 'group_price', this.value, '${group.id}')" style="text-align:right; font-weight:700; color:var(--primary)"></td>`;
              });
            } else if (currentAdvancedType === "levelJumlah") {
              for (let i = 0; i < 4; i++) {
                const tier = row.tier_prices[i] || { min_qty: 0, price: 0 };
                // Logika Range:
                // Dari Qty (Tier 1) = 1
                // Dari Qty (Tier N+1) = Sampai Qty (Tier N) + 1
                let dariQty = 1;
                if (i > 0) {
                  const prevTier = row.tier_prices[i - 1];
                  dariQty =
                    prevTier && prevTier.sampai_qty
                      ? prevTier.sampai_qty + 1
                      : 0;
                }
                // Jika i > 0 dan dariQty masih 0 (karena tier sebelumnya belum ada sampai_qty), maka sembunyikan tier ini
                if (i > 0 && !dariQty) {
                  cols += `<td colspan="4" style="background:var(--bg-color); border-left:1px solid var(--border-color)"></td>`;
                  continue;
                }

                const tierMargin =
                  row.buy_price_auto > 0 && tier.price > 0
                    ? ((tier.price - row.buy_price_auto) / row.buy_price_auto) *
                      100
                    : 0;
                const marginVal = tier.price > 0 ? tierMargin.toFixed(2) : "";
                const priceVal = tier.price > 0 ? toRibuan(tier.price) : "";
                const sampaiVal = tier.sampai_qty || "";

                cols += `<td style="text-align:center; font-weight:600; color:var(--text-muted); font-size:12px; border-left:1px solid var(--border-color)">${dariQty}</td>
                         <td><input type="text" inputmode="decimal" data-input-desimal data-desimal-maks="4" data-min="0" class="input-control" value="${sampaiVal ? toDesimal(sampaiVal, { maximumFractionDigits: 4 }) : ""}" oninput="formatInputRibuan(this)" onchange="updateAdvancedRowField(${idx}, 'tier_sampai', this.value, ${i})" style="text-align:right;" placeholder="∞"></td>
                         <td><input type="text" inputmode="decimal" data-input-desimal data-min="0" class="input-control item-percent-input" value="${toPersen(marginVal)}" oninput="formatInputPersen(this)" onchange="updateAdvancedRowField(${idx}, 'tier_margin', this.value, ${i})" style="text-align:right;" placeholder="% Mg"></td>
                         <td class="${i < 3 ? "sep" : ""}"><input type="text" inputmode="decimal" data-input-desimal data-min="0" class="input-control" value="${priceVal}" oninput="formatInputRibuan(this)" onchange="updateAdvancedRowField(${idx}, 'tier_price', this.value, ${i})" style="text-align:right; font-weight:700; color:var(--primary)" placeholder="Harga"></td>`;
              }
            }
            cols += canDelete
              ? `<td style="text-align:center; white-space:nowrap;"><button type="button" title="Hapus baris konversi" onclick="deleteAdvancedRow(${idx})" style="border:none; background:transparent; color:#ef4444; cursor:pointer; font-weight:700; font-size:16px; line-height:1; padding:4px 6px;">×</button></td>`
              : `<td style="text-align:center; color:var(--text-muted); font-size:12px;">&nbsp;</td>`;
            return `<tr class="${isBase ? "base-row" : ""} ${isDraft ? "empty-row" : ""}" data-advanced-row="${idx}" tabindex="-1"> ${cols} </tr>`;
          })
          .join("");

        restoreAdvancedGridFocus(body, focusState);
      }

      window.reverseSyncToBase = function (idx) {
        const row = satuanRows[idx];
        if (!row || row.is_base || row.conversion_factor <= 0) return;

        const factor = row.conversion_factor;
        const baseBuyPrice = Math.round(row.buy_price_auto / factor);

        // Update form utama
        const fHBeli = document.getElementById("fHBeli");
        const fHJual = document.getElementById("fHJual");
        const fMargin = document.getElementById("fMargin");

        if (fHBeli) fHBeli.value = toRibuan(baseBuyPrice);

        // JANGAN ubah fHJual. Biarkan harga jual dasar tetap, hanya update margin dasar.
        if (fMargin && fHJual) {
          const currentBaseSell = toAngka(fHJual.value) || 0;
          const margin =
            baseBuyPrice > 0
              ? ((currentBaseSell - baseBuyPrice) / baseBuyPrice) * 100
              : 0;
          fMargin.value = toPersen(margin);
        }

        // Sinkronisasi ulang seluruh baris berdasarkan nilai dasar baru
        syncGroupPricesFromMain();
      };

      window.updateAdvancedRowField = function (idx, field, val, extra = null) {
        const row = satuanRows[idx];
        if (!row) return;
        // Field harga yang berlaku untuk SEMUA supplier -> beri tahu sekali
        if (
          [
            "group_price",
            "group_margin",
            "group_discount",
            "sell_price",
            "tier_price",
            "tier_margin",
          ].includes(field)
        )
          notifySharedFieldChange();
        if (field === "child_unit_id") {
          row.child_unit_id = val;
          if (row.is_base) {
            const fSat = document.getElementById("fSat");
            if (fSat) fSat.value = val;
          }
          if (row.is_draft) {
            row.is_draft = false;
            row.conversion_factor = 1;
            row.margin_percent = parseInputPersen(
              document.getElementById("fMargin")?.value,
            );
            ensureEmptySatuanRow();
          }
          const unit = masterUnits.find((u) => String(u.id) === String(val));
          if (unit)
            row.child_name = `${document.getElementById("fNama")?.value || "Barang"} ${unit.abbreviation || unit.name}`;
          recalcAdvancedRow(idx);
        } else if (field === "conversion_factor") {
          row.conversion_factor = parseDesimal(val, {
            maximumFractionDigits: 4,
          });
          recalcAdvancedRow(idx);
        } else if (field === "buy_price_auto") {
          row.buy_price_auto = toAngka(val);
          recalcAdvancedRow(idx, "buy");
          if (row.is_base) {
            const fHBeli = document.getElementById("fHBeli");
            if (fHBeli) fHBeli.value = toRibuan(row.buy_price_auto);
            syncGroupPricesFromMain();
          } else {
            reverseSyncToBase(idx);
          }
        } else if (field === "margin_percent") {
          row.margin_percent = parseInputPersen(val);
          recalcAdvancedRow(idx, "margin");
          if (row.is_base) {
            const fMargin = document.getElementById("fMargin");
            if (fMargin) fMargin.value = toPersen(row.margin_percent);
            const fHJual = document.getElementById("fHJual");
            if (fHJual) fHJual.value = toRibuan(row.sell_price);
            syncGroupPricesFromMain();
          }
        } else if (field === "sell_price") {
          row.sell_price = toAngka(val);
          recalcAdvancedRow(idx, "sell");
          if (row.is_base) {
            const fHJual = document.getElementById("fHJual");
            if (fHJual) fHJual.value = toRibuan(row.sell_price);
            const fMargin = document.getElementById("fMargin");
            if (fMargin) fMargin.value = toPersen(row.margin_percent);
            syncGroupPricesFromMain();
          }
        } else if (field === "group_price") {
          const groupId = extra;
          row.group_prices[groupId] = toAngka(val);
          if (row.is_base) {
            const group = allGroups.find(
              (g) => String(g.id) === String(groupId),
            );
            if (group) {
              groupPrices[group.name.toLowerCase()] = row.group_prices[groupId];
              // Sync back to main HJual if it's the first group of base row
              if (allGroups[0] && String(allGroups[0].id) === String(groupId)) {
                const fHJual = document.getElementById("fHJual");
                if (fHJual) {
                  fHJual.value = toRibuan(row.group_prices[groupId]);
                  calcMarginFromHJual();
                }
              }
            }
          }
        } else if (field === "group_discount") {
          const groupId = extra;
          const disc = parseInputPersen(val);
          row.group_prices[groupId] = Math.round(
            row.sell_price * (1 - disc / 100),
          );
          if (row.is_base) {
            const group = allGroups.find(
              (g) => String(g.id) === String(groupId),
            );
            if (group)
              groupPrices[group.name.toLowerCase()] = row.group_prices[groupId];
          }
        } else if (field === "group_margin") {
          const groupId = extra;
          const margin = parseInputPersen(val);
          row.group_prices[groupId] = Math.round(
            row.buy_price_auto * (1 + margin / 100),
          );
          if (row.is_base) {
            const group = allGroups.find(
              (g) => String(g.id) === String(groupId),
            );
            if (group) {
              groupPrices[group.name.toLowerCase()] = row.group_prices[groupId];
              // Sync back to main Margin if it's the first group of base row
              if (allGroups[0] && String(allGroups[0].id) === String(groupId)) {
                const fMargin = document.getElementById("fMargin");
                if (fMargin) {
                  fMargin.value = toPersen(margin);
                  calcHJualFromMargin();
                }
              }
            }
          }
        } else if (field === "tier_sampai") {
          const tierIdx = extra;
          let sampaiVal = parseDesimal(val, { maximumFractionDigits: 4 });

          // Cari Dari Qty untuk tier ini
          let dariQty = 1;
          if (tierIdx > 0) {
            const prevTier = row.tier_prices[tierIdx - 1];
            dariQty =
              prevTier && prevTier.sampai_qty ? prevTier.sampai_qty + 1 : 0;
          }

          // Validasi: Sampai harus >= Dari
          if (sampaiVal > 0 && sampaiVal < dariQty) {
            showToast(`Qty Sampai harus ≥ ${dariQty}`, "error");
            sampaiVal = dariQty;
          }

          if (!row.tier_prices[tierIdx])
            row.tier_prices[tierIdx] = { min_qty: 0, price: 0 };

          row.tier_prices[tierIdx].sampai_qty = sampaiVal;

          // Cascade update: Sesuaikan min_qty (Dari) untuk semua tier berikutnya
          for (let j = tierIdx; j < 3; j++) {
            const currentSampai = row.tier_prices[j]?.sampai_qty;
            if (!row.tier_prices[j + 1])
              row.tier_prices[j + 1] = { min_qty: 0, price: 0 };

            const nextDari = currentSampai ? currentSampai + 1 : 0;
            row.tier_prices[j + 1].min_qty = nextDari;

            // Jika Sampai tier berikutnya sudah diisi tapi jadi tidak valid (lebih kecil dari Dari baru)
            if (
              row.tier_prices[j + 1].sampai_qty > 0 &&
              row.tier_prices[j + 1].sampai_qty < nextDari
            ) {
              row.tier_prices[j + 1].sampai_qty = nextDari;
            }
          }
        } else if (field === "tier_price") {
          const tierIdx = extra;
          if (!row.tier_prices[tierIdx])
            row.tier_prices[tierIdx] = { min_qty: 0, price: 0 };
          row.tier_prices[tierIdx].price = toAngka(val);
        } else if (field === "tier_margin") {
          const tierIdx = extra;
          if (!row.tier_prices[tierIdx])
            row.tier_prices[tierIdx] = { min_qty: 0, price: 0 };
          const margin = parseInputPersen(val);
          // Harga tier = H. Pokok + margin% (konsisten dgn margin utama & % Mg level harga)
          row.tier_prices[tierIdx].price = Math.round(
            row.buy_price_auto * (1 + margin / 100),
          );
        }
        renderAdvancedGrid();
      };

      function recalcAdvancedRow(idx, changedBy = "factor") {
        const row = satuanRows[idx];
        if (!row) return;

        // Simpan % margin saat ini sebelum buy/sell price berubah (untuk factor change)
        const groupMargins = {};
        const tierMargins = [];
        if (changedBy === "factor") {
          allGroups.forEach((g) => {
            let p = row.group_prices[g.id];
            if (p !== undefined && p !== 0) {
              groupMargins[g.id] =
                row.buy_price_auto > 0
                  ? ((p - row.buy_price_auto) / row.buy_price_auto) * 100
                  : row.margin_percent || 0;
            } else {
              groupMargins[g.id] = row.margin_percent || 0;
            }
          });
          for (let i = 0; i < 4; i++) {
            const t = row.tier_prices[i] || { price: 0 };
            tierMargins[i] =
              row.buy_price_auto > 0 && t.price > 0
                ? ((t.price - row.buy_price_auto) / row.buy_price_auto) * 100
                : 0;
          }
        }

        // Jika bukan buy, margin, atau sell yang dirubah langsung (misal: factor atau base price utama berubah)
        // Maka buy_price_auto dihitung ulang dari base.
        if (changedBy === "factor" || changedBy === "base") {
          row.buy_price_auto = Math.round(
            (toAngka(document.getElementById("fHBeli")?.value) || 0) *
              row.conversion_factor,
          );
        }

        if (changedBy === "margin")
          row.sell_price = Math.round(
            row.buy_price_auto * (1 + row.margin_percent / 100),
          );
        else if (changedBy === "sell")
          row.margin_percent =
            row.buy_price_auto > 0
              ? parseFloat(
                  (
                    ((row.sell_price - row.buy_price_auto) /
                      row.buy_price_auto) *
                    100
                  ).toFixed(2),
                )
              : 0;
        else if (changedBy === "buy") {
          // Jika buy_price_auto dirubah manual, kita hitung ulang sell_price berdasarkan margin yang ada
          row.sell_price = Math.round(
            row.buy_price_auto * (1 + row.margin_percent / 100),
          );
        } else {
          // Default: hitung sell dari buy yang sudah ada
          row.sell_price = Math.round(
            row.buy_price_auto * (1 + row.margin_percent / 100),
          );
        }

        // Jika factor berubah, update nominal harga berdasarkan % yang disimpan tadi
        if (changedBy === "factor") {
          allGroups.forEach((g) => {
            row.group_prices[g.id] = Math.round(
              row.buy_price_auto * (1 + groupMargins[g.id] / 100),
            );
          });
          for (let i = 0; i < 4; i++) {
            if (row.tier_prices[i] && row.tier_prices[i].price > 0) {
              row.tier_prices[i].price = Math.round(
                row.buy_price_auto * (1 + tierMargins[i] / 100),
              );
            }
          }
        }
      }

      async function saveAllSatuanRows() {
        for (let i = 1; i < satuanRows.length; i++) {
          const row = satuanRows[i];
          if (row.is_draft || !row.child_unit_id) continue;
          let multi = [];
          for (let gid in row.group_prices) {
            const g = allGroups.find((x) => String(x.id) === String(gid));
            if (g)
              multi.push({
                name: g.name,
                price: row.group_prices[gid],
                min_qty: 1,
              });
          }
          row.tier_prices.forEach((t) => {
            if (t.min_qty > 0)
              multi.push({
                name: `Grosir`,
                price: t.price,
                min_qty: t.min_qty,
              });
          });
          const payload = {
            source_item_id: editItemId,
            child_name: row.child_name || "Turunan",
            child_unit_id: parseInt(row.child_unit_id),
            conversion_factor: row.conversion_factor,
            sell_price: row.sell_price,
            prices: multi,
          };
          try {
            if (row.conversion_id)
              await api(
                "PUT",
                `/unit-conversion/variant/${row.conversion_id}`,
                payload,
              );
            else await api("POST", "/unit-conversion/variant", payload);
          } catch (e) {
            console.error(e);
          }
        }
      }

      async function toggleGroupPricePreview() {
        const container = document.getElementById("groupPricePreviewContainer");
        const body = document.getElementById("groupPricePreviewBody");
        if (!container || !body) return;

        if (container.style.display === "block") {
          container.style.display = "none";
        } else {
          try {
            const groups = await api("GET", "/customers/groups");
            body.innerHTML = groups.length
              ? groups
                  .map(
                    (g) => `
                        <tr style="border-bottom: 1px solid var(--border-color)">
                          <td style="padding: 8px 0; color: var(--text-main); font-weight: 500">${g.name}</td>
                          <td style="padding: 8px 0; text-align: right; color: var(--primary); font-weight: 700; font-size: 14px">${g.discount_percent}%</td>
                        </tr>
                      `,
                  )
                  .join("")
              : '<tr><td colspan="2" style="text-align: center; padding: 20px; color: var(--text-muted)">Belum ada grup diskon</td></tr>';

            container.style.display = "block";
          } catch (ex) {
            showToast("Gagal memuat grup: " + ex.message, "error");
          }
        }
      }
      window.toggleGroupPricePreview = toggleGroupPricePreview;

      function potonganGroupOptions(selectedId) {
        return (
          '<option value="">-- Pilih Grup --</option>' +
          allGroups
            .map(
              (g) =>
                `<option value="${g.id}" ${String(g.id) === String(selectedId) ? "selected" : ""}>${g.name}</option>`,
            )
            .join("")
        );
      }

      function potonganRowHtml(gd, isEmpty) {
        const v = (n) => (gd && gd[n] ? gd[n] : "");
        return `<tr class="${isEmpty ? "empty-row" : ""}">
                    <td style="text-align:center;color:var(--text-muted);font-size:14px;">${isEmpty ? "*" : "▶"}</td>
                    <td><select class="input-control group-select" data-group-id="${gd ? gd.group_id : ""}" onchange="handlePotonganRowChange(this)">${potonganGroupOptions(gd ? gd.group_id : "")}</select></td>
                    <td class="sep"><input type="text" inputmode="decimal" data-input-desimal data-min="0" data-max="100" placeholder="0,00" class="input-control item-percent-input" style="text-align:right" value="${toPersen(v("disc1"))}" oninput="formatInputPersen(this); handlePotonganRowChange(this)"></td>
                    <td><input type="text" inputmode="decimal" data-input-desimal data-min="0" data-max="100" placeholder="0,00" class="input-control item-percent-input" style="text-align:right" value="${toPersen(v("disc2"))}" oninput="formatInputPersen(this); handlePotonganRowChange(this)"></td>
                    <td><input type="text" inputmode="decimal" data-input-desimal data-min="0" data-max="100" placeholder="0,00" class="input-control item-percent-input" style="text-align:right" value="${toPersen(v("disc3"))}" oninput="formatInputPersen(this); handlePotonganRowChange(this)"></td>
                    <td><input type="text" inputmode="decimal" data-input-desimal data-min="0" data-max="100" placeholder="0,00" class="input-control item-percent-input" style="text-align:right" value="${toPersen(v("disc4"))}" oninput="formatInputPersen(this); handlePotonganRowChange(this)"></td>
                  </tr>`;
      }

      function renderPotonganGrid() {
        const body = document.getElementById("potonganGrupBody");
        if (!body) return;
        let html = groupDiscounts
          .map((gd) => potonganRowHtml(gd, false))
          .join("");
        html += potonganRowHtml(null, true); // baris kosong untuk tambah baru
        body.innerHTML = html;
      }

      function readPotonganGrid() {
        const body = document.getElementById("potonganGrupBody");
        if (!body) return; // UI belum dibuka — pertahankan state yang ada
        // Saat daftar grup belum termuat, <select> tak punya opsi sehingga value-nya
        // kosong. Pakai data-group-id sebagai cadangan agar potongan yang sudah
        // tersimpan TIDAK ikut terhapus ketika data barang disimpan ulang.
        const optionsReady = allGroups.length > 0;
        const next = [];
        body.querySelectorAll("tr").forEach((tr) => {
          const sel = tr.querySelector("select.group-select");
          if (!sel) return;
          const gid = optionsReady
            ? sel.value
            : sel.value || sel.dataset.groupId || "";
          if (!gid) return; // baris kosong / belum pilih grup
          const inp = tr.querySelectorAll("input.item-percent-input");
          next.push({
            group_id: parseInt(gid),
            disc1: parseInputPersen(inp[0]?.value),
            disc2: parseInputPersen(inp[1]?.value),
            disc3: parseInputPersen(inp[2]?.value),
            disc4: parseInputPersen(inp[3]?.value),
          });
        });
        groupDiscounts = next;
      }

      function initPotonganHargaJualUI() {
        renderPotonganGrid();
      }
      window.initPotonganHargaJualUI = initPotonganHargaJualUI;

      function handlePotonganRowChange(el) {
        const row = el.closest("tr");
        // Begitu grup dipilih di baris kosong: jadikan baris permanen + tambah baris kosong baru
        if (row && row.classList.contains("empty-row")) {
          const select = row.querySelector("select");
          if (select && select.value) {
            row.classList.remove("empty-row");
            const iconTd = row.querySelector("td");
            if (iconTd) {
              iconTd.innerHTML = "▶";
              iconTd.style.color = "var(--text-main)";
              iconTd.style.fontSize = "10px";
            }
            const tbody = document.getElementById("potonganGrupBody");
            if (tbody)
              tbody.insertAdjacentHTML(
                "beforeend",
                potonganRowHtml(null, true),
              );
          }
        }
        // Sinkronkan DOM -> state (tanpa render ulang agar fokus input tidak hilang)
        readPotonganGrid();
        notifySharedFieldChange();
      }
      window.handlePotonganRowChange = handlePotonganRowChange;

      // Versi ter-debounce untuk kotak cari (dipakai inline: oninput="debouncedLoadItems()").
      window.debouncedLoadItems = debounce(loadItems, 300);

      // Bootstrap otomatis HANYA di halaman daftar barang (punya #tblBarang).
      // Halaman lain yang memakai form ini (mis. assembly.html) cukup memuat skrip ini
      // untuk fungsinya, lalu memanggil loader yang diperlukannya sendiri.
      if (document.getElementById("tblBarang")) {
        // Kritis untuk tampilan awal: filter kategori + daftar barang.
        refreshSelects();
        loadItems();
        subscribeItemMasterChanges(() => loadItems());

        // Non-kritis (komponen modal/tab + data supplier & grup): tunda sampai browser idle
        // agar tidak berebut bandwidth dengan daftar barang saat pertama buka (terasa di Tailscale).
        const _idle = window.requestIdleCallback || ((cb) => setTimeout(cb, 200));
        _idle(() => {
          loadComponents();
          loadCustomerGroups();
          loadSuppliers();
          loadPkpStatusItem();
          // Hangatkan template tabel harga lanjutan dari SEKARANG (bukan pas diklik),
          // jadi saat user buka tab harga lanjutan sudah tersedia di memori (0 fetch).
          prefetchAdvancedTemplates();
        });
      }
