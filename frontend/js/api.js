const API_BASE = "/api";
function getToken() {
  return localStorage.getItem("ipos_token");
}
function setToken(t) {
  localStorage.setItem("ipos_token", t);
}
function clearToken() {
  localStorage.removeItem("ipos_token");
  localStorage.removeItem("ipos_user");
}
function getUser() {
  try {
    return JSON.parse(localStorage.getItem("ipos_user") || "{}");
  } catch {
    return {};
  }
}

async function api(method, path, body = null) {
  const h = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) h["Authorization"] = `Bearer ${tok}`;
  const o = { method, headers: h };
  if (body) o.body = JSON.stringify(body);
  let r;
  try {
    r = await fetch(API_BASE + path, o);
  } catch (e) {
    throw new Error("Tidak bisa terhubung ke server.");
  }
  if (r.status === 401) {
    clearToken();
    window.location.href = "/";
    return;
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
  if (tok) h["Authorization"] = `Bearer ${tok}`;
  let r;
  try {
    r = await fetch(API_BASE + path, { method: "POST", headers: h, body: fd });
  } catch (e) {
    throw new Error("Tidak bisa terhubung.");
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

function requireAuth() {
  if (!getToken()) window.location.href = "/";
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
  el.style.cssText = `position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:99999;
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

function showConfirm(msg) {
  return new Promise((res) => {
    const el = document.createElement("div");
    el.style.cssText =
      "position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:99999;display:flex;align-items:center;justify-content:center;padding:20px;";
    el.innerHTML = `<div style="background:var(--card-bg,#1e293b);border-radius:20px;padding:32px;max-width:380px;width:100%;box-shadow:0 16px 48px rgba(0,0,0,.5);border:1px solid var(--border-color,#334155);">
      <p style="font-size:16px;color:var(--text-main,#f1f5f9);margin:0 0 24px;line-height:1.6;text-align:center;white-space:pre-line;">${msg}</p>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <button id="_cN" style="padding:13px;border-radius:10px;border:2px solid var(--border-color,#475569);background:transparent;color:var(--text-muted,#94a3b8);font-size:15px;font-weight:600;cursor:pointer;font-family:inherit;">Batal</button>
        <button id="_cY" style="padding:13px;border-radius:10px;border:none;background:#ef4444;color:#fff;font-size:15px;font-weight:700;cursor:pointer;font-family:inherit;">Ya, Lanjutkan</button>
      </div></div>`;
    document.body.appendChild(el);
    el.querySelector("#_cY").onclick = () => {
      el.remove();
      res(true);
    };
    el.querySelector("#_cN").onclick = () => {
      el.remove();
      res(false);
    };
  });
}

function fmtRp(n) {
  if (n == null) return "Rp 0";
  return "Rp " + Math.round(n).toLocaleString("id-ID");
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
  return new Date().toISOString().slice(0, 10);
}
function openModal(id) {
  const m = document.getElementById(id);
  if (m) {
    m.style.display = "flex";
    document.body.style.overflow = "hidden";
  }
}
function closeModal(id) {
  const m = document.getElementById(id);
  if (m) {
    m.style.display = "none";
    document.body.style.overflow = "";
  }
}

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
