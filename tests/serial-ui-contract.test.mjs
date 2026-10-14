import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("shared serial picker exposes transaction-safe modes", () => {
  const picker = read("frontend/js/serial-picker.js");
  assert.match(picker, /\/serials\/options/);
  assert.match(picker, /normalize\("NFKC"\)/);
  assert.match(picker, /validateUnique/);
  assert.match(picker, /source_line_id/);
  assert.match(picker, /quarantine_id/);
  assert.match(read("frontend\/returns.html"), /quarantineId/);
  assert.match(picker, /event\.key !== "Tab"/);
  assert.match(read("frontend/css/serial-picker.css"), /100dvh/);
});

test("all supported transaction screens send serial arrays", () => {
  const screens = [
    "frontend/pos.html",
    "frontend/purchase/purchases.html",
    "frontend/returns.html",
    "frontend/trade_in.html",
    "frontend/assembly.html",
    "frontend/warehouse.html",
    "frontend/inventory.html",
  ];
  for (const screen of screens) {
    const source = read(screen);
    assert.match(source, /serial-picker\.js/, `${screen} must load the picker`);
    if (screen !== "frontend/purchase/purchases.html") {
      assert.match(source, /serials\s*:/, `${screen} must include serials in payload/state`);
    }
  }
  assert.match(read("frontend/js/components.js"), /serials:\s*d\.serials/);
});

test("serial type is configured from Master Barang like iPOS 5", () => {
  const popup = read("frontend/item/popUp.html");
  const form = read("frontend/js/item/itemForm.js");
  const inventory = read("frontend/inventory.html");
  assert.match(popup, /id="fTracksSerial"/);
  assert.match(form, /tracks_serial/);
  assert.match(form, /prepareSerialActivation/);
  assert.match(form, /\/serials\/reconciliations\/preview/);
  assert.match(form, /\/serials\/deactivate-item/);
  assert.match(popup, /id="mSerialActivation"/);
  assert.match(popup, /Aktivasi belum disimpan sampai seluruh gudang lengkap/);
  assert.match(form, /updateSerialActivationProgress/);
  assert.match(form, /normalize\("NFKC"\)/);
  assert.match(form, /function escapeItemHtml/);
  assert.match(form, /escapeItemHtml\(warehouse\.warehouse_name\)/);
  assert.doesNotMatch(form, /\besc\(/);
  assert.doesNotMatch(form, /\bprompt\s*\(/);
  assert.doesNotMatch(inventory, /serialActivationItem/);
});

test("warehouse and returns expose safe cancellation with fluid compact density", () => {
  const warehouse = read("frontend/warehouse.html");
  assert.match(warehouse, /\/warehouses\/transfers\/\$\{pendingCancelTransfer\.id\}\/cancel/);
  assert.match(warehouse, /cancelTransferReason/);
  for (const page of ["inventory.html", "warehouse.html", "po.html", "konsinyasi.html", "branches.html"]) {
    assert.match(read(`frontend/${page}`), /body class="inventory-density\b/, `${page} must opt into compact inventory layout`);
  }
  const css = read("frontend/css/style.css");
  assert.match(css, /--dense-page-pad:\s*clamp\(/, "layout spacing must scale fluidly");
  assert.match(css, /:is\(\.inventory-density,\.transaction-density\) \.main-content \{ padding:var\(--dense-page-pad\); \}/);
  assert.match(css, /@media \(max-width:40rem\)/, "phone layout must use a relative breakpoint");
  assert.match(css, /min-height:2\.75rem/, "phone controls must remain touch friendly");
  assert.match(css, /100dvh/, "phone dialogs must fit the visible viewport");
  const returns = read("frontend/returns.html");
  assert.match(returns, /mCancelReturn/);
  assert.match(returns, /cancellation_reason/);
  assert.match(returns, /sourceLineId/);
});

test("transaction pages provide Tab and Enter workflow without function-key payment", () => {
  const api = read("frontend/js/api.js");
  const pos = read("frontend/pos.html");
  assert.match(api, /keyboard-workflow/);
  assert.match(api, /event\.key !== "Enter"/);
  assert.doesNotMatch(pos, /e\.key\s*===\s*"F8"|e\.key\s*===\s*"Insert"/);
});
