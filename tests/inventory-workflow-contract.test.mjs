import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const inventory = readFileSync(new URL("../frontend/inventory.html", import.meta.url), "utf8");
const warehouse = readFileSync(new URL("../frontend/warehouse.html", import.meta.url), "utf8");
const po = readFileSync(new URL("../frontend/po.html", import.meta.url), "utf8");

test("mutasi memakai kategori dan arah kanonis", () => {
  assert.match(inventory, /m\.category\|\|m\.type/);
  assert.match(inventory, /direction===\"neutral\"/);
  assert.match(inventory, /internal_transfer:\"PINDAH GUDANG\"/);
  assert.match(inventory, /quarantine_supplier_return:\"RETUR SUPPLIER \(KARANTINA\)\"/);
  assert.match(inventory, /quarantine_writeoff:\"HAPUS KARANTINA\"/);
  assert.match(inventory, /quarantine_supplier_return_reversal:\"BATAL RETUR KARANTINA\"/);
  assert.equal((inventory.match(/async function loadMutasi\(/g) || []).length, 1);
});

test("stok menipis menyediakan filter lokasi kategori dan status", () => {
  assert.match(inventory, /id="lowWarehouse"/);
  assert.match(inventory, /id="lowCategory"/);
  assert.match(inventory, /value="out_of_stock">Habis/);
});

test("minimum gudang memakai modal aplikasi tanpa prompt browser", () => {
  assert.match(inventory, /id="minimumModal"/);
  assert.match(inventory, /openWarehouseMinimumModal/);
  assert.match(inventory, /saveWarehouseMinimum/);
  assert.match(inventory, /warehouse_min_stock/);
  assert.doesNotMatch(inventory, /function setWarehouseMinimum\(\).*prompt\(/);
});

test("karantina mandiri mendukung pulih hapus dan retur supplier", () => {
  assert.match(inventory, /data-tab="quarantine"/);
  assert.match(inventory, /inventory\/quarantines/);
  assert.match(inventory, /source_type=inventory_quarantine/);
  assert.match(inventory, /openQuarantineSupplierReturn/);
  assert.match(inventory, /id="qLines"/);
  assert.match(inventory, /quarantines\/batch/);
  assert.match(inventory, /supplier_id/);
  assert.match(inventory, /changeQuarantineContext/);
  assert.match(inventory, /inventory_quarantine_resolution/);
  assert.match(inventory, /quarantineId:row\.id/);
});

test("transfer menampilkan detail dan penerimaan berdasarkan kondisi", () => {
  assert.match(warehouse, /openTransferDetail/);
  assert.match(warehouse, /good_qty:good/);
  assert.match(warehouse, /damaged_qty:damaged/);
  assert.match(warehouse, /missing_qty:missing/);
});

test("ACC PO mengirim hasil pemeriksaan fisik", () => {
  assert.match(po, /Jumlah BAGUS/);
  assert.match(po, /Jumlah RUSAK/);
  assert.match(po, /receive-branch`, \{items, notes\}/);
});
