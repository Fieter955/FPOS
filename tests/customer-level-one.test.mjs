import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const customers = readFileSync(
  new URL("../frontend/customers.html", import.meta.url),
  "utf8",
);
const pos = readFileSync(
  new URL("../frontend/pos.html", import.meta.url),
  "utf8",
);
const posAlternative = readFileSync(
  new URL("../frontend/pos_2.html", import.meta.url),
  "utf8",
);
const itemForm = readFileSync(
  new URL("../frontend/js/item/itemForm.js", import.meta.url),
  "utf8",
);
const customerRoutes = readFileSync(
  new URL("../backend/app/routes/customers.py", import.meta.url),
  "utf8",
);

test("Level 1 tampil sebagai grup bawaan dengan diskon tetap nol", () => {
  assert.match(customers, /data-system-group="level-1"/);
  assert.match(
    customers,
    /data-system-group="level-1"[\s\S]*?>Level 1<[\s\S]*?>0%<[\s\S]*?>Bawaan</,
  );
  assert.match(customers, /Harga umum tanpa level harga khusus/);
});

test("pelanggan tanpa group_id ditampilkan dan dipilih sebagai Level 1", () => {
  assert.match(
    customers,
    /<option value="">Level 1 — Harga Umum \(0%\)<\/option>/,
  );
  assert.match(
    customers,
    /<option value="none">Level 1 — Harga Umum \(0%\)<\/option>/,
  );
  assert.match(customers, /c\.group\?\.name \|\| "Level 1 — Harga Umum"/);
  assert.match(pos, /Level 1 — Harga Umum \(0%\)/);
  assert.match(posAlternative, /Level 1 — Harga Umum \(0%\)/);
});

test("Level 1 muncul di informasi grup barang dan namanya dicadangkan backend", () => {
  assert.match(itemForm, /Level 1[\s\S]*?\(Harga Umum\)[\s\S]*?>0%</);
  assert.match(customerRoutes, /def _ensure_not_system_level_one/);
  assert.match(customerRoutes, /if normalized == "level1"/);
  assert.equal(
    (customerRoutes.match(/_ensure_not_system_level_one\(g\.name\)/g) || [])
      .length,
    2,
    "Validasi Level 1 harus berlaku saat membuat dan mengubah grup",
  );
});
