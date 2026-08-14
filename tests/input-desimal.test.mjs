import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const sumberApi = readFileSync(new URL("../frontend/js/api.js", import.meta.url), "utf8");
const awalHelper = sumberApi.indexOf("const PEMILIH_INPUT_DESIMAL");
const akhirHelper = sumberApi.indexOf("\nfunction fmtDate", awalHelper);

assert.notEqual(awalHelper, -1, "Helper desimal global tidak ditemukan");
assert.notEqual(akhirHelper, -1, "Batas akhir helper desimal tidak ditemukan");

const pendengar = new Map();
const kotakPasir = {
  console,
  setTimeout,
  clearTimeout,
  Node: { ELEMENT_NODE: 1 },
  HTMLInputElement: class {},
  MutationObserver: class {
    observe() {}
  },
  document: {
    activeElement: null,
    addEventListener(nama, callback) {
      if (!pendengar.has(nama)) pendengar.set(nama, []);
      pendengar.get(nama).push(callback);
    },
  },
};
kotakPasir.globalThis = kotakPasir;

vm.runInNewContext(
  `${sumberApi.slice(awalHelper, akhirHelper)}\n` +
    "globalThis.helperDesimal = { parseDesimal, toDesimal, formatDesimal };",
  kotakPasir,
);

const { parseDesimal, toDesimal, formatDesimal } = kotakPasir.helperDesimal;

class InputPalsu extends kotakPasir.HTMLInputElement {
  constructor(value, posisiKursor = String(value).length, dataset = {}) {
    super();
    this.value = String(value);
    this.selectionStart = posisiKursor;
    this.selectionEnd = posisiKursor;
    this.dataset = dataset;
    this.type = "text";
  }

  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }

  hasAttribute() {
    return false;
  }

  getAttribute() {
    return null;
  }

  setCustomValidity() {}

  closest() {
    return this;
  }
}

test("parser menerima koma dan titik sebagai desimal", () => {
  assert.equal(parseDesimal("0,5"), 0.5);
  assert.equal(parseDesimal("0.5"), 0.5);
  assert.equal(parseDesimal("1.234,56"), 1234.56);
  assert.equal(parseDesimal("1,234.56"), 1234.56);
  assert.equal(parseDesimal("1.000"), 1000);
});

test("formatter selalu menampilkan minimal dua desimal", () => {
  assert.equal(toDesimal(0), "0,00");
  assert.equal(toDesimal(1), "1,00");
  assert.equal(toDesimal(1234.5), "1.234,50");
  assert.equal(
    toDesimal(1.2345, { maximumFractionDigits: 4 }),
    "1,2345",
  );
});

test("mengetik bilangan pada 0,00 mempertahankan format dan kursor", () => {
  const input = new InputPalsu("01,00", 2);
  kotakPasir.document.activeElement = input;

  formatDesimal(input);

  assert.equal(input.value, "1,00");
  assert.equal(input.selectionStart, 1);
});

test("digit setelah koma menimpa nol tanpa mereset menjadi satu digit", () => {
  const input = new InputPalsu("0,500", 3);
  kotakPasir.document.activeElement = input;

  formatDesimal(input);

  assert.equal(input.value, "0,50");
  assert.equal(input.selectionStart, 3);
});

test("menghapus seluruh isi kembali ke 0,00 dengan kursor sebelum koma", () => {
  const input = new InputPalsu("", 0);
  kotakPasir.document.activeElement = input;

  formatDesimal(input);

  assert.equal(input.value, "0,00");
  assert.equal(input.selectionStart, 1);
});

test("tombol koma, titik, dan numpad memindahkan kursor ke belakang koma", () => {
  for (const [key, code] of [
    [",", "Comma"],
    [".", "Period"],
    ["Decimal", "NumpadDecimal"],
  ]) {
    const input = new InputPalsu("0,00", 1, { inputDesimal: "" });
    kotakPasir.document.activeElement = input;
    let dicegah = false;
    const event = {
      target: input,
      key,
      code,
      preventDefault() {
        dicegah = true;
      },
    };

    pendengar.get("keydown").forEach((callback) => callback(event));

    assert.equal(dicegah, true);
    assert.equal(input.selectionStart, 2);
    assert.equal(input.selectionEnd, 2);
  }
});
