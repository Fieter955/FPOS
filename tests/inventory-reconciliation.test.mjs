import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const inventory = readFileSync(new URL("../frontend/inventory.html", import.meta.url), "utf8");
const dashboard = readFileSync(new URL("../frontend/dashboard.html", import.meta.url), "utf8");

test("stok opname memakai HPP sistem dan memisahkan rekonsiliasi FIFO", () => {
  assert.match(inventory, /HPP Sistem/);
  assert.match(inventory, /FIFO barang belum seimbang\. Jalankan Perbaikan Saldo dahulu/);
  assert.match(inventory, /adjustment_account_id/);
  assert.doesNotMatch(inventory, /line\.unit_cost|line\.allow_zero_cost/);
  assert.match(inventory, /Harga modal disembunyikan sesuai hak akses/);
  assert.doesNotMatch(inventory, /const i=state\.lines\[index\],surplus=/);
  assert.match(inventory, /money\(i\.suggested_cost\)/);
});

test("perbaikan saldo memakai pratinjau, token, alasan, dan konfirmasi", () => {
  assert.match(inventory, /\/inventory\/reconciliation-preview\?warehouse_id=/);
  assert.match(inventory, /preview_token:r\.preview_token/);
  assert.match(inventory, /\/inventory\/reconciliations/);
  assert.match(inventory, /Alasan perbaikan \*/);
  assert.match(inventory, /Pratinjau sudah benar — Perbaiki Saldo/);
  assert.match(inventory, /<b>\$\{money\(i\.suggested_cost\)\}<\/b>/);
  assert.match(inventory, /Stok Master/);
  assert.match(inventory, /Target Master/);
  assert.match(inventory, /Stok master berbeda/);
});

test("dashboard menyediakan menu Perbaikan Saldo terpisah dari Stok Opname", () => {
  assert.match(dashboard, /menuItem\("Perbaikan Saldo", "\/inventory\?tab=reconciliation"/);
  assert.match(dashboard, /"inventory\.repair_balance"/);
});
