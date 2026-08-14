import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const sumber = readFileSync(
  new URL("../frontend/js/purchase-draft.js", import.meta.url),
  "utf8",
);

class PenyimpananMemori {
  constructor() {
    this.data = new Map();
  }

  getItem(key) {
    return this.data.has(key) ? this.data.get(key) : null;
  }

  setItem(key, value) {
    this.data.set(key, String(value));
  }

  removeItem(key) {
    this.data.delete(key);
  }
}

function muatHelper() {
  const kotakPasir = { console };
  kotakPasir.globalThis = kotakPasir;
  vm.runInNewContext(sumber, kotakPasir);
  return kotakPasir.PurchaseDraftStore;
}

function salinKeRealmUtama(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

test("draft tersimpan dan dapat dibaca kembali dalam 24 jam", () => {
  const helper = muatHelper();
  const storage = new PenyimpananMemori();
  let waktu = 1_000_000;
  const store = helper.create({
    storage,
    userId: 7,
    branchId: 2,
    mode: "purchase",
    now: () => waktu,
  });
  const draft = { supplier_id: 9, rows: [{ item_id: 12, qty: 3 }] };

  assert.equal(store.save(draft), true);
  assert.deepEqual(salinKeRealmUtama(store.read()), draft);

  waktu += helper.DEFAULT_TTL_MS - 1;
  assert.deepEqual(salinKeRealmUtama(store.read()), draft);
});

test("draft dipisahkan berdasarkan pengguna, cabang, dan mode", () => {
  const helper = muatHelper();
  const storage = new PenyimpananMemori();
  const opsi = { storage, now: () => 10_000 };
  const pembelian = helper.create({
    ...opsi,
    userId: 1,
    branchId: 1,
    mode: "purchase",
  });
  const pesanan = helper.create({
    ...opsi,
    userId: 1,
    branchId: 1,
    mode: "po",
  });
  const cabangLain = helper.create({
    ...opsi,
    userId: 1,
    branchId: 2,
    mode: "purchase",
  });
  const penggunaLain = helper.create({
    ...opsi,
    userId: 2,
    branchId: 1,
    mode: "purchase",
  });

  pembelian.save({ notes: "beli" });
  pesanan.save({ notes: "pesan" });

  assert.notEqual(pembelian.key, pesanan.key);
  assert.deepEqual(salinKeRealmUtama(pembelian.read()), { notes: "beli" });
  assert.deepEqual(salinKeRealmUtama(pesanan.read()), { notes: "pesan" });
  assert.equal(cabangLain.read(), null);
  assert.equal(penggunaLain.read(), null);
});

test("draft tepat 24 jam dianggap kedaluwarsa dan dihapus", () => {
  const helper = muatHelper();
  const storage = new PenyimpananMemori();
  let waktu = 50_000;
  const store = helper.create({
    storage,
    userId: "admin",
    branchId: 1,
    mode: "purchase",
    now: () => waktu,
  });

  store.save({ notes: "lama" });
  waktu += helper.DEFAULT_TTL_MS;

  assert.equal(store.read(), null);
  assert.equal(storage.getItem(store.key), null);
});

test("draft rusak atau tidak sesuai scope dibuang dengan aman", () => {
  const helper = muatHelper();
  const storage = new PenyimpananMemori();
  const store = helper.create({
    storage,
    userId: 4,
    branchId: 3,
    mode: "po",
    now: () => 100,
  });

  storage.setItem(store.key, "{bukan-json");
  assert.equal(store.read(), null);
  assert.equal(storage.getItem(store.key), null);

  storage.setItem(
    store.key,
    JSON.stringify({
      version: helper.VERSION,
      savedAt: 100,
      userId: "pengguna-lain",
      branchId: "3",
      mode: "po",
      data: { notes: "salah scope" },
    }),
  );
  assert.equal(store.read(), null);
  assert.equal(storage.getItem(store.key), null);
});

test("halaman pembelian memuat helper dan mengaitkan semua jalur keluar", () => {
  const html = readFileSync(
    new URL("../frontend/purchase/purchases.html", import.meta.url),
    "utf8",
  );

  assert.match(html, /\/js\/purchase-draft\.js/);
  assert.match(html, /onclick="kembaliKeDashboard\(\)"/);
  assert.match(html, /onclick="batalkanForm\(\)"/);
  assert.match(html, /window\.addEventListener\("pagehide", savePurchaseDraftNow\)/);
  assert.match(html, /getPurchaseDraftStore\(currentFormMode\)\?\.remove\(\)/);
});
