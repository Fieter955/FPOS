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
  // Bersihkan juga sesi cabang saat logout
  localStorage.removeItem("active_branch_id");
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
  const activeBranchId = localStorage.getItem("active_branch_id") || user.active_branch_id;
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
  return new Date().toLocaleDateString("en-CA", {
    timeZone: "Asia/Makassar",
  });
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
    const branches = await api("GET", "/branches/");
    let activeId = localStorage.getItem("active_branch_id");

    if (!activeId && branches.length > 0) {
      activeId = branches[0].id;
      localStorage.setItem("active_branch_id", activeId);
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
      .addEventListener("change", function () {
        localStorage.setItem("active_branch_id", this.value);
        window.location.reload();
      });
  } catch (e) {
    console.error("Gagal meload cabang untuk switcher", e);
  }
}

// ── INIT GLOBAL ──
document.addEventListener("DOMContentLoaded", () => {
  checkBillingStatus();
  initBranchSwitcher(); // 🔥 Panggil UI Cabang otomatis di semua halaman

  // Cek ulang setiap 60 detik
  setInterval(checkBillingStatus, 60000);
});
