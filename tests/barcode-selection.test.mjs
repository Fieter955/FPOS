import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function bacaSkripBarcode() {
  const html = readFileSync(
    new URL("../frontend/barcode.html", import.meta.url),
    "utf8",
  );
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((script) => script.trim());
  assert.ok(scripts.length, "Skrip inline halaman barcode tidak ditemukan");
  return `${scripts.at(-1)}
    globalThis.__barcodeTest = {
      setItems(items) { allItems = JSON.parse(JSON.stringify(items)); },
      getDaftar() { return JSON.parse(JSON.stringify(daftarCetak)); },
      getPayload: getStickerPayload,
      getDraft() { return Object.fromEntries(itemSearchDraftQty); },
      tambahKeDaftar,
      pilihItemById,
      setPickerQty,
      applyPickerQuantities,
      renderSearch: renderItemSearchResults,
      printStiker,
    };`;
}

class ElemenPalsu {
  constructor(id, document) {
    this.id = id;
    this.document = document;
    this.value = "";
    this.textContent = "";
    this.disabled = false;
    this.checked = false;
    this.dataset = {};
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.style = {
      display: "",
      setProperty() {},
    };
    this.classList = {
      toggle() {},
      add() {},
      remove() {},
    };
    this._innerHTML = "";
    this.qtyInputs = new Map();
  }

  set innerHTML(value) {
    this._innerHTML = String(value ?? "");
    if (this.id !== "itemSearchResults") return;
    this.children = [...this._innerHTML.matchAll(/data-item-row-id="([^"]+)"/g)].map(
      (match) => {
        const row = new ElemenPalsu("", this.document);
        row.dataset.itemRowId = match[1];
        row.parentElement = this;
        return row;
      },
    );
    this.qtyInputs = new Map();
    for (const match of this._innerHTML.matchAll(
      /class="[^"]*barcode-picker-qty[^"]*" data-item-id="([^"]+)"/g,
    )) {
      const input = new ElemenPalsu("", this.document);
      input.dataset.itemId = match[1];
      input.parentElement = this;
      this.qtyInputs.set(match[1], input);
    }
  }

  get innerHTML() {
    return this._innerHTML;
  }

  addEventListener(type, callback) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(callback);
  }

  dispatch(type, props = {}) {
    const event = {
      type,
      target: props.target || this,
      key: props.key,
      defaultPrevented: false,
      propagationStopped: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
      stopPropagation() {
        this.propagationStopped = true;
      },
    };
    for (const callback of this.listeners.get(type) || []) {
      callback.call(this, event);
    }
    return event;
  }

  click() {
    if (this.parentElement) {
      this.parentElement.dispatch("click", { target: this });
    } else {
      this.dispatch("click");
    }
  }

  closest(selector) {
    if (selector === ".barcode-picker-qty[data-item-id]") {
      return this.dataset.itemId ? this : null;
    }
    if (selector === "button[data-qty-action]") {
      return this.dataset.qtyAction ? this : null;
    }
    if (selector === '[data-action="apply-picker"]') {
      return this.dataset.action === "apply-picker" ? this : null;
    }
    if (selector === "[data-focus-qty]") {
      return this.dataset.focusQty ? this : null;
    }
    return null;
  }

  querySelectorAll(selector) {
    return selector === "[data-item-row-id]" ? this.children : [];
  }

  querySelector(selector) {
    const match = selector.match(/data-item-id="([^"]+)"/);
    return match ? this.qtyInputs.get(match[1]) || null : null;
  }

  contains(element) {
    let current = element;
    while (current) {
      if (current === this) return true;
      current = current.parentElement;
    }
    return false;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  scrollIntoView() {}
  focus() {
    this.document.activeElement = this;
  }
}

