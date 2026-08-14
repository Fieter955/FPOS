const API_BASE = "/api";
const SESSION_EXPIRED_NOTICE_KEY = "fpos_session_expired_notice";
let fposUnsavedChangesGuard = null;
let fposSuppressNextUnloadWarning = false;

const FPOS_WORKSPACE_EMBEDDED = (() => {
  if (window.self === window.top) return false;
  try {
    return window.parent.location.pathname.replace(/\.html$/, "") === "/workspace";
  } catch {
    return false;
  }
})();

function markSessionExpired() {
  try {
    sessionStorage.setItem(SESSION_EXPIRED_NOTICE_KEY, "1");
  } catch {}
}

function consumeSessionExpiredNotice() {
  try {
    const shouldNotify =
      sessionStorage.getItem(SESSION_EXPIRED_NOTICE_KEY) === "1";
    sessionStorage.removeItem(SESSION_EXPIRED_NOTICE_KEY);
    return shouldNotify;
  } catch {
    return false;
  }
}

function redirectToLogin({ sessionExpired = false } = {}) {
  if (sessionExpired) markSessionExpired();
  if (FPOS_WORKSPACE_EMBEDDED) {
    window.parent.postMessage(
      { type: "fpos-auth-expired", sessionExpired },
      location.origin,
    );
    try {
      window.top.location.replace("/login");
      return;
    } catch {}
  }
  window.location.replace("/login");
}

function handleUnauthorized(path) {
  // HTTP 401 dari endpoint login berarti kredensial salah, bukan sesi kedaluwarsa.
  // Biarkan apiForm() membaca detail error agar tetap tampil di formulir login.
  if (path === "/auth/login") return false;
  clearToken();
  redirectToLogin({ sessionExpired: true });
  return true;
}

function openWorkspaceTab(url, title = "", reuseExisting = false) {
  const resolved = new URL(url, location.href);
  if (FPOS_WORKSPACE_EMBEDDED) {
    window.parent.postMessage(
      {
        type: "fpos-open-tab",
        url: resolved.href,
        title,
        reuseExisting: Boolean(reuseExisting),
      },
      location.origin,
    );
    return;
  }
  window.location.href = resolved.href;
}

function focusWorkspaceDashboard() {
  if (FPOS_WORKSPACE_EMBEDDED) {
    window.parent.postMessage({ type: "fpos-focus-dashboard" }, location.origin);
    return;
  }
  window.location.href = "/dashboard";
}

// Copot service worker lama + bersihkan cache-nya. SW lama (pass-through di sw.js &
// cache-first di service-worker.js) hanya menambah overhead per-request dan bikin
// loading/navigasi di browser lebih lambat dari versi .exe. Idempoten: setelah bersih,
// jadi no-op. (Pendaftaran SW sudah dihapus dari index.html & pos.html.)
if (!FPOS_WORKSPACE_EMBEDDED && "serviceWorker" in navigator) {
  navigator.serviceWorker
    .getRegistrations()
    .then((rs) => rs.forEach((r) => r.unregister()))
    .catch(() => {});
  if (window.caches) {
    caches
      .keys()
      .then((ks) => ks.forEach((k) => caches.delete(k)))
      .catch(() => {});
  }
}

function getToken() {
  return localStorage.getItem("ipos_token");
}

function setToken(t) {
  localStorage.setItem("ipos_token", t);
  sessionStorage.removeItem("fpos_effective_permissions");
}

function clearToken() {
  localStorage.removeItem("ipos_token");
  localStorage.removeItem("ipos_user");
  // Bersihkan juga sesi cabang saat logout
  localStorage.removeItem("active_branch_id");
  sessionStorage.removeItem("fpos_effective_permissions");
}

function registerUnsavedChangesGuard(guard) {
  fposUnsavedChangesGuard = guard || null;
  notifyUnsavedChangesChanged();
  return () => {
    if (fposUnsavedChangesGuard === guard) {
      fposUnsavedChangesGuard = null;
      notifyUnsavedChangesChanged();
    }
  };
}

function hasUnsavedChanges() {
  try {
    return Boolean(fposUnsavedChangesGuard?.isDirty?.());
  } catch (error) {
    console.error("Gagal memeriksa perubahan yang belum disimpan", error);
    return true;
  }
}

function notifyUnsavedChangesChanged() {
  if (!FPOS_WORKSPACE_EMBEDDED) return;
  window.parent.postMessage(
    {
      type: "fpos-unsaved-state",
      dirty: hasUnsavedChanges(),
      label: fposUnsavedChangesGuard?.label || document.title,
    },
    location.origin,
  );
}

async function saveUnsavedChanges() {
  if (!hasUnsavedChanges()) return true;
  if (typeof fposUnsavedChangesGuard?.save !== "function") return false;
  try {
    const saved = (await fposUnsavedChangesGuard.save()) !== false;
    if (saved) notifyUnsavedChangesChanged();
    return saved && !hasUnsavedChanges();
  } catch (error) {
    console.error("Gagal menyimpan perubahan sebelum keluar", error);
    if (typeof showToast === "function") {
      showToast(error?.message || "Gagal menyimpan perubahan", "error");
    }
    return false;
  }
}

async function discardUnsavedChanges() {
  fposSuppressNextUnloadWarning = true;
  try {
    await fposUnsavedChangesGuard?.discard?.();
  } finally {
    notifyUnsavedChangesChanged();
    setTimeout(() => {
      fposSuppressNextUnloadWarning = false;
    }, 1500);
  }
}

async function resolveUnsavedChangesBeforeExit(message, options = {}) {
  if (!hasUnsavedChanges()) return true;
  const action = await showUnsavedChangesDialog(message, options);
  if (action === "cancel") return false;
  if (action === "discard") {
    await discardUnsavedChanges();
    return true;
  }
  return saveUnsavedChanges();
}

async function logoutCurrentUser() {
  if (FPOS_WORKSPACE_EMBEDDED) {
    window.parent.postMessage({ type: "fpos-request-logout" }, location.origin);
    return;
  }

  let confirmed;
  if (typeof window.fposPrepareForLogout === "function") {
    confirmed = await window.fposPrepareForLogout();
  } else if (hasUnsavedChanges()) {
    confirmed = await resolveUnsavedChangesBeforeExit(
      "Perubahan belum disimpan. Apa yang ingin dilakukan sebelum keluar dari akun?",
      {
        saveText: "Simpan & Keluar",
        discardText: "Keluar Tanpa Simpan",
      },
    );
  } else {
    confirmed =
      typeof showConfirm === "function"
        ? await showConfirm("Yakin ingin keluar dari akun saat ini?")
        : window.confirm("Yakin ingin keluar dari akun saat ini?");
  }
  if (!confirmed) return;

  clearToken();
  redirectToLogin();
}

function getUser() {
  try {
    return JSON.parse(localStorage.getItem("ipos_user") || "{}");
  } catch {
    return {};
  }
}

function isMainStore() {
  const user = getUser();
  const activeBranchId =
    localStorage.getItem("active_branch_id") || user.active_branch_id;
  // Main store is branch ID 1 OR branch status "Toko Utama"
  return activeBranchId == 1 || user.branch_status === "Toko Utama";
}

