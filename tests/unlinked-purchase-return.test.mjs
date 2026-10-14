import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const source = fs.readFileSync(new URL("../frontend/returns.html", import.meta.url), "utf8");

test("form retur pembelian menyediakan mode tanpa faktur dan bukti", () => {
  assert.match(source, /id="mPurchaseReturnMode"/);
  assert.match(source, /value="unlinked">Tanpa Faktur/);
  assert.match(source, /id="mEvidenceReference"/);
  assert.match(source, /payload\.return_mode = "unlinked"/);
  assert.match(source, /payload\.supplier_id = mManualSelectedEntity\.id/);
  assert.match(source, /unlinked-stock-options\/\$\{item\.id\}/);
  assert.match(source, /changeUnlinkedReturnCondition/);
  assert.match(source, /damagedSourceOptions/);
  assert.match(source, /row\.querySelector\("\.row-avail"\)\.textContent = toDesimal\(available/);
  assert.match(source, /select\.disabled = lockCondition/);
});

test("alur retur tidak lagi meminta kandidat swap aktif", () => {
  assert.doesNotMatch(source, /`\/returns\/swap-candidates\?/);
});
