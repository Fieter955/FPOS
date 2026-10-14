import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const form = readFileSync(
  new URL("../frontend/js/item/itemForm.js", import.meta.url),
  "utf8",
);
const template = readFileSync(
  new URL("../frontend/item/satuan.html", import.meta.url),
  "utf8",
);

test("advanced unit form exposes the virtual base selector and factor", () => {
  assert.match(template, /id="virtualBaseControls"/);
  assert.match(form, /window\.selectVirtualBaseUnit/);
  assert.match(form, /window\.updateVirtualBaseFactor/);
  assert.match(form, /Kemasan virtual/);
  assert.match(form, /Dasar baru/);
});

test("item save rebases directly through snapshot-protected endpoints", () => {
  assert.match(form, /async function applyPendingUnitRebase/);
  assert.match(form, /unit-conversion\/rebase\/preview/);
  assert.match(form, /unit-conversion\/rebase\/confirm/);
  assert.match(form, /editItemId = rebaseContext\.base_item_id/);
});

test("remaining variants use one effective factor against the new stock base", () => {
  assert.match(
    form,
    /function getEffectiveConversionFactor\(row\)/,
  );
  assert.match(form, /row\.factor_basis === "pending_base"/);
  assert.match(form, /pendingBaseBuyPrice \* factor/);
  assert.match(form, /const factor = getEffectiveConversionFactor\(row\)/);
  assert.match(form, /String\(rebaseContext\.base_unit_id\)/);
});

test("new rows are explicitly based on the pending base unit", () => {
  assert.match(
    form,
    /row\.factor_basis = pendingUnitRebase[\s\S]*?"pending_base"[\s\S]*?: "current_base"/,
  );
  assert.match(
    form,
    /pendingBaseBuyPrice = pendingUnitRebase[\s\S]*?sourceBuyPrice \/[\s\S]*?pendingUnitRebase\.units_per_source/,
  );
});