async function api(method, path, body = null) {
  const h = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) {
    h["Authorization"] = `Bearer ${tok}`;

    // 👇 INJEKSI ID CABANG SECARA GLOBAL KE BACKEND 👇
    const activeBranch = localStorage.getItem("active_branch_id");
    if (activeBranch) h["X-Branch-ID"] = activeBranch;
  }

  const o = { method, headers: h };
  if (body) o.body = JSON.stringify(body);

  let r;
  try {
    r = await fetch(API_BASE + path, o);
  } catch (e) {
    throw new Error("Tidak bisa terhubung ke server.");
  }

  if (r.status === 401 && handleUnauthorized(path)) {
    return;
  }
  if (r.status === 403) {
    sessionStorage.removeItem(PERMISSION_CACHE_KEY);
  }

  let d;
  try {
    d = await r.json();
  } catch {
    throw new Error(
      "Respons server tidak valid (bukan JSON). Cek terminal backend.",
    );
  }

  if (!r.ok) {
    let msg = d.detail;
    if (Array.isArray(msg)) {
      // FastAPI 422 validation errors
      msg = msg
        .map((e) => {
          const loc = (e.loc || []).slice(1).join(".");
          return loc ? `${loc}: ${e.msg}` : e.msg;
        })
        .join("\n");
    } else if (msg && typeof msg === "object") {
      msg = JSON.stringify(msg);
    }
    throw new Error(msg || `HTTP ${r.status}`);
  }

  return d;
}

async function apiForm(path, fd) {
  const h = {};
  const tok = getToken();
  if (tok) {
    h["Authorization"] = `Bearer ${tok}`;

    // 👇 INJEKSI ID CABANG SECARA GLOBAL KE BACKEND 👇
    const activeBranch = localStorage.getItem("active_branch_id");
    if (activeBranch) h["X-Branch-ID"] = activeBranch;
  }

  let r;
  try {
    r = await fetch(API_BASE + path, { method: "POST", headers: h, body: fd });
  } catch (e) {
    throw new Error("Tidak bisa terhubung.");
  }

  if (r.status === 401 && handleUnauthorized(path)) {
    return;
  }

  let d;
  try {
    d = await r.json();
  } catch {
    throw new Error("Respons server tidak valid.");
  }

  if (!r.ok) {
    let msg = d.detail;
    if (Array.isArray(msg))
      msg = msg.map((e) => e.msg || JSON.stringify(e)).join("; ");
    throw new Error(msg || "Terjadi kesalahan");
  }

  return d;
}

// GET dengan cache di sessionStorage untuk data master yang jarang berubah
// (kategori, merek, satuan, cabang). Mengurangi request berulang tiap pindah halaman.
// Cache otomatis hilang saat tab/sesi ditutup. Invalidasi manual via invalidateCache(path)
// setelah data diubah. ttlMs default 5 menit sebagai jaring pengaman.
async function cachedApi(path, ttlMs = 300000) {
  const key = "cache:" + path;
  try {
    const raw = sessionStorage.getItem(key);
    if (raw) {
      const { t, d } = JSON.parse(raw);
      if (Date.now() - t < ttlMs) return d;
    }
  } catch {}
  const data = await api("GET", path);
  try {
    // Jangan cache respons kosong (mis. saat 401/redirect) agar tidak meracuni cache 5 menit.
    if (data !== undefined && data !== null) {
      sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), d: data }));
    }
  } catch {}
  return data;
}

function invalidateCache(path) {
  try {
    sessionStorage.removeItem("cache:" + path);
  } catch {}
}

function requireAuth() {
  if (!getToken()) redirectToLogin();
}

function redirectStandalonePageToWorkspace() {
  if (FPOS_WORKSPACE_EMBEDDED || !getToken()) return;
  let path = location.pathname.replace(/\.html$/, "");
  if (path.length > 1) path = path.replace(/\/$/, "");
  if (["/", "/index", "/login", "/workspace"].includes(path)) return;
  const start = `${path}${location.search}${location.hash}`;
  window.location.replace(`/workspace?start=${encodeURIComponent(start)}`);
}

redirectStandalonePageToWorkspace();

// ==============================================================================
// Hak akses berbasis role
// ==============================================================================
const PERMISSION_CACHE_KEY = "fpos_effective_permissions";

function getPermissionState() {
  try {
    return JSON.parse(sessionStorage.getItem(PERMISSION_CACHE_KEY) || "null");
  } catch {
    return null;
  }
}

async function loadMyPermissions(force = false) {
  if (!getToken()) return { is_admin: false, grants: {} };
  const cached = getPermissionState();
  if (!force && cached) return cached;
  const state = await api("GET", "/auth/permissions/me");
  try {
    sessionStorage.setItem(PERMISSION_CACHE_KEY, JSON.stringify(state));
  } catch {}
  return state;
}

function hasPermission(permissionKey, action = "view", state = null) {
  const permissions = state || getPermissionState();
  if (!permissions) return false;
  if (permissions.is_admin) return true;
  return (permissions.grants?.[permissionKey] || []).includes(action);
}

async function can(permissionKey, action = "view") {
  const state = await loadMyPermissions();
  return hasPermission(permissionKey, action, state);
}

function renderAccessDenied(message = "Anda tidak memiliki hak akses untuk membuka modul ini.") {
  if (document.getElementById("permissionDeniedOverlay")) return;
  const overlay = document.createElement("div");
  overlay.id = "permissionDeniedOverlay";
  overlay.style.cssText =
    "position:fixed;inset:0;z-index:99999999;background:var(--bg-color,#eef2f7);display:flex;align-items:center;justify-content:center;padding:24px;";
  overlay.innerHTML = `
    <div style="width:min(480px,100%);background:var(--card-bg,#fff);border:1px solid var(--border-color,#cbd5e1);border-radius:24px;padding:32px;text-align:center;box-shadow:0 20px 50px rgba(15,23,42,.16)">
      <div style="width:64px;height:64px;border-radius:18px;background:rgba(239,68,68,.12);color:#ef4444;display:grid;place-items:center;margin:0 auto 18px;font-size:30px">🔒</div>
      <h2 style="margin-bottom:8px">Akses Ditolak</h2>
      <p style="margin:0 0 22px">${message}</p>
      <button class="btn btn-primary" onclick="location.href='/dashboard'">Kembali ke Dashboard</button>
    </div>`;
  document.body.appendChild(overlay);
}

async function requirePagePermission(permissionKey, action = "view") {
  try {
    const allowed = await can(permissionKey, action);
    if (!allowed) renderAccessDenied();
    return allowed;
  } catch (error) {
    if (error?.message) renderAccessDenied(error.message);
    return false;
  }
}

function applyPermissionVisibility(root = document) {
  const state = getPermissionState();
  if (!state) return;
  root.querySelectorAll("[data-permission]").forEach((element) => {
    const key = element.dataset.permission;
    const action = element.dataset.permissionAction || "view";
    element.style.display = hasPermission(key, action, state) ? "" : "none";
  });
}

