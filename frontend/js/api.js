const API_BASE = "/api";
const SESSION_EXPIRED_NOTICE_KEY = "fpos_session_expired_notice";

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

async function logoutCurrentUser() {
  const confirmed =
    typeof showConfirm === "function"
      ? await showConfirm("Yakin ingin keluar dari akun saat ini?")
      : window.confirm("Yakin ingin keluar dari akun saat ini?");
  if (!confirmed) return;

  clearToken();
  redirectToLogin();
}

function renderGlobalLogoutButton() {
  const path = location.pathname.replace(/\.html$/, "").replace(/\/$/, "") || "/";
  const loginPages = new Set(["/", "/index", "/login"]);
  if (
    !getToken() ||
    loginPages.has(path) ||
    document.getElementById("globalLogoutButton")
  ) {
    return;
  }

  const button = document.createElement("button");
  button.id = "globalLogoutButton";
  button.type = "button";
  button.className = "global-logout-button";
  button.title = "Keluar dari akun";
  button.setAttribute("aria-label", "Keluar dari akun");
  button.innerHTML =
    '<span aria-hidden="true">🚪</span><span class="global-logout-label">Keluar</span>';
  button.addEventListener("click", logoutCurrentUser);
  document.body.appendChild(button);
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
  // Logout adalah kontrol sesi dasar untuk semua akun, bukan hak akses admin.
  if (!FPOS_WORKSPACE_EMBEDDED) renderGlobalLogoutButton();
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

function fmtRp(n) {
  if (n == null) return "Rp 0";
  return "Rp " + Math.round(n).toLocaleString("id-ID");
}

function formatDesimal(el) {
  if (!el) return;
  const start = el.selectionStart;
  const oldLen = el.value.length;

  let parts = el.value.split(',');
  let integerPart = parts[0].replace(/[^0-9-]/g, "");
  let angka = parseInt(integerPart) || 0;
  
  // Format bagian bulat dengan pemisah ribuan
  let formattedInteger = integerPart ? new Intl.NumberFormat("id-ID").format(angka) : "0";
  
  // Format bagian desimal (jika ada koma)
  if (parts.length > 1) {
    let decimalPart = parts[1].replace(/[^0-9]/g, "").substring(0, 2);
    el.value = formattedInteger + "," + decimalPart;
  } else {
    el.value = formattedInteger;
  }

  if (document.activeElement === el && el.value) {
    const newLen = el.value.length;
    let newPos = start + (newLen - oldLen);
    if (newPos < 0) newPos = 0;
    try { el.setSelectionRange(newPos, newPos); } catch (e) {}
  }
}

function parseDesimal(str) {
  if (typeof str === "number") return str;
  if (!str) return 0;
  let parts = str.toString().split(',');
  let integerPart = parts[0].replace(/[^0-9-]/g, "");
  if (!integerPart) integerPart = "0";
  let decimalPart = parts.length > 1 ? parts[1].replace(/[^0-9]/g, "").substring(0, 2) : "00";
  let floatStr = integerPart + "." + decimalPart;
  return parseFloat(floatStr) || 0;
}

function toDesimal(num) {
  if (num === null || num === undefined || num === "") return "0,00";
  let floatNum = parseFloat(num);
  if (isNaN(floatNum)) return "0,00";
  
  let parts = floatNum.toFixed(2).split('.');
  let integerPart = parseInt(parts[0]) || 0;
  let formattedInteger = new Intl.NumberFormat("id-ID").format(integerPart);
  return formattedInteger + "," + parts[1];
}

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
async function initBranchSwitcher() {
  if (FPOS_WORKSPACE_EMBEDDED) return;
  const user = getUser();
  if (!user || !user.id) return; // Abaikan jika belum login

  // Jika Kasir/Staff biasa, paksa ID Cabangnya sendiri dan tampilkan badge statis
  if (!user.role.includes("admin")) {
    localStorage.setItem("active_branch_id", user.branch_id || 1);
    const badge = document.createElement("div");
    badge.style.cssText =
      "position:fixed; bottom:20px; left:20px; z-index:9999; background:var(--card-bg, #1e293b); padding:8px 16px; border-radius:12px; border:1px solid var(--border-color); box-shadow:0 10px 25px rgba(0,0,0,0.3); color:var(--text-muted); font-size:13px; font-weight:bold;";
    badge.innerHTML = "📍 Kasir Cabang";
    document.body.appendChild(badge);
    return;
  }

  // Jika Admin, load daftar cabang dan buat Dropdown
  try {
    // Cabang jarang berubah → cache per-sesi agar tiap pindah halaman tidak fetch ulang.
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

    const switcher = document.createElement("div");
    switcher.style.cssText =
      "position:fixed; bottom:20px; left:20px; z-index:9999; background:var(--card-bg, #1e293b); padding:8px 12px; border-radius:12px; border:2px solid var(--primary); box-shadow:0 10px 25px rgba(0,0,0,0.5); display:flex; align-items:center; gap:10px; transition:0.3s;";

    let options = branches
      .map(
        (b) =>
          `<option value="${b.id}" ${b.id == activeId ? "selected" : ""}>📍 ${b.name}</option>`,
      )
      .join("");

    switcher.innerHTML = `
      <span style="font-size:11px; color:var(--text-muted); font-weight:bold; letter-spacing:0.5px;">ZONA KERJA:</span>
      <select id="globalBranchSelect" style="background:transparent; color:var(--primary); font-weight:900; border:none; outline:none; cursor:pointer; font-size:14px; width:150px; text-overflow:ellipsis;">
          ${options}
      </select>
    `;
    document.body.appendChild(switcher);

    // Saat admin mengganti cabang, simpan dan refresh halaman!
    document
      .getElementById("globalBranchSelect")
      .addEventListener("change", async function () {
        localStorage.setItem("active_branch_id", this.value);

        // 🔥 REVISI: Refresh data user agar branch_status terbaru ikut tersimpan sebelum reload
        try {
          const updatedUser = await api("GET", "/auth/me");
          localStorage.setItem("ipos_user", JSON.stringify(updatedUser));
        } catch (e) {
          console.error("Gagal sinkron status cabang:", e);
        }

        window.location.reload();
      });
  } catch (e) {
    console.error("Gagal meload cabang untuk switcher", e);
  }
}

// ── INIT GLOBAL ──
document.addEventListener("DOMContentLoaded", () => {
  if (FPOS_WORKSPACE_EMBEDDED) return;
  checkBillingStatus();
  initBranchSwitcher(); // 🔥 Panggil UI Cabang otomatis di semua halaman

  // Cek ulang setiap 60 detik
  setInterval(checkBillingStatus, 60000);
});
