import { expect, test } from "@playwright/test";

const warehouse = { id: 10, name: "Gudang Uji", code: "WH-UJI", is_default: true };
const item = {
  id: 20,
  code: "SKU-UJI",
  name: "Barang Audit",
  unit: "PCS",
  stock: 5,
  fifo_qty: 5,
  fifo_drift: 0,
  suggested_cost: 10_000,
  cost_source: "batch_transaksi_terakhir",
  snapshot_token: "snapshot-1",
  tracks_serial: false,
};

async function clickDom(locator) {
  await locator.evaluate((element) => element.click());
}

async function openInventory(page, tab, overrides = {}) {
  const requests = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}`;
    requests.push({ key, url, body: request.postDataJSON?.() });
    const defaults = {
      "GET /api/auth/permissions/me": { is_admin: true, grants: {} },
      "GET /api/inventory/document-warehouses": [warehouse],
      "GET /api/inventory/document-accounts": {
        can_override: false,
        defaults: {
          item_in: { surplus: "4-1300", shortage: null },
          item_out: { surplus: null, shortage: "5-2700" },
          opening_stock: { surplus: "3-1999", shortage: null },
          stock_opname: { surplus: "5-1200", shortage: "5-1200" },
        },
        accounts: [],
      },
      "GET /api/inventory/movements": [],
      "GET /api/inventory/documents": [],
      "GET /api/inventory/reconciliations": [],
      "GET /api/inventory/low-stock": [],
      "GET /api/serials": [],
      "GET /api/suppliers/": [],
      "GET /api/inventory/quarantines": [],
    };
    let response = overrides[key];
    if (typeof response === "function") response = await response({ request, url, requests });
    if (response === undefined) response = defaults[key];
    if (response === undefined && key === "GET /api/inventory/item-snapshot") response = [item];
    if (response === undefined) response = { detail: `Mock belum tersedia: ${key}` };
    await route.fulfill({
      status: response?.detail?.startsWith("Mock belum") ? 500 : 200,
      contentType: "application/json",
      body: JSON.stringify(response),
    });
  });

  await page.goto("/");
  await page.evaluate((selectedTab) => {
    localStorage.setItem("ipos_token", "token-uji");
    localStorage.setItem("active_branch_id", "1");
    localStorage.setItem("ipos_user", JSON.stringify({ id: 1, role: "admin", active_branch_id: 1 }));
    history.replaceState(null, "", "/workspace");
    document.body.innerHTML = `<iframe id="workspace" style="width:1280px;height:900px"></iframe>`;
    document.querySelector("#workspace").src = `/inventory.html?tab=${selectedTab}`;
  }, tab);
  const frame = page.frameLocator("#workspace");
  await expect(frame.locator("#inventoryTabs")).toBeVisible();
  return { frame, requests };
}

test("stok menipis dirender dan dapat dibuka langsung ke opname", async ({ page }) => {
  const low = [{ id: 20, code: "SKU-UJI", name: "Barang Audit", stock: 1, min_stock: 5 }];
  const { frame } = await openInventory(page, "low", {
    "GET /api/inventory/low-stock": low,
    "GET /api/inventory/item-snapshot": [item],
  });
  await expect(frame.locator("#tblLow")).toContainText("Barang Audit");
  await expect(frame.locator("#tblLow")).toContainText("MENIPIS");
  await clickDom(frame.getByRole("button", { name: "Opname", exact: true }));
  await expect(frame.locator("#docTitle")).toHaveText("Stok Opname");
  await expect(frame.locator("#docLines")).toContainText("Barang Audit");
});

test("minimum gudang diatur melalui modal dan memuat nilai saat ini", async ({ page }) => {
  let saved;
  const minimumItem = {
    ...item,
    master_min_stock: 3,
    warehouse_min_stock: 5,
    effective_min_stock: 5,
    uses_master_default: false,
  };
  const { frame } = await openInventory(page, "low", {
    "GET /api/inventory/item-snapshot": [minimumItem],
    "PUT /api/inventory/low-stock-settings": ({ request }) => {
      saved = request.postDataJSON();
      return { ...saved, uses_master_default: false };
    },
  });

  await clickDom(frame.getByRole("button", { name: "Atur minimum gudang" }));
  await expect(frame.getByRole("heading", { name: "Atur Minimum Gudang" })).toBeVisible();
  await frame.locator("#minimumSearch").fill("SKU-UJI");
  await expect(frame.locator("#minimumResults")).toContainText("Barang Audit");
  await clickDom(frame.locator("#minimumResults .search-result"));
  await expect(frame.locator("#minimumMasterValue")).toHaveText("3");
  await expect(frame.locator("#minimumEffectiveValue")).toHaveText("5");
  await expect(frame.locator("#minimumValue")).toHaveValue("5");
  await frame.locator("#minimumValue").fill("7");
  await clickDom(frame.getByRole("button", { name: "Simpan Minimum" }));
  await expect(frame.locator("#_t")).toContainText("Minimum gudang diperbarui");

  expect(saved).toEqual({ warehouse_id: 10, item_id: 20, min_stock: 7 });
});

test("item masuk mengirim payload dokumen sesuai barang dan gudang", async ({ page }) => {
  let posted;
  const { frame } = await openInventory(page, "item_in", {
    "POST /api/inventory/documents": ({ request }) => {
      posted = request.postDataJSON();
      return { number: "IM-C1-20260914-0001" };
    },
  });
  await frame.locator("#docSearch").fill("SKU-UJI");
  await expect(frame.locator(".search-result")).toContainText("Barang Audit");
  await clickDom(frame.locator(".search-result"));
  await frame.locator("#docLines .qty-input").fill("3");
  await frame.locator("#docNotes").fill("Bonus supplier");
  await clickDom(frame.getByRole("button", { name: "Simpan Item Masuk" }));
  await clickDom(frame.locator("#_cY"));
  await expect(frame.locator("#_t")).toContainText("berhasil disimpan");
  expect(posted.type).toBe("item_in");
  expect(posted.warehouse_id).toBe(10);
  expect(posted.lines).toEqual([{ item_id: 20, qty: 3, notes: "", serials: [] }]);
});

test("opname menolak barang ketika FIFO belum seimbang", async ({ page }) => {
  const drifted = { ...item, fifo_qty: 4, fifo_drift: -1 };
  const { frame } = await openInventory(page, "stock_opname", {
    "GET /api/inventory/item-snapshot": [drifted],
  });
  await frame.locator("#docSearch").fill("SKU-UJI");
  await clickDom(frame.locator(".search-result"));
  await expect(frame.locator("#_t")).toContainText("Jalankan Perbaikan Saldo dahulu");
  await expect(frame.locator("#docLines")).not.toContainText("Barang Audit");
});

test("perbaikan saldo memakai preview, alasan, token, dan konfirmasi", async ({ page }) => {
  let posted;
  const preview = {
    warehouse_id: 10,
    warehouse: "Gudang Uji",
    branch_id: 1,
    items: [{
      item_id: 20, code: "SKU-UJI", name: "Barang Audit", warehouse_qty: 5,
      fifo_qty: 4, qty_gap: 1, suggested_cost: 10_000,
      cost_source: "batch_transaksi_terakhir",
    }],
    item_count: 1,
    gl_persediaan: 50_000,
    fifo_value: 40_000,
    value_gap: 10_000,
    show_cost: true,
    can_apply: true,
    preview_token: "preview-aman",
    balanced: false,
  };
  const { frame } = await openInventory(page, "reconciliation", {
    "GET /api/inventory/reconciliation-preview": preview,
    "POST /api/inventory/reconciliations": ({ request }) => {
      posted = request.postDataJSON();
      return { number: "REK-C1-001" };
    },
  });
  await clickDom(frame.getByRole("button", { name: "Analisa Saldo" }));
  await expect(frame.locator("#reconLines")).toContainText("Barang Audit");
  await frame.locator("#reconReason").fill("Audit bulanan");
  await clickDom(frame.getByRole("button", { name: /Perbaiki Saldo/ }));
  await clickDom(frame.locator("#_cY"));
  await expect(frame.locator("#_t")).toContainText("berhasil diterapkan");
  expect(posted.preview_token).toBe("preview-aman");
  expect(posted.reason).toBe("Audit bulanan");
  expect(posted.lines).toEqual([{ item_id: 20, unit_cost: 10_000, allow_zero_cost: false }]);
});

test("halaman riwayat mempertahankan tipe dan filter gudang", async ({ page }) => {
  let query;
  const document = {
    id: 1, number: "IK-C1-001", date: "2026-09-14", line_count: 1,
    total_value: 10_000, status: "posted", can_cancel: true,
  };
  const { frame } = await openInventory(page, "item_out", {
    "GET /api/inventory/documents": ({ url }) => {
      query = url.searchParams;
      return [document];
    },
  });
  await clickDom(frame.getByRole("button", { name: "Riwayat Item Keluar" }));
  await expect(frame.locator("#historyTitle")).toHaveText("Riwayat Item Keluar");
  await expect(frame.locator("#tblHistory")).toContainText("IK-C1-001");
  expect(query.get("type")).toBe("item_out");
  expect(query.get("warehouse_id")).toBe("10");
});

test("karantina memilih beberapa barang dan mengirim batch supplier", async ({ page }) => {
  const second = { ...item, id: 21, code: "SKU-DUA", name: "Barang Kedua", stock: 8 };
  let posted;
  let supplierFilter;
  const { frame } = await openInventory(page, "quarantine", {
    "GET /api/suppliers/": [{ id: 7, code: "SUP-7", name: "Supplier Uji" }],
    "GET /api/inventory/quarantines": [],
    "GET /api/inventory/item-snapshot": ({ url }) => {
      supplierFilter = url.searchParams.get("supplier_id");
      return [item, second];
    },
    "POST /api/inventory/quarantines/batch": ({ request }) => {
      posted = request.postDataJSON();
      return { count: 2, items: [] };
    },
  });

  await frame.locator("#qSupplier").selectOption("7");
  await frame.locator("#qSearch").fill("Barang");
  await expect(frame.locator("#qResults")).toContainText("Barang Audit");
  await clickDom(frame.locator("#qResults .search-result").filter({ hasText: "Barang Audit" }));
  await frame.locator("#qSearch").fill("Kedua");
  const secondResult = frame.locator("#qResults .search-result").filter({ hasText: "Barang Kedua" });
  await expect(secondResult).toBeVisible();
  await clickDom(secondResult);
  await expect(frame.locator("#qLines tr")).toHaveCount(2);
  await page.locator("#workspace").evaluate((element) => { element.style.width = "390px"; });
  expect(await frame.locator("#qLines tr").first().evaluate((element) => getComputedStyle(element).display)).toBe("block");
  await frame.locator("#qSupplier").selectOption("");
  await expect(frame.locator("#_cN")).toBeVisible();
  await clickDom(frame.locator("#_cN"));
  await expect(frame.locator("#qSupplier")).toHaveValue("7");
  await expect(frame.locator("#qLines tr")).toHaveCount(2);
  await frame.locator("#qLines .qty-input").nth(0).fill("2");
  await frame.locator("#qNotes").fill("Rusak saat inspeksi");
  await clickDom(frame.locator("#qSave"));
  await clickDom(frame.locator("#_cY"));
  await expect(frame.locator("#_t")).toContainText("2 barang dipindahkan");

  expect(supplierFilter).toBe("7");
  expect(posted.supplier_id).toBe(7);
  expect(posted.warehouse_id).toBe(10);
  expect(posted.notes).toBe("Rusak saat inspeksi");
  expect(posted.lines).toEqual([
    { item_id: 20, qty: 2, serials: [] },
    { item_id: 21, qty: 1, serials: [] },
  ]);
});