const PAGE_PERMISSIONS = {
  "/pos": ["sales.cashier", "view"],
  "/pos_2": ["sales.cashier", "view"],
  "/sales": ["sales.transaction", "view"],
  "/shifts": ["sales.cashier", "view"],
  "/returns": ["sales.return", "view"],
  "/delivery": ["sales.transaction", "view"],
  "/trade_in": ["sales.trade_in", "view"],
  "/po": ["purchase.order", "view"],
  "/setor": ["accounting.cash_in", "view"],
  "/setoran": ["accounting.cash_in", "view"],
  "/item/items": ["master.item", "view"],
  "/item/dashboard": ["master.item", "view"],
  "/item/kategori": ["master.type", "view"],
  "/item/merek": ["master.brand", "view"],
  "/item/satuan": ["master.unit", "view"],
  "/item/units": ["master.unit", "view"],
  "/purchases": ["purchase.transaction", "view"],
  "/purchase/purchases": ["purchase.transaction", "view"],
  "/purchase/catat-pembelian": ["purchase.transaction", "create"],
  "/catat-pembelian": ["purchase.transaction", "create"],
  "/purchase/detail_item": ["purchase.transaction", "view"],
  "/detail_item": ["purchase.transaction", "view"],
  "/inventory": ["inventory.item_in", "view"],
  "/warehouse": ["inventory.transfer", "view"],
  "/assembly": ["assembly.transaction", "view"],
  "/unit_conversion": ["master.unit", "view"],
  "/konsinyasi": ["inventory.item_in", "view"],
  "/reports": ["report.sales", "view"],
  "/accounting": ["accounting.journal", "view"],
  "/customers": ["master.customer", "view"],
  "/supplier/dashboard": ["master.supplier", "view"],
  "/supplier/tambahSuplier": ["master.supplier", "create"],
  "/discounts": ["master.discount_period", "view"],
  "/branches": ["master.warehouse", "view"],
  "/users": ["settings.user_management", "access"],
  "/barcode": ["master.barcode", "view"],
  "/settings": ["settings.general", "access"],
};

document.addEventListener("DOMContentLoaded", async () => {
  if (!getToken()) return;
  try {
    await loadMyPermissions();
    applyPermissionVisibility();
    const path = location.pathname.replace(/\.html$/, "").replace(/\/$/, "") || "/";
    const required = PAGE_PERMISSIONS[path];
    if (required) await requirePagePermission(required[0], required[1]);
  } catch {
    // Handler 401 global pada api() akan mengarahkan kembali ke halaman login.
  }
});

function initializeWorkspaceEmbedding() {
  if (!FPOS_WORKSPACE_EMBEDDED) return;

  document.body.classList.add("workspace-embedded-page");

  const sendLocation = () => {
    window.parent.postMessage(
      {
        type: "fpos-frame-location",
        url: location.href,
        title: document.title,
      },
      location.origin,
    );
  };

  const cleanNavigationPath = (url) => {
    try {
      let path = new URL(url, location.href).pathname.replace(/\.html$/, "");
      if (path.length > 1) path = path.replace(/\/$/, "");
      return path;
    } catch {
      return "";
    }
  };

  const inlineNavigationUrl = (element) => {
    if (!(element instanceof Element)) return "";
    const anchor = element.closest("a[href]");
    if (anchor) return anchor.getAttribute("href");
    const clickable = element.closest("[onclick]");
    const code = clickable?.getAttribute("onclick") || "";
    const match = code.match(
      /(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]/i,
    );
    return match?.[1] || "";
  };

  document.addEventListener(
    "click",
    (event) => {
      const targetUrl = inlineNavigationUrl(event.target);
      if (!targetUrl) return;
      const currentPath = cleanNavigationPath(location.href);
      const targetPath = cleanNavigationPath(targetUrl);

      if (targetPath === "/dashboard") {
        event.preventDefault();
        event.stopImmediatePropagation();
        focusWorkspaceDashboard();
        return;
      }

      if (currentPath === "/dashboard") {
        event.preventDefault();
        event.stopImmediatePropagation();
        openWorkspaceTab(targetUrl);
      }
    },
    true,
  );

  for (const method of ["pushState", "replaceState"]) {
    const original = history[method];
    history[method] = function (...args) {
      const result = original.apply(this, args);
      queueMicrotask(sendLocation);
      return result;
    };
  }
  window.addEventListener("popstate", sendLocation);

  const titleElement = document.querySelector("title");
  if (titleElement) {
    new MutationObserver(sendLocation).observe(titleElement, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  }
  sendLocation();
}

document.addEventListener("DOMContentLoaded", initializeWorkspaceEmbedding);

// Debounce: tunda eksekusi `fn` sampai `ms` ms berlalu tanpa panggilan baru.
// Dipakai untuk input pencarian agar tidak memicu request/render tiap ketukan huruf.
function debounce(fn, ms = 300) {
  let t;
  return function (...args) {
    clearTimeout(t);
    t = setTimeout(() => fn.apply(this, args), ms);
  };
}

function showToast(msg, type = "success") {
  document.getElementById("_t")?.remove();
  const c = {
    success: "#10b981",
    error: "#ef4444",
    info: "#3b82f6",
    warning: "#f59e0b",
  };
  const el = document.createElement("div");
  el.id = "_t";
  el.style.cssText = `position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:9999999;
    background:${c[type] || c.success};color:#fff;padding:14px 28px;border-radius:12px;
    font-size:15px;font-weight:700;box-shadow:0 8px 32px rgba(0,0,0,.35);
    max-width:420px;text-align:center;font-family:inherit;white-space:pre-line;line-height:1.5;`;
  el.textContent = String(msg);
  document.body.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity .3s";
    setTimeout(() => el.remove(), 300);
  }, 4000);
}

function showLoading(msg = "Memproses...") {
  let el = document.getElementById("_ld");
  if (!el) {
    el = document.createElement("div");
    el.id = "_ld";
    el.style.cssText =
      "position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:99998;display:flex;align-items:center;justify-content:center;";
    el.innerHTML = `<div style="background:var(--card-bg,#1e293b);border-radius:16px;padding:28px 36px;display:flex;align-items:center;gap:16px;box-shadow:0 16px 48px rgba(0,0,0,.5);">
      <div style="width:24px;height:24px;border:3px solid var(--primary,#f97316);border-top-color:transparent;border-radius:50%;animation:_sp .7s linear infinite;flex-shrink:0;"></div>
      <span id="_lm" style="color:var(--text-main,#fff);font-size:16px;font-weight:600;font-family:inherit;">${msg}</span>
    </div>`;
    document.body.appendChild(el);
    if (!document.getElementById("_spSt")) {
      const s = document.createElement("style");
      s.id = "_spSt";
      s.textContent = "@keyframes _sp{to{transform:rotate(360deg)}}";
      document.head.appendChild(s);
    }
  } else {
    document.getElementById("_lm").textContent = msg;
  }
}

function hideLoading() {
  document.getElementById("_ld")?.remove();
}

