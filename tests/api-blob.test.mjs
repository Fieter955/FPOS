import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const sumberApi = readFileSync(
  new URL("../frontend/js/api.js", import.meta.url),
  "utf8",
);

function ambilFungsi(name, nextMarker) {
  const candidates = [`async function ${name}(`, `function ${name}(`];
  const start = candidates
    .map((candidate) => sumberApi.indexOf(candidate))
    .find((index) => index >= 0);
  assert.notEqual(start, undefined, `Fungsi ${name} tidak ditemukan`);
  const end = sumberApi.indexOf(nextMarker, start);
  assert.notEqual(end, -1, `Batas akhir fungsi ${name} tidak ditemukan`);
  return sumberApi.slice(start, end).trim();
}

function buatSimulasi({ status = 200, responseBody = "image" } = {}) {
  const storage = new Map([
    ["ipos_token", "token-aktif"],
    ["token", "token-lama-kedaluwarsa"],
    ["active_branch_id", "7"],
    ["ipos_user", "{}"],
  ]);
  const session = new Map([["fpos_effective_permissions", "cached"]]);
  const requests = [];
  const unauthorized = [];

  const localStorage = {
    getItem(key) {
      return storage.get(key) ?? null;
    },
    setItem(key, value) {
      storage.set(key, String(value));
    },
    removeItem(key) {
      storage.delete(key);
    },
  };
  const sessionStorage = {
    getItem(key) {
      return session.get(key) ?? null;
    },
    setItem(key, value) {
      session.set(key, String(value));
    },
    removeItem(key) {
      session.delete(key);
    },
  };

  const sandbox = {
    console,
    Blob,
    localStorage,
    sessionStorage,
    async fetch(path, options) {
      requests.push({ path, options });
      return {
        status,
        ok: status >= 200 && status < 300,
        headers: { get() { return null; } },
        async text() {
          return responseBody;
        },
        async blob() {
          return new Blob([responseBody]);
        },
      };
    },
    handleUnauthorized(path) {
      unauthorized.push(path);
      sandbox.clearToken();
      return true;
    },
  };
  sandbox.globalThis = sandbox;

  const functions = [
    ambilFungsi("getToken", "function setToken"),
    ambilFungsi("setToken", "function clearToken"),
    ambilFungsi("clearToken", "function registerUnsavedChangesGuard"),
    ambilFungsi("apiBlob", "// GET dengan cache"),
  ].join("\n");
  vm.runInNewContext(
    `const API_BASE = "/api";
     const PERMISSION_CACHE_KEY = "fpos_effective_permissions";
     ${functions}
     globalThis.getToken = getToken;
     globalThis.setToken = setToken;
     globalThis.clearToken = clearToken;
     globalThis.apiBlob = apiBlob;`,
    sandbox,
  );

  return { sandbox, storage, session, requests, unauthorized };
}

test("apiBlob memakai ipos_token dan cabang aktif, bukan token lama", async () => {
  const simulasi = buatSimulasi();
  const result = await simulasi.sandbox.apiBlob(
    "POST",
    "/sticker/render-sheet",
    { jumlah_kolom: 2 },
  );

  assert.ok(result.blob);
  assert.equal(simulasi.requests.length, 1);
  const request = simulasi.requests[0];
  assert.equal(request.path, "/api/sticker/render-sheet");
  assert.equal(request.options.headers.Authorization, "Bearer token-aktif");
  assert.equal(request.options.headers["X-Branch-ID"], "7");
  assert.equal(request.options.headers["Content-Type"], "application/json");
  assert.equal(request.options.body, JSON.stringify({ jumlah_kolom: 2 }));
});

test("login dan logout membersihkan key token lama", () => {
  const simulasi = buatSimulasi();
  simulasi.sandbox.setToken("token-baru");
  assert.equal(simulasi.storage.get("ipos_token"), "token-baru");
  assert.equal(simulasi.storage.has("token"), false);

  simulasi.storage.set("token", "muncul-lagi");
  simulasi.sandbox.clearToken();
  assert.equal(simulasi.storage.has("ipos_token"), false);
  assert.equal(simulasi.storage.has("token"), false);
  assert.equal(simulasi.storage.has("ipos_user"), false);
});

test("apiBlob menyerahkan respons 401 ke alur sesi kedaluwarsa", async () => {
  const simulasi = buatSimulasi({
    status: 401,
    responseBody: JSON.stringify({
      detail: "Token tidak valid atau sudah expired",
    }),
  });

  const result = await simulasi.sandbox.apiBlob(
    "POST",
    "/sticker/render-sheet",
    {},
  );

  assert.equal(result, undefined);
  assert.equal(JSON.stringify(simulasi.unauthorized), JSON.stringify(["/sticker/render-sheet"]));
  assert.equal(simulasi.storage.has("ipos_token"), false);
  assert.equal(simulasi.storage.has("token"), false);
});
