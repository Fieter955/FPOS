// One-off setup: unduh dependency vendor agar di-host lokal (lepas dari CDN).
// - Chart.js  -> frontend/js/vendor/chart.umd.min.js
// - Poppins   -> frontend/css/fonts/poppins-<weight>.woff2 (+ rules @font-face)
// Jalankan: node fetch-vendor.mjs
import { writeFile, mkdir } from "node:fs/promises";
import path from "node:path";

const ROOT = path.resolve("frontend");
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36";

async function dl(url) {
  const r = await fetch(url, { headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
  return r;
}

// 1) Chart.js (UMD) — hanya dipakai di reports.html
await mkdir(path.join(ROOT, "js", "vendor"), { recursive: true });
const chartJs = await (
  await dl("https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js")
).text();
await writeFile(path.join(ROOT, "js", "vendor", "chart.umd.min.js"), chartJs);
console.log("OK chart.umd.min.js", chartJs.length, "bytes");

// 2) Poppins (subset latin) untuk semua weight yang dipakai style.css
await mkdir(path.join(ROOT, "css", "fonts"), { recursive: true });
const css = await (
  await dl(
    "https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap"
  )
).text();

const re = /\/\*\s*([\w-]+)\s*\*\/\s*@font-face\s*{([^}]*)}/g;
const faces = [];
let m;
while ((m = re.exec(css))) {
  const subset = m[1];
  const body = m[2];
  if (subset !== "latin") continue; // cukup latin untuk teks Indonesia
  const weight = (body.match(/font-weight:\s*(\d+)/) || [])[1];
  const urlM = body.match(/url\((https:[^)]+\.woff2)\)/);
  const range = (body.match(/unicode-range:\s*([^;]+);/) || [])[1] || "";
  if (!weight || !urlM) continue;
  const fname = `poppins-${weight}.woff2`;
  const buf = Buffer.from(await (await dl(urlM[1])).arrayBuffer());
  await writeFile(path.join(ROOT, "css", "fonts", fname), buf);
  faces.push(
    `@font-face{font-family:'Poppins';font-style:normal;font-weight:${weight};font-display:swap;src:url('/css/fonts/${fname}') format('woff2');${
      range ? `unicode-range:${range};` : ""
    }}`
  );
  console.log("OK", fname, buf.length, "bytes");
}

// Tulis rules ke file sementara supaya bisa di-inline ke style.css
await writeFile(path.join(ROOT, "css", "fonts", "_poppins.css"), faces.join("\n") + "\n");
console.log("\n--- @font-face rules ---\n" + faces.join("\n"));
