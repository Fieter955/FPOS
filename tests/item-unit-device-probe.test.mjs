import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiSource = readFileSync(
  new URL("../frontend/js/api.js", import.meta.url),
  "utf8",
);
const itemFormSource = readFileSync(
  new URL("../frontend/js/item/itemForm.js", import.meta.url),
  "utf8",
);
const itemModalSource = readFileSync(
  new URL("../frontend/item/popUp.html", import.meta.url),
  "utf8",
);

test("device-info hanya dipanggil melalui helper lazy", () => {
  assert.match(apiSource, /async function ensureFposLocalDeviceId\(\)/);
  assert.equal(
    apiSource.match(/fetch\("http:\/\/127\.0\.0\.1:19731\/device-info"/g)?.length,
    1,
  );
  assert.doesNotMatch(
    apiSource.slice(0, apiSource.indexOf("async function ensureFposLocalDeviceId")),
    /fetch\(/,
  );
});

test("form barang mewajibkan satuan dan melewati config untuk data lama", () => {
  assert.match(itemModalSource, /id="fSat"[^>]*required[^>]*aria-required="true"/);
  assert.match(itemFormSource, /Satuan dasar barang wajib dipilih/);
  assert.match(
    itemFormSource,
    /if \(editItemId && baseUnitId && !window\._editFromBOM\)/,
  );
});