function showConfirm(msg, options = {}) {
  return new Promise((res) => {
    const {
      confirmText = "Ya, Lanjutkan",
      cancelText = "Batal",
      initialFocus = null,
    } = options;
    const el = document.createElement("div");
    el.style.cssText =
      "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99999;display:flex;align-items:center;justify-content:center;padding:20px;";
    el.innerHTML = `<div style="background:var(--card-bg,#1e293b);border-radius:20px;padding:32px;max-width:380px;width:100%;box-shadow:0 16px 48px rgba(0,0,0,.5);border:1px solid var(--border-color,#334155);">
      <p style="font-size:16px;color:var(--text-main,#f1f5f9);margin:0 0 24px;line-height:1.6;text-align:center;white-space:pre-line;">${msg}</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <button id="_cN" style="padding:13px;border-radius:10px;border:2px solid var(--border-color,#475569);background:transparent;color:var(--text-muted,#94a3b8);font-size:15px;font-weight:600;cursor:pointer;font-family:inherit;"></button>
        <button id="_cY" style="padding:13px;border-radius:10px;border:none;background:#ef4444;color:#fff;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;"></button>
      </div></div>`;
    document.body.appendChild(el);
    const confirmButton = el.querySelector("#_cY");
    const cancelButton = el.querySelector("#_cN");
    confirmButton.textContent = confirmText;
    cancelButton.textContent = cancelText;
    confirmButton.onclick = () => {
      el.remove();
      res(true);
    };
    cancelButton.onclick = () => {
      el.remove();
      res(false);
    };
    if (initialFocus === "confirm") {
      setTimeout(() => confirmButton.focus(), 0);
    } else if (initialFocus === "cancel") {
      setTimeout(() => cancelButton.focus(), 0);
    }
  });
}

function showUnsavedChangesDialog(msg, options = {}) {
  return new Promise((resolve) => {
    const {
      saveText = "Simpan & Keluar",
      discardText = "Keluar Tanpa Simpan",
      cancelText = "Batal",
    } = options;
    const overlay = document.createElement("div");
    overlay.style.cssText =
      "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100000;display:flex;align-items:center;justify-content:center;padding:20px;";
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.style.cssText =
      "background:var(--card-bg,#1e293b);border-radius:20px;padding:28px;max-width:480px;width:100%;box-shadow:0 16px 48px rgba(0,0,0,.5);border:1px solid var(--border-color,#334155);";

    const text = document.createElement("p");
    text.style.cssText =
      "font-size:16px;color:var(--text-main,#f1f5f9);margin:0 0 24px;line-height:1.6;text-align:center;white-space:pre-line;";
    text.textContent = msg;

    const actions = document.createElement("div");
    actions.style.cssText =
      "display:grid;grid-template-columns:1fr;gap:10px;";
    const makeButton = (label, styles, action) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.style.cssText =
        `padding:13px;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;${styles}`;
      button.onclick = () => finish(action);
      return button;
    };
    const saveButton = makeButton(
      saveText,
      "border:none;background:var(--primary,#2563eb);color:#fff;",
      "save",
    );
    const discardButton = makeButton(
      discardText,
      "border:none;background:#ef4444;color:#fff;",
      "discard",
    );
    const cancelButton = makeButton(
      cancelText,
      "border:2px solid var(--border-color,#475569);background:transparent;color:var(--text-main,#f1f5f9);",
      "cancel",
    );
    actions.append(saveButton, discardButton, cancelButton);
    dialog.append(text, actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    let finished = false;
    const finish = (action) => {
      if (finished) return;
      finished = true;
      document.removeEventListener("keydown", onKeyDown, true);
      overlay.remove();
      resolve(action);
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        finish("cancel");
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) finish("cancel");
    });
    setTimeout(() => cancelButton.focus(), 0);
  });
}

