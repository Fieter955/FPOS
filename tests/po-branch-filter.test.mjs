import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../frontend/po.html", import.meta.url),
  "utf8",
);

test("filter permintaan PO pusat menyediakan dropdown cabang", () => {
  assert.match(source, /id="reqBranch"[\s\S]*?<option value="">Semua Cabang<\/option>/);
  assert.match(source, /id="reqBranch"[\s\S]*?onchange="loadRequests\(\)"/);
});

test("cabang terpilih dikirim ke endpoint permintaan PO", () => {
  assert.match(source, /params\.set\("branch_id", branchId\)/);
  assert.match(source, /`\/po\/requests\?\$\{params\.toString\(\)\}`/);
});

test("dropdown cabang diisi sebelum permintaan awal dimuat", () => {
  const setupIndex = source.indexOf("setupRequestBranchFilter();");
  const loadIndex = source.indexOf("if (isMainStore()) await loadRequests();");

  assert.ok(setupIndex >= 0, "inisialisasi dropdown cabang tidak ditemukan");
  assert.ok(loadIndex > setupIndex, "permintaan dimuat sebelum dropdown siap");
});