function buatSimulasi({ generateError = false, printAccepted = true } = {}) {
  const elemen = new Map();
  const document = {
    activeElement: null,
    documentElement: { style: { setProperty() {} } },
    getElementById(id) {
      if (!elemen.has(id)) elemen.set(id, new ElemenPalsu(id, document));
      return elemen.get(id);
    },
    querySelector(selector) {
      if (selector === 'input[name="colLayout"]:checked') return radio;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === 'input[name="colLayout"]') return [radio];
      if (selector === "#itemSearchResults [data-item-row-id]") {
        return document.getElementById("itemSearchResults").children;
      }
      return [];
    },
  };
  const radio = new ElemenPalsu("col2", document);
  radio.value = "2";
  radio.checked = true;

  const nilaiAwal = {
    paperW: "33",
    paperH: "15",
    gapX: "2",
    gapY: "0",
    slPrice: "14",
    slBarcode: "100",
    slName: "12",
  };
  for (const [id, value] of Object.entries(nilaiAwal)) {
    document.getElementById(id).value = value;
  }

  const panggilanApi = [];
  const panggilanFetch = [];
  const toast = [];
  const storage = new Map([
    ["active_branch_id", "2"],
    ["ipos_token", "token-aktif"],
    ["token", "token-lama-kedaluwarsa"],
  ]);

  class FileReaderPalsu {
    readAsDataURL() {
      this.result = "data:image/png;base64,aW1hZ2U=";
      this.onloadend?.();
    }
  }

  const sandbox = {
    console: { ...console, error() {} },
    document,
    Blob,
    AbortController,
    FileReader: FileReaderPalsu,
    URL: {
      createObjectURL: () => "blob:preview",
      revokeObjectURL() {},
    },
    localStorage: {
      getItem(key) {
        return storage.get(key) ?? null;
      },
      setItem(key, value) {
        storage.set(key, String(value));
      },
    },
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
    requireAuth() {},
    subscribeItemMasterChanges() {
      return () => {};
    },
    showToast(message, type) {
      toast.push({ message, type });
    },
    parseDesimal(value) {
      return Number(String(value?.value ?? value ?? "0").replace(",", "."));
    },
    async api(method, path, body) {
      panggilanApi.push({ method, path, body });
      if (method === "GET" && path.startsWith("/items/")) {
        return JSON.parse(JSON.stringify(barang));
      }
      if (path === "/barcode/generate") {
        if (generateError) throw new Error("Barcode duplikat");
        return { barcode_value: `GEN-${body.item_id}` };
      }
      if (path === "/print/") {
        return { success: printAccepted, job_id: printAccepted ? 91 : null };
      }
      return {};
    },
    async apiBlob(method, path, body, options) {
      panggilanFetch.push({ method, path, body, options });
      return {
        headers: {
          get(name) {
            if (name === "X-Sheet-Width-Mm") return "68";
            if (name === "X-Sheet-Height-Mm") return "15";
            return null;
          },
        },
        blob: new Blob(["image"], { type: "image/png" }),
      };
    },
    async fetch(path, options) {
      return {
        ok: true,
        headers: {
          get(name) {
            if (name === "X-Sheet-Width-Mm") return "68";
            if (name === "X-Sheet-Height-Mm") return "15";
            return null;
          },
        },
        async blob() {
          return new Blob(["image"], { type: "image/png" });
        },
      };
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  vm.runInNewContext(bacaSkripBarcode(), sandbox);
  return { sandbox, document, elemen, panggilanApi, panggilanFetch, toast };
}

async function tuntaskanPromise() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

const barang = [
  {
    id: 2,
    name: "Semen Putih",
    code: "X-BC-001-X",
    barcode: "BC-002",
    sell_price: 72_000,
    category: { name: "SEMEN" },
  },
  {
    id: 1,
    name: "Semen Abu",
    code: "SMN-001",
    barcode: "BC-001",
    sell_price: 68_000,
    category: { name: "SEMEN" },
  },
  {
    id: 3,
    name: "Pasir Halus",
    code: "PSR-001",
    barcode: null,
    sell_price: 25_000,
    category: { name: "PASIR" },
  },
];

test("pencarian Avian menampilkan semua hasil dan menerapkan qty massal", async () => {
  const simulasi = buatSimulasi();
  const avianItems = Array.from({ length: 49 }, (_, index) => ({
    id: 100 + index,
    name: `Avian Warna ${index + 1}`,
    code: `AV-${index + 1}`,
    barcode: `899000${index + 1}`,
    sell_price: 50_000 + index,
    category: { name: "CAT" },
  }));
  simulasi.sandbox.__barcodeTest.setItems(avianItems);
  const input = simulasi.document.getElementById("itemSearch");
  const hasil = simulasi.document.getElementById("itemSearchResults");

  input.value = "avian";
  input.dispatch("input");
  assert.equal(hasil.children.length, 49);
  assert.match(hasil.innerHTML, /Avian Warna 49/);

  simulasi.sandbox.__barcodeTest.setPickerQty(100, 3);
  simulasi.sandbox.__barcodeTest.setPickerQty(101, 2);
  await simulasi.sandbox.__barcodeTest.applyPickerQuantities();

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 2);
  assert.equal(daftar[0].qty, 3);
  assert.equal(daftar[1].qty, 2);
  assert.equal(input.value, "");
  assert.equal(simulasi.sandbox.__barcodeTest.getPayload().data_produk.length, 5);
});

test("Enter scanner memprioritaskan barcode exact dan pilihan ulang menambah qty", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  const input = simulasi.document.getElementById("itemSearch");

  for (let scan = 0; scan < 2; scan += 1) {
    input.value = "  bc-001  ";
    input.dispatch("input");
    input.dispatch("keydown", { key: "Enter" });
    await tuntaskanPromise();
  }

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 1);
  assert.equal(daftar[0].id, 1);
  assert.equal(daftar[0].qty, 2);
});