window.addEventListener("beforeunload", (event) => {
  if (fposSuppressNextUnloadWarning || !hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = "";
});

window.addEventListener("message", async (event) => {
  if (
    !FPOS_WORKSPACE_EMBEDDED ||
    event.origin !== location.origin ||
    event.source !== window.parent
  ) {
    return;
  }
  const message = event.data || {};
  if (!message.type?.startsWith("fpos-unsaved-")) return;

  const response = {
    type: "fpos-unsaved-response",
    requestId: message.requestId,
    action: message.type,
  };
  if (message.type === "fpos-unsaved-status-request") {
    response.dirty = hasUnsavedChanges();
    response.label = fposUnsavedChangesGuard?.label || document.title;
    response.success = true;
  } else if (message.type === "fpos-unsaved-save-request") {
    response.success = await saveUnsavedChanges();
    response.dirty = hasUnsavedChanges();
  } else if (message.type === "fpos-unsaved-discard-request") {
    await discardUnsavedChanges();
    response.success = true;
    response.dirty = false;
  } else {
    return;
  }
  window.parent.postMessage(response, location.origin);
});

function fmtRp(n) {
  if (n == null) return "Rp 0";
  return "Rp " + Math.round(n).toLocaleString("id-ID");
}

const PEMILIH_INPUT_DESIMAL = "input[data-input-desimal]";

function batasiBilangan(value, min, max) {
  let hasil = value;
  if (Number.isFinite(min) && hasil < min) hasil = min;
  if (Number.isFinite(max) && hasil > max) hasil = max;
  return hasil;
}

function opsiInputDesimal(target, overrides = {}) {
  const dataset = target?.dataset || {};
  const minimum = Number.parseInt(
    overrides.minimumFractionDigits ??
      overrides.minimum ??
      overrides.desimalMin ??
      dataset.desimalMin ??
      2,
    10,
  );
  const maksimum = Number.parseInt(
    overrides.maximumFractionDigits ??
      overrides.maksimum ??
      overrides.desimalMaks ??
      dataset.desimalMaks ??
      Math.max(minimum, 2),
    10,
  );
  const minRaw = overrides.min ?? dataset.min;
  const maxRaw = overrides.max ?? dataset.max;
  return {
    minimum: Math.max(0, Number.isFinite(minimum) ? minimum : 2),
    maksimum: Math.max(
      Math.max(0, Number.isFinite(minimum) ? minimum : 2),
      Number.isFinite(maksimum) ? maksimum : 2,
    ),
    min: minRaw === "" || minRaw == null ? NaN : Number(minRaw),
    max: maxRaw === "" || maxRaw == null ? NaN : Number(maxRaw),
    bolehNegatif:
      overrides.bolehNegatif === true ||
      dataset.bolehNegatif === "true" ||
      (minRaw != null && Number(minRaw) < 0),
  };
}

function tentukanPemisahDesimal(raw, maksimumDesimal = 2) {
  const komaTerakhir = raw.lastIndexOf(",");
  const titikTerakhir = raw.lastIndexOf(".");
  if (komaTerakhir >= 0 && titikTerakhir >= 0) {
    return Math.max(komaTerakhir, titikTerakhir);
  }

  const pemisah = komaTerakhir >= 0 ? "," : titikTerakhir >= 0 ? "." : "";
  if (!pemisah) return -1;
  const posisi = raw.lastIndexOf(pemisah);
  const jumlahPemisah = raw.split(pemisah).length - 1;
  const digitSesudah = raw.slice(posisi + 1).replace(/\D/g, "");
  const digitSebelum = raw.slice(0, posisi).replace(/\D/g, "") || "0";

  // Format Indonesia memakai titik untuk ribuan. Satu titik setelah angka bukan nol
  // dengan tiga digit di belakang tetap dibaca sebagai ribuan (contoh 1.000).
  // Bentuk 0.5/0.25 tetap dianggap desimal agar titik dan koma fleksibel.
  if (
    pemisah === "." &&
    jumlahPemisah === 1 &&
    digitSesudah.length === 3 &&
    Number(digitSebelum) !== 0 &&
    maksimumDesimal <= 2
  ) {
    return -1;
  }

  if (jumlahPemisah > 1) {
    const semuaGrupRibuan = raw
      .split(pemisah)
      .slice(1)
      .every((bagian) => bagian.replace(/\D/g, "").length === 3);
    if (semuaGrupRibuan) return -1;
  }
  return posisi;
}

function uraikanDesimal(value, options = {}) {
  const opts = opsiInputDesimal(null, options);
  if (typeof value === "number") {
    const aman = Number.isFinite(value) ? value : 0;
    const mutlak = Math.abs(aman).toFixed(opts.maksimum);
    const [bulat, desimal = ""] = mutlak.split(".");
    return {
      negatif: aman < 0 && opts.bolehNegatif,
      bulat: bulat.replace(/^0+(?=\d)/, "") || "0",
      desimal,
      adaPemisah: opts.minimum > 0,
    };
  }

  const raw = String(value ?? "").trim();
  const posisiPemisah = tentukanPemisahDesimal(raw, opts.maksimum);
  const bagianBulat =
    posisiPemisah >= 0 ? raw.slice(0, posisiPemisah) : raw;
  const bagianDesimal =
    posisiPemisah >= 0 ? raw.slice(posisiPemisah + 1) : "";
  return {
    negatif: opts.bolehNegatif && /^\s*-/.test(raw),
    bulat: bagianBulat.replace(/\D/g, "").replace(/^0+(?=\d)/, "") || "0",
    desimal: bagianDesimal.replace(/\D/g, "").slice(0, opts.maksimum),
    adaPemisah: posisiPemisah >= 0,
  };
}

function kelompokkanRibuan(digit) {
  return (digit || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

function parseDesimal(value, options = {}) {
  if (value instanceof HTMLInputElement) {
    options = opsiInputDesimal(value, options);
    value = value.value;
  }
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  const opts = opsiInputDesimal(null, options);
  const bagian = uraikanDesimal(value, opts);
  const desimal = bagian.desimal ? `.${bagian.desimal}` : "";
  const hasil = Number(`${bagian.negatif ? "-" : ""}${bagian.bulat}${desimal}`);
  return Number.isFinite(hasil) ? hasil : 0;
}

function toDesimal(value, options = {}) {
  const opts = opsiInputDesimal(null, options);
  let angka =
    typeof value === "number" ? value : parseDesimal(String(value ?? ""), opts);
  if (!Number.isFinite(angka)) angka = 0;
  angka = batasiBilangan(angka, opts.min, opts.max);

  const fixed = Math.abs(angka).toFixed(opts.maksimum);
  const [bagianBulat, desimalPenuh = ""] = fixed.split(".");
  let desimal = desimalPenuh.replace(/0+$/, "");
  if (desimal.length < opts.minimum) desimal = desimal.padEnd(opts.minimum, "0");
  const tanda = angka < 0 && opts.bolehNegatif ? "-" : "";
  return `${tanda}${kelompokkanRibuan(bagianBulat)}${desimal ? `,${desimal}` : ""}`;
}

function posisiSetelahJumlahDigit(text, jumlahDigit) {
  if (jumlahDigit <= 0) return text.startsWith("-") ? 1 : 0;
  let terlihat = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (/\d/.test(text[i])) terlihat += 1;
    if (terlihat >= jumlahDigit) return i + 1;
  }
  return text.length;
}

function formatDesimal(input, options = {}) {
  if (!input) return "0,00";
  const opts = opsiInputDesimal(input, options);
  const raw = String(input.value ?? "");
  const posisiKursor = input.selectionStart ?? raw.length;
  const akhirSeleksi = input.selectionEnd ?? posisiKursor;
  const seluruhNilaiDipilih =
    posisiKursor === 0 && akhirSeleksi === raw.length && raw.length > 0;
  const posisiPemisah = tentukanPemisahDesimal(raw, opts.maksimum);
  const kursorDiDesimal = posisiPemisah >= 0 && posisiKursor > posisiPemisah;
  const bagian = uraikanDesimal(raw, opts);
  const bulatTerformat = `${bagian.negatif ? "-" : ""}${kelompokkanRibuan(bagian.bulat)}`;
  const desimalTerformat = bagian.desimal
    .slice(0, opts.maksimum)
    .padEnd(opts.minimum, "0");
  const hasil = `${bulatTerformat}${desimalTerformat ? `,${desimalTerformat}` : ""}`;
  input.value = hasil;

  if (document.activeElement !== input) return hasil;
  if (seluruhNilaiDipilih) {
    try {
      input.setSelectionRange(0, hasil.length);
    } catch (_) {}
    return hasil;
  }
  let posisiBaru;
  if (kursorDiDesimal) {
    const jumlahDesimalSebelumKursor = raw
      .slice(posisiPemisah + 1, posisiKursor)
      .replace(/\D/g, "").length;
    posisiBaru =
      bulatTerformat.length +
      1 +
      Math.min(jumlahDesimalSebelumKursor, desimalTerformat.length);
  } else if (!raw.replace(/\D/g, "")) {
    posisiBaru = bulatTerformat.length;
  } else {
    const batasBulat = posisiPemisah >= 0 ? posisiPemisah : raw.length;
    const jumlahBulatSebelumKursor = raw
      .slice(0, Math.min(posisiKursor, batasBulat))
      .replace(/\D/g, "").length;
    posisiBaru = posisiSetelahJumlahDigit(
      bulatTerformat,
      jumlahBulatSebelumKursor,
    );
  }
  try {
    input.setSelectionRange(posisiBaru, posisiBaru);
  } catch (_) {}
  return hasil;
}

function validasiInputDesimal(input) {
  const opts = opsiInputDesimal(input);
  const sebelumDibatasi = parseDesimal(input, opts);
  const angka = batasiBilangan(sebelumDibatasi, opts.min, opts.max);
  input.value = toDesimal(angka, opts);
  input.setCustomValidity("");
  if (angka !== sebelumDibatasi) {
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  return angka;
}

function siapkanInputDesimal(input) {
  if (!input || input.dataset.inputDesimalSiap === "1") return;
  if (input.hasAttribute("min") && input.dataset.min == null) {
    input.dataset.min = input.getAttribute("min");
  }
  if (input.hasAttribute("max") && input.dataset.max == null) {
    input.dataset.max = input.getAttribute("max");
  }
  if (input.type === "number") input.type = "text";
  input.inputMode = "decimal";
  input.dataset.inputDesimalSiap = "1";
  formatDesimal(input);
}

function aktifkanInputDesimal(root = document) {
  if (root.matches?.(PEMILIH_INPUT_DESIMAL)) siapkanInputDesimal(root);
  root.querySelectorAll?.(PEMILIH_INPUT_DESIMAL).forEach(siapkanInputDesimal);
}

document.addEventListener("focusin", (event) => {
  const input = event.target.closest?.(PEMILIH_INPUT_DESIMAL);
  if (!input) return;
  siapkanInputDesimal(input);
  formatDesimal(input);
  if (parseDesimal(input) === 0) {
    const posisi = Math.max(0, input.value.indexOf(","));
    try {
      input.setSelectionRange(posisi, posisi);
      input.dataset.desimalBaruFokus = "1";
      setTimeout(() => delete input.dataset.desimalBaruFokus, 0);
    } catch (_) {}
  }
});

document.addEventListener("mouseup", (event) => {
  const input = event.target.closest?.(PEMILIH_INPUT_DESIMAL);
  if (!input || input.dataset.desimalBaruFokus !== "1") return;
  event.preventDefault();
  const posisi = Math.max(0, input.value.indexOf(","));
  try {
    input.setSelectionRange(posisi, posisi);
  } catch (_) {}
});

document.addEventListener("keydown", (event) => {
  const input = event.target.closest?.(PEMILIH_INPUT_DESIMAL);
  if (!input) return;
  if (
    event.key === "," ||
    event.key === "." ||
    event.key === "Decimal" ||
    event.code === "NumpadDecimal"
  ) {
    event.preventDefault();
    formatDesimal(input);
    const posisi = input.value.indexOf(",");
    try {
      input.setSelectionRange(posisi + 1, posisi + 1);
    } catch (_) {}
    return;
  }

  if (input.selectionStart !== input.selectionEnd) return;
  const posisiKoma = input.value.indexOf(",");
  const posisiKursor = input.selectionStart ?? 0;
  const menghapusKoma =
    (event.key === "Delete" && posisiKursor === posisiKoma) ||
    (event.key === "Backspace" && posisiKursor === posisiKoma + 1);
  if (menghapusKoma) event.preventDefault();
});

document.addEventListener("input", (event) => {
  const input = event.target.closest?.(PEMILIH_INPUT_DESIMAL);
  if (input && !event.isComposing) formatDesimal(input);
});

document.addEventListener("focusout", (event) => {
  const input = event.target.closest?.(PEMILIH_INPUT_DESIMAL);
  if (input) validasiInputDesimal(input);
});

document.addEventListener("DOMContentLoaded", () => aktifkanInputDesimal());

const pemantauInputDesimal = new MutationObserver((mutations) => {
  mutations.forEach((mutation) => {
    mutation.addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) aktifkanInputDesimal(node);
    });
  });
});
document.addEventListener("DOMContentLoaded", () => {
  if (document.body) {
    pemantauInputDesimal.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }
});

