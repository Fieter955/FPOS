import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function bacaSkripInline(path) {
  const html = readFileSync(new URL(path, import.meta.url), "utf8");
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((script) => script.trim());
  assert.ok(scripts.length, `Skrip inline tidak ditemukan pada ${path}`);
  return scripts.at(-1);
}

function buatElemen() {
  return {
    value: "",
    innerHTML: "",
    textContent: "",
    style: {},
    addEventListener() {},
    classList: { add() {}, remove() {}, contains() { return false; } },
  };
}

function buatKotakPasir() {
  const elemen = new Map();
  const pendengar = new Map();
  const panggilanApi = [];
  const document = {
    getElementById(id) {
      if (!elemen.has(id)) elemen.set(id, buatElemen());
      return elemen.get(id);
    },
    querySelector() {
      return buatElemen();
    },
    addEventListener(type, callback) {
      pendengar.set(type, callback);
    },
  };
  const kotakPasir = {
    console: { ...console, warn() {}, error() {} },
    document,
    location: { search: "", href: "", origin: "https://fpos.test" },
    URLSearchParams,
    setTimeout,
    clearTimeout,
    requireAuth() {},
    openModal() {},
    closeModal() {},
    showToast() {},
    showConfirm: async () => false,
    showLoading() {},
    hideLoading() {},
    fmtRp(value) {
      return String(value || 0);
    },
    parseDesimal() {
      return 0;
    },
    toDesimal(value) {
      return String(value || 0);
    },
    createPremiumCombo() {
      return { val: () => "" };
    },
    fetch: async () => {
      throw new Error("Modal tambahan sengaja tidak tersedia dalam simulasi");
    },
    async api(_method, path) {
      panggilanApi.push(path);
      return [];
    },
  };
  kotakPasir.window = kotakPasir;
  kotakPasir.globalThis = kotakPasir;
  return { kotakPasir, elemen, pendengar, panggilanApi };
}

async function tuntaskanPromise() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

test("pelanggan tetap dimuat ketika api.js lama belum memiliki guard", async () => {
  const simulasi = buatKotakPasir();
  vm.runInNewContext(
    bacaSkripInline("../frontend/customers.html"),
    simulasi.kotakPasir,
  );
  await tuntaskanPromise();

  assert.ok(
    simulasi.panggilanApi.includes("/customers/?limit=300"),
    "Request daftar pelanggan tidak dijalankan",
  );
  assert.match(
    simulasi.elemen.get("tblCust").innerHTML,
    /Belum ada pelanggan/,
  );
});

test("supplier dimuat sebelum modal dan data barang selesai", async () => {
  const simulasi = buatKotakPasir();
  vm.runInNewContext(
    bacaSkripInline("../frontend/supplier/dashboard.html"),
    simulasi.kotakPasir,
  );

  const saatSiap = simulasi.pendengar.get("DOMContentLoaded");
  assert.equal(typeof saatSiap, "function");
  await saatSiap();
  await tuntaskanPromise();

  assert.ok(
    simulasi.panggilanApi.includes("/suppliers/?limit=500"),
    "Request daftar supplier tidak dijalankan",
  );
  assert.match(
    simulasi.elemen.get("tblBody").innerHTML,
    /Belum ada supplier/,
  );
});

test("aset development memakai no-store dan build tetap immutable", () => {
  const sumberMain = readFileSync(
    new URL("../backend/main.py", import.meta.url),
    "utf8",
  );

  assert.match(sumberMain, /_ASSET_NO_STORE = not _USE_BUILT/);
  assert.match(sumberMain, /cc = "no-store"/);
  assert.match(sumberMain, /cc \+= ", immutable"/);
  assert.equal(
    sumberMain.match(/no_store=_ASSET_NO_STORE/g)?.length,
    2,
    "Mount JS dan CSS harus sama-sama memakai kebijakan cache sesuai mode",
  );
});