test("pencarian nama menunggu qty, sedangkan kode exact tetap masuk cepat", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  const input = simulasi.document.getElementById("itemSearch");

  input.value = "semen";
  input.dispatch("input");
  input.dispatch("keydown", { key: "Enter" });
  await tuntaskanPromise();
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar().length, 0);

  input.value = "PSR-001";
  input.dispatch("keydown", { key: "Enter" });
  await tuntaskanPromise();

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 1);
  assert.equal(daftar[0].id, 3);
  assert.equal(daftar[0].barcode, "GEN-3");
  assert.ok(
    simulasi.panggilanApi.some(
      ({ path, body }) => path === "/barcode/generate" && body.item_id === 3,
    ),
  );
});

test("qty nol diabaikan dan kegagalan generate tidak membuat label kosong", async () => {
  const gagalGenerate = buatSimulasi({ generateError: true });
  gagalGenerate.sandbox.__barcodeTest.setItems(barang);
  gagalGenerate.sandbox.__barcodeTest.setPickerQty(1, 0);
  gagalGenerate.sandbox.__barcodeTest.setPickerQty(3, 4);
  await gagalGenerate.sandbox.__barcodeTest.applyPickerQuantities();
  assert.equal(gagalGenerate.sandbox.__barcodeTest.getDaftar().length, 0);
  assert.ok(
    gagalGenerate.toast.some(
      ({ message, type }) =>
        type === "error" && message.includes("Barcode duplikat"),
    ),
  );
});

test("qty menu bersifat tambahan terhadap jumlah yang sudah ada", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1);
  simulasi.sandbox.__barcodeTest.setPickerQty(1, 5);
  await simulasi.sandbox.__barcodeTest.applyPickerQuantities();

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 1);
  assert.equal(daftar[0].qty, 8);
});

test("tombol plus minus mengubah draft dan penerapan ganda dicegah", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  simulasi.sandbox.__barcodeTest.renderSearch("pasir");
  const hasil = simulasi.document.getElementById("itemSearchResults");
  const plus = new ElemenPalsu("", simulasi.document);
  plus.dataset.qtyAction = "plus";
  plus.dataset.itemId = "3";
  plus.parentElement = hasil;
  const minus = new ElemenPalsu("", simulasi.document);
  minus.dataset.qtyAction = "minus";
  minus.dataset.itemId = "3";
  minus.parentElement = hasil;

  hasil.dispatch("click", { target: plus });
  hasil.dispatch("click", { target: plus });
  hasil.dispatch("click", { target: minus });
  assert.equal(simulasi.sandbox.__barcodeTest.getDraft()[3], 1);

  simulasi.sandbox.__barcodeTest.setPickerQty(3, 2);
  const firstApply = simulasi.sandbox.__barcodeTest.applyPickerQuantities();
  const secondApply = await simulasi.sandbox.__barcodeTest.applyPickerQuantities();
  await firstApply;

  assert.equal(secondApply, false);
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar()[0].qty, 2);
  assert.equal(
    simulasi.panggilanApi.filter(({ path }) => path === "/barcode/generate")
      .length,
    1,
  );
});

test("satu label mempertahankan lebar media 1, 2, dan 3 Lin", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1);
  const radio = simulasi.document.querySelector(
    'input[name="colLayout"]:checked',
  );

  for (const columns of [1, 2, 3]) {
    radio.value = String(columns);
    const payload = simulasi.sandbox.__barcodeTest.getPayload();
    assert.equal(payload.data_produk.length, 1);
    assert.equal(payload.jumlah_kolom, columns);
    assert.equal(payload.jumlah_kolom_sheet, columns);
  }
});

test("cetak mengirim barcode, qty, dan cabang aktif ke antrean printer", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1);

  await simulasi.sandbox.__barcodeTest.printStiker();

  assert.equal(simulasi.panggilanFetch.length, 1);
  assert.equal(simulasi.panggilanFetch[0].path, "/sticker/render-sheet");
  const renderPayload = simulasi.panggilanFetch[0].body;
  assert.equal(renderPayload.data_produk.length, 2);
  assert.ok(renderPayload.data_produk.every((item) => item.barcode === "BC-001"));

  const printCall = simulasi.panggilanApi.find(({ path }) => path === "/print/");
  assert.ok(printCall, "Job cetak tidak dikirim");
  assert.equal(printCall.body.content_type, "label_image");
  assert.equal(printCall.body.branch_id, 2);
  const content = JSON.parse(printCall.body.content);
  assert.equal(content.total_labels, 2);
  assert.equal(content.col_count, 2);
  assert.equal(content.image_base64, "aW1hZ2U=");
  assert.ok(
    simulasi.toast.some(
      ({ message, type }) =>
        type === "success" && message.includes("antrean printer"),
    ),
  );
});

test("respons antrean yang ditolak dilaporkan dan tombol cetak dipulihkan", async () => {
  const simulasi = buatSimulasi({ printAccepted: false });
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1);

  await simulasi.sandbox.__barcodeTest.printStiker();

  const tombol = simulasi.document.getElementById("btnCetakBarcode");
  assert.equal(tombol.disabled, false);
  assert.ok(
    simulasi.toast.some(
      ({ message, type }) =>
        type === "error" && message.includes("tidak menerima job barcode"),
    ),
  );
});
