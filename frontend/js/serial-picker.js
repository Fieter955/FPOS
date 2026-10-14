(function () {
  "use strict";

  const normalize = (value) => String(value || "").trim().normalize("NFKC").toUpperCase();
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const whole = (value) => Math.abs(Number(value) - Math.round(Number(value))) < 1e-9;

  let overlay;
  let state = null;
  let searchTimer = null;
  let returnFocus = null;

  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement("div");
    overlay.className = "serial-picker-overlay";
    overlay.innerHTML = `
      <div class="serial-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="serialPickerTitle">
        <div class="serial-picker-head">
          <div class="serial-picker-headline">
            <div><h2 id="serialPickerTitle">Pilih Serial</h2><div id="serialPickerMeta" class="serial-picker-meta"></div></div>
            <button type="button" class="btn" data-serial-close aria-label="Tutup">✕</button>
          </div>
        </div>
        <div id="serialPickerBody" class="serial-picker-body"></div>
        <div class="serial-picker-actions"><button type="button" class="btn" data-serial-close>Batal</button><button type="button" class="btn btn-primary" id="serialPickerSave">Gunakan Serial</button></div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay || event.target.closest("[data-serial-close]")) close();
    });
    overlay.addEventListener("keydown", (event) => {
      if (event.key !== "Tab") return;
      const controls = [...overlay.querySelectorAll(
        "button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex='-1'])",
      )].filter((element) => element.offsetParent !== null);
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    overlay.querySelector("#serialPickerSave").addEventListener("click", save);
    return overlay;
  }

  function effectiveItem(item, catalog) {
    if (!item) return null;
    if (!item.is_virtual_variant || !item.parent_item_id) return item;
    return (catalog || []).find((row) => Number(row.id) === Number(item.parent_item_id)) || item;
  }

  function factor(item) {
    return item?.is_virtual_variant ? Number(item.conversion_factor_to_parent || 0) : 1;
  }

  function requiredQuantity(item, quantity, catalog) {
    const source = effectiveItem(item, catalog);
    const multiplier = source === item ? factor(item) || 1 : factor(item) || 1;
    return Number(quantity || 0) * multiplier;
  }

  function isTracked(item, catalog) {
    return Boolean(effectiveItem(item, catalog)?.tracks_serial);
  }

  function unique(values) {
    const seen = new Set();
    const output = [];
    for (const raw of values || []) {
      const display = String(raw || "").trim();
      const key = normalize(display);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      output.push(display);
    }
    return output;
  }

  function buttonText(serials, required) {
    const count = unique(serials).length;
    return `Serial ${count}/${Math.max(0, Math.round(Number(required) || 0))}`;
  }

  async function fetchContext(search = "", skip = 0) {
    const query = new URLSearchParams({
      purpose: state.purpose,
      item_id: String(state.itemId),
      skip: String(skip),
      limit: "100",
    });
    if (state.warehouseId) query.set("warehouse_id", String(state.warehouseId));
    if (state.sourceLineId) query.set("source_line_id", String(state.sourceLineId));
    if (state.quarantineId) query.set("quarantine_id", String(state.quarantineId));
    if (search) query.set("search", search);
    return api("GET", `/serials/options?${query}`);
  }

  function renderSelected() {
    const target = overlay.querySelector("#serialPickerSelected");
    if (!target) return;
    target.innerHTML = state.selected.length
      ? state.selected.map((serial, index) => `<span class="serial-picker-chip">${esc(serial)}<button type="button" data-remove-serial="${index}" aria-label="Hapus ${esc(serial)}">✕</button></span>`).join("")
      : '<span class="serial-picker-meta">Belum ada serial dipilih.</span>';
    target.querySelectorAll("[data-remove-serial]").forEach((button) => button.addEventListener("click", () => {
      state.selected.splice(Number(button.dataset.removeSerial), 1);
      renderSelected();
      renderCounter();
      renderOptions();
    }));
  }

  function renderCounter() {
    const target = overlay.querySelector("#serialPickerCounter");
    if (!target) return;
    const valid = state.selected.length === state.required;
    target.textContent = `${state.selected.length} dari ${state.required} serial`;
    target.classList.toggle("invalid", !valid);
  }

  function renderOptions() {
    const target = overlay.querySelector("#serialPickerList");
    if (!target) return;
    target.innerHTML = state.options.length
      ? state.options.map((row) => {
          const checked = state.selected.some((value) => normalize(value) === normalize(row.serial));
          return `<label class="serial-picker-option"><input type="checkbox" data-option-serial="${esc(row.serial)}" ${checked ? "checked" : ""}><span><b>${esc(row.serial)}</b><br><small>${esc(row.status)}</small></span></label>`;
        }).join("")
      : '<div class="serial-picker-empty">Tidak ada serial yang cocok.</div>';
    target.querySelectorAll("[data-option-serial]").forEach((input) => input.addEventListener("change", () => {
      const serial = input.dataset.optionSerial;
      if (input.checked) {
        if (state.selected.length >= state.required) {
          input.checked = false;
          return notify(`Jumlah serial sudah ${state.required}`);
        }
        state.selected = unique([...state.selected, serial]);
      } else {
        state.selected = state.selected.filter((value) => normalize(value) !== normalize(serial));
      }
      renderSelected();
      renderCounter();
    }));
    const optionInputs = [...target.querySelectorAll("[data-option-serial]")];
    optionInputs.forEach((input, index) => input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        input.click();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        optionInputs[(index + offset + optionInputs.length) % optionInputs.length]?.focus();
      }
    }));
  }

  function notify(message, type = "error") {
    if (typeof showToast === "function") showToast(message, type);
  }

  async function addScanned(value) {
    const raw = String(value || "").trim();
    if (!raw) return;
    if (state.selected.some((serial) => normalize(serial) === normalize(raw))) return notify("Serial sudah dipilih");
    if (state.selected.length >= state.required) return notify(`Jumlah serial sudah ${state.required}`);
    try {
      const result = await fetchContext(raw, 0);
      const exact = (result.items || []).find((row) => normalize(row.serial) === normalize(raw));
      if (!exact) return notify("Serial tidak tersedia untuk transaksi ini");
      state.selected.push(exact.serial);
      renderSelected(); renderCounter(); renderOptions();
    } catch (error) { notify(error.message); }
  }

  async function render() {
    overlay.querySelector("#serialPickerTitle").textContent = state.title || (state.mode === "new" ? "Masukkan Serial Baru" : "Pilih Serial");
    overlay.querySelector("#serialPickerMeta").textContent = `${state.itemName} • kebutuhan ${state.required}`;
    const body = overlay.querySelector("#serialPickerBody");
    if (state.mode === "new") {
      body.innerHTML = `<div id="serialPickerCounter" class="serial-picker-counter"></div><p class="serial-picker-meta">Scan atau masukkan satu nomor serial per baris.</p><textarea id="serialPickerNew" class="input-control serial-picker-new" autocomplete="off" spellcheck="false" placeholder="SN-0001\nSN-0002"></textarea>`;
      const input = body.querySelector("#serialPickerNew");
      input.value = state.selected.join("\n");
      input.addEventListener("input", () => { state.selected = unique(input.value.split(/\r?\n/)); renderCounter(); });
      renderCounter();
      setTimeout(() => input.focus(), 0);
      return;
    }
    body.innerHTML = `<div id="serialPickerCounter" class="serial-picker-counter"></div><div id="serialPickerSelected" class="serial-picker-selected"></div><div class="serial-picker-search"><input id="serialPickerSearch" class="input-control" autocomplete="off" placeholder="Scan atau cari serial"><button type="button" class="btn" id="serialPickerAdd">Tambah</button></div><div id="serialPickerList" class="serial-picker-list"><div class="serial-picker-empty">Memuat serial…</div></div>`;
    renderSelected(); renderCounter();
    const search = body.querySelector("#serialPickerSearch");
    const load = async () => {
      try { const result = await fetchContext(search.value.trim(), 0); state.options = result.items || []; renderOptions(); }
      catch (error) { body.querySelector("#serialPickerList").innerHTML = `<div class="serial-picker-empty">${esc(error.message)}</div>`; }
    };
    search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(load, 220); });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addScanned(search.value).then(() => { search.value = ""; search.focus(); load(); });
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        body.querySelector("[data-option-serial]")?.focus();
      }
    });
    body.querySelector("#serialPickerAdd").addEventListener("click", () => addScanned(search.value).then(() => { search.value = ""; search.focus(); load(); }));
    await load();
    setTimeout(() => search.focus(), 0);
  }

  async function open(options) {
    ensureOverlay();
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    state = {
      purpose: options.purpose,
      itemId: Number(options.itemId || options.item?.id),
      itemName: options.itemName || options.item?.name || "Barang",
      warehouseId: Number(options.warehouseId || 0) || null,
      sourceLineId: Number(options.sourceLineId || 0) || null,
      quarantineId: Number(options.quarantineId || 0) || null,
      mode: options.mode || "select",
      selected: unique(options.initialSerials || []),
      options: [],
      title: options.title,
      onSave: options.onSave,
      requestedQty: Number(options.quantity || 0),
    };
    try {
      const context = await fetchContext("", 0);
      if (!context.tracking_required) return notify("Barang ini tidak memakai pelacakan serial", "warning");
      const required = state.requestedQty * Number(context.quantity_factor || 1);
      if (!whole(required) || required <= 0) return notify("Jumlah barang berserial harus berupa unit dasar utuh");
      state.required = Math.round(required);
      state.itemName = context.serial_item_name || state.itemName;
      state.options = context.items || [];
      overlay.classList.add("open");
      await render();
    } catch (error) { notify(error.message); }
  }

  function save() {
    state.selected = unique(state.selected);
    if (state.selected.length !== state.required) return notify(`Serial harus tepat ${state.required}`);
    const selected = [...state.selected];
    const callback = state.onSave;
    close();
    if (typeof callback === "function") callback(selected);
  }

  function close() {
    clearTimeout(searchTimer);
    overlay?.classList.remove("open");
    state = null;
    const target = returnFocus;
    returnFocus = null;
    if (target?.isConnected && !target.matches(":disabled")) setTimeout(() => target.focus({preventScroll:true}), 0);
  }

  function validate(item, quantity, serials, catalog) {
    if (!isTracked(item, catalog)) return null;
    const needed = requiredQuantity(item, quantity, catalog);
    if (!whole(needed) || needed <= 0) return `${item.name} harus memakai jumlah unit dasar utuh`;
    if (unique(serials).length !== Math.round(needed)) return `${item.name}: pilih tepat ${Math.round(needed)} serial`;
    return null;
  }

  function validateUnique(groups) {
    const seen = new Set();
    for (const group of groups || []) for (const serial of group || []) {
      const key = normalize(serial);
      if (seen.has(key)) return `Serial ${serial} dipilih lebih dari sekali`;
      seen.add(key);
    }
    return null;
  }

  window.SerialPicker = { open, close, normalize, unique, isTracked, effectiveItem, requiredQuantity, buttonText, validate, validateUnique };
})();