function fmtDate(d) {
  if (!d) return "-";
  try {
    return new Date(d + "T00:00:00").toLocaleDateString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return d;
  }
}

function today() {
  return new Date().toLocaleDateString("en-CA", {
    timeZone: "Asia/Makassar",
  });
}

const openModalOrder = [];
const modalFocusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[contenteditable='true']",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function isVisibleModalElement(el) {
  if (!el || !el.isConnected) return false;
  const style = window.getComputedStyle(el);
  return (
    style.display !== "none" &&
    style.visibility !== "hidden" &&
    el.getClientRects().length > 0
  );
}

function getModalFocusableElements(modal) {
  return Array.from(modal.querySelectorAll(modalFocusableSelector)).filter(
    (el) => {
      const style = window.getComputedStyle(el);
      return (
        !el.matches(":disabled") &&
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        el.getClientRects().length > 0
      );
    },
  );
}

function getPreferredModalControl(controls) {
  return controls.find((el) =>
    el.matches("[autofocus], input, select, textarea, [contenteditable='true']"),
  );
}

function getTopOpenModal() {
  for (let i = openModalOrder.length - 1; i >= 0; i--) {
    if (isVisibleModalElement(openModalOrder[i])) return openModalOrder[i];
  }

  // Fallback untuk modal lama yang ditampilkan langsung lewat style.display.
  const visibleModals = Array.from(
    document.querySelectorAll(".modal-overlay"),
  ).filter(isVisibleModalElement);
  return visibleModals[visibleModals.length - 1] || null;
}

function focusFirstModalControl(modal) {
  if (getTopOpenModal() !== modal) return;
  const controls = getModalFocusableElements(modal);
  const preferred = getPreferredModalControl(controls);
  const target = preferred || controls[0] || modal;
  if (target === modal && !modal.hasAttribute("tabindex")) {
    modal.setAttribute("tabindex", "-1");
  }
  target.focus({ preventScroll: true });
}

function syncModalScrollLock() {
  const hasOpenModal = Boolean(getTopOpenModal());
  document.body.style.overflow = hasOpenModal ? "hidden" : "";
  // Lock scroll for .main-content layout
  const main = document.querySelector(".main-content");
  if (main) main.style.overflow = hasOpenModal ? "hidden" : "auto";
}

function openModal(id) {
  const m = document.getElementById(id);
  if (m) {
    m.style.display = "flex";
    m.setAttribute("role", "dialog");
    m.setAttribute("aria-modal", "true");
    m.removeAttribute("aria-hidden");

    const oldIndex = openModalOrder.indexOf(m);
    if (oldIndex !== -1) openModalOrder.splice(oldIndex, 1);
    openModalOrder.push(m);

    syncModalScrollLock();
    // Fokuskan langsung agar penekanan Tab yang sangat cepat tidak sempat masuk
    // ke tombol tutup yang muncul lebih dulu dalam urutan DOM.
    focusFirstModalControl(m);
    // Ulangi setelah browser selesai menghitung layout untuk modal yang baru
    // disisipkan secara dinamis.
    setTimeout(() => focusFirstModalControl(m), 0);
  }
}

function closeModal(id) {
  const m = document.getElementById(id);
  if (m) {
    m.style.display = "none";
    m.removeAttribute("aria-modal");
    m.setAttribute("aria-hidden", "true");

    const oldIndex = openModalOrder.indexOf(m);
    if (oldIndex !== -1) openModalOrder.splice(oldIndex, 1);
    syncModalScrollLock();
  }
}

// Tahan navigasi Tab di dalam modal paling depan. Tanpa ini, fokus dari modal
// tambah cepat (mis. Merek/Satuan saat membuat barang) dapat masuk ke halaman
// atau form barang yang berada di belakang overlay.
document.addEventListener(
  "keydown",
  (event) => {
    if (
      event.key !== "Tab" ||
      event.defaultPrevented ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey
    ) {
      return;
    }

    const modal = getTopOpenModal();
    if (!modal) return;

    const controls = getModalFocusableElements(modal);
    if (!controls.length) {
      event.preventDefault();
      focusFirstModalControl(modal);
      return;
    }

    const first = controls[0];
    const last = controls[controls.length - 1];
    const active = document.activeElement;

    if (!modal.contains(active)) {
      event.preventDefault();
      const entryTarget = event.shiftKey
        ? last
        : getPreferredModalControl(controls) || first;
      entryTarget.focus({ preventScroll: true });
    } else if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus({ preventScroll: true });
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus({ preventScroll: true });
    }
  },
  true,
);

