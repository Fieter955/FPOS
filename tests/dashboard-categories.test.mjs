import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dashboard = readFileSync(
  new URL("../frontend/dashboard.html", import.meta.url),
  "utf8",
);
const api = readFileSync(
  new URL("../frontend/js/api.js", import.meta.url),
  "utf8",
);
const workspace = readFileSync(
  new URL("../frontend/js/workspace.js", import.meta.url),
  "utf8",
);

const categoryLabels = [
  "Master Data",
  "Pembelian",
  "Penjualan",
  "Perakitan",
  "Persediaan",
  "Akuntansi",
  "Laporan",
  "Pengaturan",
];

test("dashboard memakai delapan kategori iPos dalam urutan yang benar", () => {
  let previousIndex = -1;
  for (const label of categoryLabels) {
    const index = dashboard.indexOf(`label: "${label}"`);
    assert.ok(index > previousIndex, `${label} harus ada dan berurutan`);
    previousIndex = index;
  }
  assert.doesNotMatch(dashboard, /<h2 class="section-title">/);
  assert.match(dashboard, /id="menuCategoryTabs"/);
  assert.match(dashboard, /role="tablist"/);
});

test("registry menu menyaring izin fitur dan izin tampil kategori", () => {
  assert.match(dashboard, /hasPermission\(category\.menuPermission, "show", state\)/);
  assert.match(dashboard, /hasPermission\(item\.permission, item\.action, state\)/);
  assert.match(dashboard, /filter\(\(category\) => category\.groups\.length\)/);
  assert.match(dashboard, /activateMenuCategory\(visibleCategories\[0\]\.key\)/);
});

test("shortcut rinci memiliki kontrak deep-link dan memakai ulang tab workspace", () => {
  for (const target of [
    "/item/items?tab=kat",
    "/purchases?tab=payment",
    "/assembly?tab=results",
    "/inventory?tab=stock_opname",
    "/warehouse?tab=transfer",
    "/konsinyasi?tab=out",
    "/reports?tab=pl",
    "/accounting?tab=coa",
    "/settings?tab=backup",
    "/users?tab=permission",
  ]) {
    assert.ok(dashboard.includes(target), `${target} belum terdaftar`);
  }
  assert.match(api, /function setupRoutedTabs/);
  assert.match(api, /event\.data\?\.type !== "fpos-route-state"/);
  assert.match(workspace, /\{ type: "fpos-route-state", url \}/);
});

test("KPI dan badge pesanan tetap dipertahankan", () => {
  assert.match(dashboard, /id="kpiGrid"/);
  assert.match(dashboard, /badgeId: "poBadge"/);
  assert.match(dashboard, /if \(!badge\) return/);
  assert.match(dashboard, /loadKPI\(\)/);
  assert.match(dashboard, /loadPOCount\(\)/);
});
