import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const returnsPage = readFileSync(
  new URL("../frontend/returns.html", import.meta.url),
  "utf8",
);
const tradeInPage = readFileSync(
  new URL("../frontend/trade_in.html", import.meta.url),
  "utf8",
);

test("tukar tambah hanya menawarkan kondisi bagus atau rusak", () => {
  assert.match(tradeInPage, /<option value="good">Bagus<\/option>/);
  assert.match(tradeInPage, /<option value="damaged">Rusak \(Broken\)<\/option>/);
  assert.doesNotMatch(tradeInPage, /<option value="partial">Parsial<\/option>/);
});

test("retur penjualan mengirim kondisi per baris", () => {
  assert.match(returnsPage, /id="mReturnCondition"/);
  assert.match(returnsPage, /class="input-control row-condition"/);
  assert.match(returnsPage, /itemData\.condition = row\.querySelector\("\.row-condition"\)\.value/);
});

test("retur barang rusak memfilter supplier dan membawa sumber karantina", () => {
  assert.match(returnsPage, /history\/broken\/\$\{item\.source_type/);
  assert.match(returnsPage, /pendingBrokenSuppliers/);
  assert.match(returnsPage, /row\.dataset\.damagedSourceType/);
  assert.match(returnsPage, /itemData\.damaged_source_item_id/);
});
