import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function bacaSkripBarcode() {
  const helper = readFileSync(
    new URL("../frontend/js/barcode-search.js", import.meta.url),
    "utf8",
  );
  const html = readFileSync(
    new URL("../frontend/barcode.html", import.meta.url),
    "utf8",
  );
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((script) => script.trim());
  assert.ok(scripts.length, "Skrip inline halaman barcode tidak ditemukan");
  return `${helper}
    ${scripts.at(-1)}
    globalThis.__barcodeTest = {
      setItems(items) { allItems = JSON.parse(JSON.stringify(items)); },
      getDaftar() { return JSON.parse(JSON.stringify(daftarCetak)); },
      getPayload: getStickerPayload,
      tambahKeDaftar,
      pilihItemById,
      renderSearch: renderItemSearchResults,
      getRequestedAddQty,
      ubahQty,
      selesaikanUbahQty,
      ubahQtyDenganDelta,
      hapusItem,
      hapusSemuaItem,
      ubahHargaGlobal,
      ubahTampilanHarga,
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
      ctrlKey: !!props.ctrlKey,
      currentTarget: this,
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
    if (selector === "[data-item-row-id]") {
      return this.dataset.itemRowId ? this : null;
    }
    return null;
  }

  querySelectorAll(selector) {
    return selector === "[data-item-row-id]" ? this.children : [];
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

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  scrollIntoView() {}
  focus() {
    this.document.activeElement = this;
  }
  select() {
    this.selectionActive = true;
  }
}

function buatSimulasi({ generateError = false, printAccepted = true } = {}) {
  const elemen = new Map();
  const document = {
    activeElement: null,
    listeners: new Map(),
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
    addEventListener(type, callback) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(callback);
    },
    dispatch(type, props = {}) {
      const event = {
        type,
        key: props.key,
        target: props.target || document.activeElement,
        ctrlKey: !!props.ctrlKey,
        defaultPrevented: false,
        preventDefault() {
          this.defaultPrevented = true;
        },
        stopImmediatePropagation() {
          this.immediatePropagationStopped = true;
        },
      };
      for (const callback of this.listeners.get(type) || []) callback(event);
      return event;
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
    itemAddQty: "1",
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
    HTMLInputElement: ElemenPalsu,
    HTMLTextAreaElement: ElemenPalsu,
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
        return {
          status: printAccepted ? "queued" : "rejected",
          job_id: printAccepted ? 91 : null,
        };
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
    supplier_barcodes: ["SUP-BC-001"],
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

test("pencarian panjang dibatasi agar daftar pilihan tetap mudah dinavigasi", async () => {
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
  assert.equal(hasil.children.length, 20);
  assert.match(hasil.innerHTML, /Avian Warna 1/);
  assert.match(hasil.innerHTML, /Menampilkan 20 dari 49 hasil/);
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

test("scanner keyboard memproses barcode supplier secara langsung", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  const input = simulasi.document.getElementById("itemSearch");
  input.focus();

  for (const key of "SUP-BC-001") {
    simulasi.document.dispatch("keydown", { key, target: input });
  }
  simulasi.document.dispatch("keydown", { key: "Enter", target: input });
  await tuntaskanPromise();

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 1);
  assert.equal(daftar[0].id, 1);
});

test("Enter pada pencarian nama langsung menambahkan hasil yang disorot", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  const input = simulasi.document.getElementById("itemSearch");

  input.value = "semen";
  input.dispatch("input");
  input.dispatch("keydown", { key: "Enter" });
  await tuntaskanPromise();
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar().length, 1);
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar()[0].name, "Semen Abu");

  input.value = "PSR-001";
  input.dispatch("keydown", { key: "Enter" });
  await tuntaskanPromise();

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 2);
  assert.equal(daftar[1].id, 3);
  assert.equal(daftar[1].barcode, "GEN-3");
  assert.ok(
    simulasi.panggilanApi.some(
      ({ path, body }) => path === "/barcode/generate" && body.item_id === 3,
    ),
  );
});

test("panah memilih barang, panah kanan menuju Qty, dan Enter menambahkan", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  const input = simulasi.document.getElementById("itemSearch");
  const qty = simulasi.document.getElementById("itemAddQty");

  input.value = "semen";
  input.dispatch("input");
  input.dispatch("keydown", { key: "ArrowDown" });
  input.dispatch("keydown", { key: "ArrowRight" });
  assert.equal(simulasi.document.activeElement, qty);

  qty.value = "3";
  qty.dispatch("keydown", { key: "Enter" });
  await tuntaskanPromise();

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 1);
  assert.equal(daftar[0].name, "Semen Putih");
  assert.equal(daftar[0].qty, 3);
  assert.equal(qty.value, "1");
  assert.equal(simulasi.document.activeElement, input);

  simulasi.document.activeElement = qty;
  simulasi.document.dispatch("keydown", { key: "F2" });
  assert.equal(simulasi.document.activeElement, input);
  assert.equal(input.selectionActive, true);
});

test("klik satu baris hasil langsung menambahkan barang", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  const input = simulasi.document.getElementById("itemSearch");
  const hasil = simulasi.document.getElementById("itemSearchResults");

  input.value = "pasir";
  input.dispatch("input");
  hasil.children[0].click();
  await tuntaskanPromise();

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 1);
  assert.equal(daftar[0].name, "Pasir Halus");
});

test("barang langsung masuk daftar dan cetak ditahan jika pembuatan barcode gagal", async () => {
  const gagalGenerate = buatSimulasi({ generateError: true });
  gagalGenerate.sandbox.__barcodeTest.setItems(barang);
  const input = gagalGenerate.document.getElementById("itemSearch");
  const qty = gagalGenerate.document.getElementById("itemAddQty");
  input.value = "pasir";
  qty.value = "4";
  input.dispatch("input");
  input.dispatch("keydown", { key: "Enter" });
  await tuntaskanPromise();

  const daftar = gagalGenerate.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 1);
  assert.equal(daftar[0].name, "Pasir Halus");
  assert.equal(daftar[0].qty, 4);
  assert.equal(daftar[0].barcode, "");
  assert.equal(daftar[0].barcode_status, "error");
  assert.ok(
    gagalGenerate.toast.some(
      ({ message, type }) =>
        type === "error" && message.includes("Barcode duplikat"),
    ),
  );

  await gagalGenerate.sandbox.__barcodeTest.printStiker();
  assert.equal(gagalGenerate.panggilanFetch.length, 0);
  assert.ok(
    gagalGenerate.toast.some(
      ({ message, type }) =>
        type === "warning" && message.includes("belum siap"),
    ),
  );
});

test("Qty global bersifat tambahan untuk barang yang sudah ada", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1, 3);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1, 5);

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  assert.equal(daftar.length, 1);
  assert.equal(daftar[0].qty, 8);

  await simulasi.sandbox.__barcodeTest.pilihItemById(1, 9999);
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar()[0].qty, 9999);
});

test("Qty pada daftar menjadi jumlah akhir label di payload", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1, 8);

  assert.equal(simulasi.sandbox.__barcodeTest.ubahQty(1, "4"), true);

  const daftar = simulasi.sandbox.__barcodeTest.getDaftar();
  const payload = simulasi.sandbox.__barcodeTest.getPayload();
  assert.equal(daftar[0].qty, 4);
  assert.equal(payload.data_produk.length, 4);
  assert.ok(payload.data_produk.every((item) => item.barcode === "BC-001"));
});

test("Qty kosong atau desimal mempertahankan nilai sah terakhir dan batas diterapkan", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1, 7);

  assert.equal(simulasi.sandbox.__barcodeTest.ubahQty(1, ""), false);
  assert.equal(simulasi.sandbox.__barcodeTest.ubahQty(1, "2.5"), false);
  assert.equal(simulasi.sandbox.__barcodeTest.ubahQty(1, "abc"), false);
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar()[0].qty, 7);

  const input = { value: "" };
  simulasi.sandbox.__barcodeTest.selesaikanUbahQty(1, input);
  assert.equal(input.value, "7");

  simulasi.sandbox.__barcodeTest.ubahQty(1, "0");
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar()[0].qty, 1);
  simulasi.sandbox.__barcodeTest.ubahQty(1, "10000");
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar()[0].qty, 9999);
});

test("Qty lima pada layout tiga Lin menghasilkan lima label dan dua baris", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1, 5);
  simulasi.document.querySelector('input[name="colLayout"]:checked').value = "3";

  await simulasi.sandbox.__barcodeTest.printStiker();

  const renderPayload = simulasi.panggilanFetch[0].body;
  const printCall = simulasi.panggilanApi.find(({ path }) => path === "/print/");
  const printContent = JSON.parse(printCall.body.content);
  assert.equal(renderPayload.data_produk.length, 5);
  assert.equal(printContent.total_labels, 5);
  assert.equal(printContent.row_count, 2);
  assert.equal(printContent.col_count, 3);
});

test("daftar terpilih mendukung tombol kuantitas, batal per barang, dan batalkan semua", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1, 2);
  await simulasi.sandbox.__barcodeTest.pilihItemById(2, 3);

  simulasi.sandbox.__barcodeTest.ubahQtyDenganDelta(1, 1);
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar()[0].qty, 3);
  assert.match(
    simulasi.document.getElementById("daftarCetakBody").innerHTML,
    /Batal/,
  );

  simulasi.sandbox.__barcodeTest.hapusItem(1);
  assert.equal(
    simulasi.sandbox.__barcodeTest.getDaftar().map((item) => item.id).join(","),
    "2",
  );

  simulasi.sandbox.__barcodeTest.hapusSemuaItem();
  assert.equal(simulasi.sandbox.__barcodeTest.getDaftar().length, 0);
  assert.equal(
    simulasi.document.getElementById("btnHapusSemuaBarcode").disabled,
    true,
  );
});

test("harga tidak dicetak sebelum checkbox harga diaktifkan", async () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.__barcodeTest.setItems(barang);
  await simulasi.sandbox.__barcodeTest.pilihItemById(1);

  let payload = simulasi.sandbox.__barcodeTest.getPayload();
  assert.equal(payload.data_produk[0].tampilkan_harga, false);

  simulasi.sandbox.__barcodeTest.ubahTampilanHarga(1, true);
  payload = simulasi.sandbox.__barcodeTest.getPayload();
  assert.equal(payload.data_produk[0].tampilkan_harga, true);

  simulasi.sandbox.__barcodeTest.ubahTampilanHarga(1, false);
  payload = simulasi.sandbox.__barcodeTest.getPayload();
  assert.equal(payload.data_produk[0].tampilkan_harga, false);
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
