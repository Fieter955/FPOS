import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const po = readFileSync(new URL("../frontend/po.html", import.meta.url), "utf8");
const receive = readFileSync(new URL("../frontend/purchase/catat-pembelian.html", import.meta.url), "utf8");
const components = readFileSync(new URL("../frontend/js/components.js", import.meta.url), "utf8");
const returns = readFileSync(new URL("../frontend/returns.html", import.meta.url), "utf8");

test("ACC cabang diarahkan ke fulfillment, bukan request induk", () => {
  assert.match(receive, /terimaBarangCabang\(\$\{initialData\.id\}\)/);
  assert.doesNotMatch(receive, /terimaBarangCabang\(\$\{initialData\.from_po_id\}\)/);
  assert.match(receive, /Tunggu kiriman dari Pusat/);
});

test("UI membawa provenance baris dan hanya mengirim outstanding", () => {
  assert.match(components, /source_request_item_id: row\._sourceRequestItemId/);
  assert.match(components, /it\.qty_outstanding/);
  assert.match(po, /const outstandingItems = data\.items\.filter/);
  assert.match(po, /DITERIMA SEBAGIAN/);
});

test("retur kiriman pusat tersedia untuk cabang dan ACC pusat", () => {
  assert.match(returns, /\/returns\/branch-shipments/);
  assert.match(returns, /\/returns\/branch-to-center/);
  assert.match(returns, /ACC Retur dari Cabang/);
});
