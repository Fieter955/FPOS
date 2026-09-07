import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const salesPage = readFileSync(
  new URL("../frontend/sales.html", import.meta.url),
  "utf8",
);

test("riwayat penjualan tidak menampilkan status pembayaran", () => {
  assert.doesNotMatch(salesPage, /id="fStatus"/);
  assert.doesNotMatch(salesPage, /<th>Status<\/th>/);
  assert.doesNotMatch(salesPage, />Lunas</);
  assert.doesNotMatch(salesPage, />Belum Bayar</);
  assert.doesNotMatch(salesPage, />Sebagian</);
});

test("riwayat penjualan tidak menyediakan pelunasan piutang", () => {
  assert.doesNotMatch(salesPage, /id="mBayar"/);
  assert.doesNotMatch(salesPage, /function openBayar/);
  assert.doesNotMatch(salesPage, /function doBayar/);
  assert.doesNotMatch(salesPage, /&status=/);
});
