import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const sumberApi = readFileSync(
  new URL("../frontend/js/api.js", import.meta.url),
  "utf8",
);
const sumberWorkspace = readFileSync(
  new URL("../frontend/js/workspace.js", import.meta.url),
  "utf8",
);
const sumberCabang = readFileSync(
  new URL("../frontend/branches.html", import.meta.url),
  "utf8",
);
const awalSwitcher = sumberApi.indexOf("const GLOBAL_BRANCH_SWITCHER_ID");
const akhirSwitcher = sumberApi.indexOf("// ── INIT GLOBAL", awalSwitcher);

assert.notEqual(awalSwitcher, -1, "Helper switcher cabang tidak ditemukan");
assert.notEqual(akhirSwitcher, -1, "Batas helper switcher cabang tidak ditemukan");

class ElemenPalsu {
  constructor(tagName, document) {
    this.tagName = tagName.toUpperCase();
    this.document = document;
    this.children = [];
    this.style = {};
    this.listeners = new Map();
    this.parent = null;
    this._id = "";
    this.value = "";
    this.textContent = "";
    this.selected = false;
  }

  set id(value) {
    this._id = value;
    if (value) this.document.byId.set(value, this);
  }

  get id() {
    return this._id;
  }

  append(...children) {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }

  appendChild(child) {
    this.append(child);
    return child;
  }

  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }

  addEventListener(type, callback) {
    this.listeners.set(type, callback);
  }

  remove() {
    if (this.parent) {
      this.parent.children = this.parent.children.filter((child) => child !== this);
    }
    if (this.id) this.document.byId.delete(this.id);
  }
}

function buatKotakPasir({ embedded = false } = {}) {
  const document = {
    byId: new Map(),
    createElement(tagName) {
      return new ElemenPalsu(tagName, document);
    },
    getElementById(id) {
      return document.byId.get(id) || null;
    },
  };
  document.body = new ElemenPalsu("body", document);

  const cacheDihapus = [];
  const pesan = [];
  const local = new Map([["active_branch_id", "1"]]);
  let daftarCabang = [{ id: 1, name: "Pusat" }];
  const kotakPasir = {
    console,
    document,
    location: { origin: "https://fpos.test" },
    localStorage: {
      getItem(key) {
        return local.get(key) ?? null;
      },
      setItem(key, value) {
        local.set(key, String(value));
      },
    },
    window: {
      location: { reload() {} },
      parent: {
        postMessage(message, origin) {
          pesan.push({ message, origin });
        },
      },
    },
    getUser() {
      return { id: 1, role: "admin", active_branch_id: 1 };
    },
    async cachedApi() {
      return daftarCabang;
    },
    async api() {
      return { id: 1, role: "admin", active_branch_id: 1 };
    },
    invalidateCache(path) {
      cacheDihapus.push(path);
    },
  };
  kotakPasir.globalThis = kotakPasir;

  vm.runInNewContext(
    `const FPOS_WORKSPACE_EMBEDDED = ${embedded};\n` +
      `${sumberApi.slice(awalSwitcher, akhirSwitcher)}\n` +
      "globalThis.branchHelpers = { refreshBranchSwitcher, notifyBranchListChanged };",
    kotakPasir,
  );

  return {
    ...kotakPasir,
    cacheDihapus,
    pesan,
    setDaftarCabang(value) {
      daftarCabang = value;
    },
  };
}

test("refresh memperbarui dropdown yang sama tanpa membuat duplikat", async () => {
  const sandbox = buatKotakPasir();
  await sandbox.branchHelpers.refreshBranchSwitcher();

  sandbox.setDaftarCabang([
    { id: 1, name: "Pusat" },
    { id: 2, name: "Cabang Baru" },
  ]);
  await sandbox.branchHelpers.refreshBranchSwitcher({ force: true });

  assert.equal(sandbox.document.body.children.length, 1);
  const select = sandbox.document.getElementById("globalBranchSelect");
  assert.equal(select.children.length, 2);
  assert.equal(select.children[1].textContent, "📍 Cabang Baru");
  assert.deepEqual(sandbox.cacheDihapus, ["/branches/"]);
});

test("halaman embedded menghapus cache dan memberi tahu workspace", async () => {
  const sandbox = buatKotakPasir({ embedded: true });

  await sandbox.branchHelpers.notifyBranchListChanged();

  assert.deepEqual(sandbox.cacheDihapus, ["/branches/"]);
  assert.equal(sandbox.pesan.length, 1);
  assert.equal(sandbox.pesan[0].message.type, "fpos-branches-changed");
  assert.equal(sandbox.pesan[0].origin, "https://fpos.test");
});

test("halaman cabang dan workspace terhubung ke notifikasi perubahan", () => {
  assert.equal(
    sumberCabang.match(/await notifyBranchListChanged\(\);/g)?.length,
    2,
  );
  assert.match(sumberWorkspace, /message\.type === "fpos-branches-changed"/);
  assert.match(sumberWorkspace, /refreshBranchSwitcher\(\{ force: true \}\)/);
});
