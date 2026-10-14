import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const inventory = readFileSync(new URL("../frontend/inventory.html", import.meta.url), "utf8");

test("form dokumen persediaan tidak lagi berdampingan dengan riwayat", () => {
  assert.doesNotMatch(inventory, /class="doc-layout"/);
  assert.match(inventory, /id="openHistoryBtn"/);
  assert.match(inventory, /id="pnlHistory"/);
});

test("riwayat dokumen memiliki halaman khusus per jenis dokumen", () => {
  assert.match(inventory, /view=history/);
  assert.match(inventory, /Riwayat \$\{cfg\.title\}/);
  assert.match(inventory, /Kembali ke Form/);
  assert.match(inventory, /id="historyWarehouse"/);
  assert.match(inventory, /type:state\.docType/);
});