// Shared CSS
(function () {
  const s = document.createElement("style");
  s.textContent = `
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);backdrop-filter:blur(4px);align-items:center;justify-content:center;z-index:9000;padding:20px;}
.modal-box{background:var(--card-bg);width:100%;max-width:540px;border-radius:var(--radius-lg,24px);padding:32px;box-shadow:0 20px 60px rgba(0,0,0,.5);max-height:90vh;overflow-y:auto;border:1px solid var(--border-color);}
.modal-box.wide{max-width:780px;}
.modal-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;}
.modal-hdr h2{margin:0;font-size:20px;color:var(--text-main);}
.btn-x{background:none;border:none;font-size:28px;cursor:pointer;color:var(--text-muted);padding:0;line-height:1;}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
.row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;}
@media(max-width:580px){.row2,.row3{grid-template-columns:1fr;}}
.tbl{width:100%;border-collapse:collapse;}
.tbl th{background:var(--bg-color);color:var(--text-muted);padding:12px 16px;text-align:left;font-size:13px;font-weight:600;border-bottom:2px solid var(--border-color);}
.tbl td{padding:13px 16px;border-bottom:1px solid var(--border-color);vertical-align:middle;font-size:15px;color:var(--text-main);}
.tbl tr:last-child td{border-bottom:none;}
.tbl tr:hover td{background:rgba(249,115,22,.04);}
.tbl-wrap{overflow-x:auto;}
.bl{background:rgba(16,185,129,.15);color:#10b981;padding:4px 12px;border-radius:8px;font-size:13px;font-weight:700;}
.br{background:rgba(239,68,68,.15);color:#ef4444;padding:4px 12px;border-radius:8px;font-size:13px;font-weight:700;}
.bo{background:rgba(249,115,22,.15);color:#f97316;padding:4px 12px;border-radius:8px;font-size:13px;font-weight:700;}
.bg{background:rgba(148,163,184,.15);color:#94a3b8;padding:4px 12px;border-radius:8px;font-size:13px;font-weight:700;}
.bi{background:rgba(99,102,241,.15);color:#818cf8;padding:4px 12px;border-radius:8px;font-size:13px;font-weight:700;}
.bsm{padding:7px 14px;font-size:13px;font-weight:700;border-radius:8px;border:none;cursor:pointer;font-family:inherit;margin-left:4px;}
.be{background:rgba(59,130,246,.15);color:#3b82f6;}
.bd{background:rgba(239,68,68,.15);color:#ef4444;}
.bp{background:rgba(16,185,129,.15);color:#10b981;}
.bv{background:rgba(249,115,22,.15);color:#f97316;}
.pg-header{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;margin-bottom:24px;}
.pg-header h1{margin:0;}
.filter-bar{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px;background:var(--card-bg);padding:14px;border-radius:14px;border:1px solid var(--border-color);}
.filter-bar .input-control{margin:0;flex:1;min-width:130px;}
.btn-danger{background:#ef4444;color:#fff;padding:12px 24px;border:none;border-radius:12px;font-size:15px;font-weight:600;cursor:pointer;font-family:inherit;}
`;
  document.head.appendChild(s);
})();

// ==============================================================================
// ─── SISTEM KEAMANAN & PENAGIHAN (KILL SWITCH DEVELOPER) ──────────────────────
// ==============================================================================

async function checkBillingStatus() {
  // --- FITUR IMUNITAS FIETER ---
  const user = getUser();
  if (user && user.username && user.username.toLowerCase() === "fieter") {
    document.getElementById("billingWarning")?.remove();
    document.getElementById("lockScreenOverlay")?.remove();
    document.body.style.overflow = "";
    return;
  }

  if (!getToken() && window.location.pathname === "/") return;

  try {
    const resp = await api("GET", "/license/status?_t=" + new Date().getTime());

    if (resp.billing_status === "warning") {
      tampilkanBannerWarning(resp.billing_message);
    } else if (resp.billing_status === "blocked") {
      kunciTotalAplikasi(resp.billing_message);
    } else {
      document.getElementById("billingWarning")?.remove();
      if (document.getElementById("lockScreenOverlay")) {
        window.location.reload();
      }
    }
  } catch (e) {
    console.log("Mengecek lisensi server...");
  }
}

function tampilkanBannerWarning(pesan) {
  if (document.getElementById("billingWarning")) {
    document.getElementById("billingWarningText").textContent = "⚠️ " + pesan;
    return;
  }

  const banner = document.createElement("div");
  banner.id = "billingWarning";
  banner.innerHTML = `
      <div style="background:#ef4444; color:white; padding:12px; text-align:center; font-weight:bold; position:fixed; top:0; left:0; width:100%; z-index:999999; animation: blink 2s infinite; box-shadow: 0 4px 6px rgba(0,0,0,0.2);">
          <span id="billingWarningText">⚠️ ${pesan}</span>
          <button onclick="bukaModalUploadBukti()" style="margin-left:20px; padding:6px 14px; cursor:pointer; background:white; color:#ef4444; border:none; border-radius:6px; font-weight:900; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
              📎 Upload Bukti Transfer
          </button>
      </div>
      <style>@keyframes blink { 50% { background:#dc2626; } } body { padding-top: 50px !important; }</style>
  `;
  document.body.prepend(banner);
}

function kunciTotalAplikasi(pesan) {
  if (document.getElementById("lockScreenOverlay")) return;

  const lock = document.createElement("div");
  lock.id = "lockScreenOverlay";
  lock.style.cssText =
    "position:fixed; inset:0; background:#0f172a; z-index:9999999; display:flex; flex-direction:column; justify-content:center; align-items:center; color:white; font-family:inherit; text-align:center; padding:20px;";
  lock.innerHTML = `
      <div style="font-size:70px; margin-bottom:20px;">⛔</div>
      <h1 style="color:#ef4444; margin-bottom:15px; font-size:32px;">APLIKASI DINONAKTIFKAN</h1>
      <p style="font-size:18px; color:#cbd5e1; max-width:600px; line-height:1.6; margin-bottom:40px;">
          ${pesan}<br><br>
          Akses ke sistem kasir telah ditangguhkan sementara waktu. Silakan selesaikan pembayaran Anda dan upload bukti transfer untuk membuka kembali akses aplikasi.
      </p>
      <button onclick="bukaModalUploadBukti()" style="padding:16px 32px; font-size:18px; font-weight:bold; background:#10b981; color:white; border:none; border-radius:12px; cursor:pointer; box-shadow: 0 8px 16px rgba(16, 185, 129, 0.2);">
          📎 Upload Bukti Transfer Sekarang
      </button>
  `;
  document.body.appendChild(lock);
  document.body.style.overflow = "hidden";
}

