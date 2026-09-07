import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const components = readFileSync(
  new URL("../frontend/js/components.js", import.meta.url),
  "utf8",
);
const purchases = readFileSync(
  new URL("../frontend/purchase/purchases.html", import.meta.url),
  "utf8",
);
const reports = readFileSync(
  new URL("../frontend/reports.html", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../frontend/css/style.css", import.meta.url),
  "utf8",
);

test("halaman pembelian mengaktifkan layout lebar secara opt-in", () => {
  assert.match(components, /layout = "default"/);
  assert.match(components, /layout === "wide"/);
  assert.match(purchases, /createPaymentModal\(\{\s*type: "ap",\s*layout: "wide",/);
  assert.doesNotMatch(reports, /layout:\s*["']wide["']/);
});

test("modal lebar membagi form pembayaran menjadi dua kolom desktop", () => {
  for (const className of [
    "payment-modal-layout",
    "payment-modal-column-left",
    "payment-modal-column-right",
    "p-method-group",
    "p-balances",
    "p-amounts",
    "p-total",
    "p-note-group",
    "p-actions",
  ]) {
    assert.match(components, new RegExp(className));
  }

  assert.match(styles, /@media \(min-width: 769px\)/);
  assert.match(styles, /\.payment-modal-box\.payment-modal-box-wide/);
  assert.match(
    styles,
    /\.payment-modal-box-wide \.payment-modal-layout \{\s*grid-template-columns: minmax\(0, 0\.9fr\) minmax\(0, 1\.1fr\);/,
  );
  assert.match(styles, /width: min\(94vw, 920px\) !important/);
  assert.doesNotMatch(styles, /grid-template-areas:/);
});

test("modal pembayaran kembali satu kolom pada layar kecil", () => {
  assert.match(styles, /@media \(max-width: 768px\)/);
  assert.match(
    styles,
    /\.payment-modal-box-wide \.p-balances \{\s*grid-template-columns: 1fr !important;/,
  );
});

test("modal pembayaran mendukung panah, Tab native, dan Enter untuk berpindah", () => {
  for (const key of ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]) {
    assert.match(components, new RegExp(`key === "${key}"`));
  }
  assert.match(components, /event\.key === "Enter"/);
  assert.match(components, /focusDirectionalPaymentControl/);
  assert.match(components, /focusNextPaymentControl/);
  assert.doesNotMatch(components, /event\.key === "Tab"/);
  assert.match(components, /requestAnimationFrame\(\(\) => focusPaymentControl\(methodSel\)\)/);
});
