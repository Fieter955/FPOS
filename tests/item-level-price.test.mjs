import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../frontend/js/item/itemForm.js", import.meta.url),
  "utf8",
);

test("Level 1 dirender sebagai harga dasar sebelum grup pelanggan", () => {
  const levelOne = source.indexOf("Level 1 — Harga Dasar");
  const groupColumns = source.indexOf(
    '${allGroups.map((g) => `<th colspan="2" class="group-label sep">${g.name}</th>`).join("")}',
  );

  assert.ok(levelOne >= 0, "Header Level 1 tidak ditemukan");
  assert.ok(groupColumns > levelOne, "Grup pelanggan harus berada setelah Level 1");
  assert.match(
    source,
    /Level 1 selalu mengikuti harga jual dasar[\s\S]*?'margin_percent'[\s\S]*?'sell_price'/,
  );
});

test("grup pertama tidak lagi dianggap sebagai harga dasar", () => {
  assert.doesNotMatch(source, /const isUmum = gIdx === 0/);
  assert.doesNotMatch(source, /Grup pertama \(Umum\)/);
  assert.doesNotMatch(source, /allGroups\[0\].*String\(groupId\)/);
  assert.doesNotMatch(source, /groupPrices\[key\] = base\.sell_price/);
});

test("Level Harga menunggu grup dan menyimpan satuan konversi", () => {
  assert.match(
    source,
    /type === "levelHarga" && !allGroups\.length\)[\s\S]*?await loadCustomerGroups\(\)/,
  );
  assert.match(
    source,
    /allGroups = await api\("GET", "\/customers\/groups"\)[\s\S]*?if \(satuanRows\.length\) syncGroupPricesFromMain\(\)/,
  );
  assert.match(
    source,
    /\["satuan", "levelHarga", "levelJumlah"\]\.includes\([\s\S]*?currentAdvancedType/,
  );
  assert.match(source, /await saveAllSatuanRows\(\)/);
});
