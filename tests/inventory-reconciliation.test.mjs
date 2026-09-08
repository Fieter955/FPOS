import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const inventory = readFileSync(new URL("../frontend/inventory.html", import.meta.url), "utf8");
const dashboard = readFileSync(new URL("../frontend/dashboard.html", import.meta.url), "utf8");

test("stok opname membedakan stok fisik, modal surplus, dan rekonsiliasi FIFO", () => {
  assert.match(inventory, /Harga Modal Surplus/);
  assert.match(inventory, /FIFO barang belum seimbang\. Jalankan Perbaikan Saldo dahulu/);
  assert.match(inventory, /adjustment_account_id/);
  assert.match(inventory, /allow_zero_cost/);
});

test("perbaikan saldo memakai pratinjau, token, alasan, dan konfirmasi", () => {
  assert.match(inventory, /\/inventory\/reconciliation-preview\?warehouse_id=/);
  assert.match(inventory, /preview_token:r\.preview_token/);
  assert.match(inventory, /\/inventory\/reconciliations/);
  assert.match(inventory, /Alasan perbaikan \*/);
  assert.match(inventory, /Pratinjau sudah benar — Perbaiki Saldo/);
});

test("dashboard menyediakan menu Perbaikan Saldo terpisah dari Stok Opname", () => {
  assert.match(dashboard, /menuItem\("Perbaikan Saldo", "\/inventory\?tab=reconciliation"/);
  assert.match(dashboard, /"inventory\.repair_balance"/);
});
