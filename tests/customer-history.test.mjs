import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("../frontend/customers.html", import.meta.url), "utf8");

test("daftar pelanggan memiliki filter grup dan urutan deposit", () => {
  assert.match(page, /id="filterGroup"/);
  assert.match(page, /id="sortCust"/);
  assert.match(page, /deposit_desc/);
  assert.match(page, /deposit_asc/);
  assert.match(page, /without_group=true/);
});

test("riwayat pelanggan dibuka dalam modal layar penuh dan menampilkan cabang", () => {
  assert.match(page, /id="mHistory"/);
  assert.match(page, /width: calc\(100vw - 24px\)/);
  assert.match(page, /height: calc\(100vh - 24px\)/);
  assert.match(page, /id="historyBranch"/);
  assert.match(page, />Cabang</);
  assert.match(page, /openCustomerHistory\(\$\{c\.id\}\)/);
});

test("riwayat memiliki filter, saldo berjalan, dan detail transaksi", () => {
  assert.match(page, /id="historyType"/);
  assert.match(page, /id="historyStart"/);
  assert.match(page, /id="historyEnd"/);
  assert.match(page, />Saldo</);
  assert.match(page, /openHistoryDetail/);
  assert.match(page, /history\?\$\{params\}/);
});
