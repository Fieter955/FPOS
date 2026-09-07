import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const returnsPage = readFileSync(
  new URL("../frontend/returns.html", import.meta.url),
  "utf8",
);

test("pencarian barang retur memakai dropdown terfilter", () => {
  assert.match(returnsPage, /id="mManualItemCombo"/);
  assert.match(
    returnsPage,
    /createPremiumCombo\("mManualItemCombo", \[\], \{[\s\S]*?isItem: true/,
  );
  assert.match(
    returnsPage,
    /mManualItemCombo\.updateData\(mManualValidItems\)/,
  );
  assert.match(returnsPage, /onSelect: async \(item\) => \{/);
  assert.doesNotMatch(returnsPage, /Tampilkan seluruh barang dari semua transaksi/);
});

test("daftar barang tetap dibatasi oleh supplier atau pelanggan terpilih", () => {
  assert.match(
    returnsPage,
    /`\/returns\/history\/\$\{historyType\}\?\$\{entityParam\}=\$\{entity\.id\}`/,
  );
  assert.match(returnsPage, /const itemsById = new Map\(\)/);
  assert.match(returnsPage, /returnTransactions: \[\]/);
  assert.match(returnsPage, /mManualItemCombo\.disable\(\)/);
  assert.match(returnsPage, /mManualItemCombo\.enable\(\)/);
});

test("setiap barang menyediakan pilihan seluruh faktur di tabel", () => {
  assert.match(returnsPage, />Faktur \/ Invoice<\/th>/);
  assert.match(returnsPage, /class="input-control row-invoice"/);
  assert.match(returnsPage, /onchange="selectManualReturnTransaction\(this\)"/);
  assert.match(returnsPage, /row\._returnTransactions/);
  assert.match(returnsPage, /function applyManualReturnTransaction/);
});

test("popup retur manual memenuhi ruang halaman", () => {
  assert.match(returnsPage, /class="modal-box manual-return-modal-box"/);
  assert.match(returnsPage, /width: calc\(100vw - 24px\)/);
  assert.match(returnsPage, /height: calc\(100vh - 24px\)/);
  assert.match(returnsPage, /class="tbl-wrap manual-return-table-wrap"/);
  assert.match(returnsPage, /class="manual-return-actions"/);
});