function bukaModalUploadBukti() {
  const el = document.createElement("div");
  el.id = "modalUploadBukti";
  el.style.cssText =
    "position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:99999999;display:flex;align-items:center;justify-content:center;padding:20px;";
  el.innerHTML = `
  <div style="background:var(--card-bg,#1e293b);border-radius:20px;padding:32px;max-width:450px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.5);border:1px solid var(--border-color,#334155);">
      <h2 style="color:#fff; margin-top:0; margin-bottom:15px;">📤 Upload Bukti Pembayaran</h2>
      <p style="color:#94a3b8; font-size:14px; margin-bottom:20px; line-height:1.5;">
          Silakan upload foto struk transfer atau screenshot M-Banking Anda (JPG/PNG).
      </p>
      <input type="file" id="fileBukti" accept="image/*" style="width:100%; padding:12px; background:#0f172a; color:#fff; border:1px solid #334155; border-radius:10px; margin-bottom:25px;">

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <button onclick="document.getElementById('modalUploadBukti').remove()" style="padding:14px;border-radius:10px;border:2px solid #475569;background:transparent;color:#94a3b8;font-size:15px;font-weight:600;cursor:pointer;">Batal</button>
          <button id="btnKirimBukti" style="padding:14px;border-radius:10px;border:none;background:#10b981;color:#fff;font-size:15px;font-weight:700;cursor:pointer;">Kirim Bukti</button>
      </div>
  </div>`;
  document.body.appendChild(el);

  document.getElementById("btnKirimBukti").onclick = async () => {
    const fileInput = document.getElementById("fileBukti");
    const file = fileInput.files[0];

    if (!file) {
      showToast("Harap pilih gambar terlebih dahulu!", "error");
      return;
    }

    const btn = document.getElementById("btnKirimBukti");
    btn.disabled = true;
    btn.textContent = "Mengirim...";

    try {
      const fd = new FormData();
      fd.append("file", file);

      await apiForm("/license/upload-proof", fd);

      showToast(
        "Bukti berhasil dikirim! Menunggu verifikasi Developer.",
        "success",
      );
      el.remove();

      if (document.getElementById("lockScreenOverlay")) {
        document.getElementById("lockScreenOverlay").innerHTML +=
          `<p style="color:#10b981; margin-top:20px; font-weight:bold; font-size:16px;">✅ Bukti sedang ditinjau. Aplikasi akan terbuka otomatis jika sudah disetujui.</p>`;
      }
    } catch (err) {
      showToast(err.message, "error");
      btn.disabled = false;
      btn.textContent = "Kirim Bukti";
    }
  };
}

// ==============================================================================
// ─── FITUR MULTI-BRANCH SWITCHER UI ───────────────────────────────────────────
// ==============================================================================
const GLOBAL_BRANCH_SWITCHER_ID = "globalBranchSwitcher";
const GLOBAL_BRANCH_SELECT_ID = "globalBranchSelect";

async function handleGlobalBranchChange(event) {
  const select = event.currentTarget;
  localStorage.setItem("active_branch_id", select.value);

  // Refresh data user agar branch_status terbaru ikut tersimpan sebelum reload.
  try {
    const updatedUser = await api("GET", "/auth/me");
    localStorage.setItem("ipos_user", JSON.stringify(updatedUser));
  } catch (e) {
    console.error("Gagal sinkron status cabang:", e);
  }

  window.location.reload();
}

function renderBranchOptions(select, branches, activeId) {
  const options = branches.map((branch) => {
    const option = document.createElement("option");
    option.value = String(branch.id);
    option.textContent = `📍 ${branch.name}`;
    option.selected = String(branch.id) === String(activeId);
    return option;
  });
  select.replaceChildren(...options);
}

async function refreshBranchSwitcher({ force = false } = {}) {
  if (FPOS_WORKSPACE_EMBEDDED) return;
  const user = getUser();
  if (!user || !user.id) return; // Abaikan jika belum login

  // Jika Kasir/Staff biasa, paksa ID Cabangnya sendiri dan tampilkan badge statis
  if (!user.role.includes("admin")) {
    localStorage.setItem("active_branch_id", user.branch_id || 1);
    if (document.getElementById(GLOBAL_BRANCH_SWITCHER_ID)) return;
    const badge = document.createElement("div");
    badge.id = GLOBAL_BRANCH_SWITCHER_ID;
    badge.style.cssText =
      "position:fixed; bottom:20px; left:20px; z-index:9999; background:var(--card-bg, #1e293b); padding:8px 16px; border-radius:12px; border:1px solid var(--border-color); box-shadow:0 10px 25px rgba(0,0,0,0.3); color:var(--text-muted); font-size:13px; font-weight:bold;";
    badge.innerHTML = "📍 Kasir Cabang";
    document.body.appendChild(badge);
    return;
  }

  // Jika Admin, load daftar cabang dan buat Dropdown
  try {
    // Cabang jarang berubah → cache per-sesi agar tiap pindah halaman tidak fetch ulang.
    // Setelah master cabang berubah, force membuang cache sebelum mengambil data terbaru.
    if (force) invalidateCache("/branches/");
    const branches = await cachedApi("/branches/");
    // 401/redirect atau respons tak terduga → hentikan diam-diam tanpa lempar error
    if (!Array.isArray(branches)) return;
    let activeId = localStorage.getItem("active_branch_id");

    if (!activeId && branches.length > 0) {
      activeId = branches[0].id;
      localStorage.setItem("active_branch_id", activeId);
    }

    // 🔥 SYNC: Jika ID cabang di localStorage beda dengan data user, refresh agar branch_status sinkron
    if (activeId && activeId != user.active_branch_id) {
      try {
        const updatedUser = await api("GET", "/auth/me");
        localStorage.setItem("ipos_user", JSON.stringify(updatedUser));
        Object.assign(user, updatedUser); // Update objek user lokal
      } catch (e) {
        console.warn("Gagal sinkron data user di awal:", e);
      }
    }

    let switcher = document.getElementById(GLOBAL_BRANCH_SWITCHER_ID);
    let select = document.getElementById(GLOBAL_BRANCH_SELECT_ID);
    if (!switcher || !select) {
      switcher?.remove();
      switcher = document.createElement("div");
      switcher.id = GLOBAL_BRANCH_SWITCHER_ID;
      switcher.style.cssText =
        "position:fixed; bottom:20px; left:20px; z-index:9999; background:var(--card-bg, #1e293b); padding:8px 12px; border-radius:12px; border:2px solid var(--primary); box-shadow:0 10px 25px rgba(0,0,0,0.5); display:flex; align-items:center; gap:10px; transition:0.3s;";

      const label = document.createElement("span");
      label.style.cssText =
        "font-size:11px; color:var(--text-muted); font-weight:bold; letter-spacing:0.5px;";
      label.textContent = "ZONA KERJA:";

      select = document.createElement("select");
      select.id = GLOBAL_BRANCH_SELECT_ID;
      select.style.cssText =
        "background:transparent; color:var(--primary); font-weight:900; border:none; outline:none; cursor:pointer; font-size:14px; width:auto; min-width:150px; max-width:min(24rem, calc(100vw - 9rem));";
      select.addEventListener("change", handleGlobalBranchChange);

      switcher.append(label, select);
      document.body.appendChild(switcher);
    }

    renderBranchOptions(select, branches, activeId);
    return true;
  } catch (e) {
    console.error("Gagal meload cabang untuk switcher", e);
    return false;
  }
}

async function notifyBranchListChanged() {
  invalidateCache("/branches/");
  if (FPOS_WORKSPACE_EMBEDDED) {
    window.parent.postMessage(
      { type: "fpos-branches-changed" },
      location.origin,
    );
    return true;
  }
  return refreshBranchSwitcher({ force: true });
}

async function initBranchSwitcher() {
  return refreshBranchSwitcher();
}

// ── INIT GLOBAL ──
document.addEventListener("DOMContentLoaded", () => {
  if (FPOS_WORKSPACE_EMBEDDED) return;
  checkBillingStatus();
  initBranchSwitcher(); // 🔥 Panggil UI Cabang otomatis di semua halaman

  // Cek ulang setiap 60 detik
  setInterval(checkBillingStatus, 60000);
});
