// Build pipeline ringan untuk frontend FPOS (tetap MPA vanilla, tanpa SPA).
//   - JS/CSS  : minify + content-hash ('[name]-[hash]') via esbuild (bundle:false → file shared
//               tetap terpisah & reusable antar-halaman, url()/@import dibiarkan).
//   - HTML    : minify (termasuk JS/CSS inline) via html-minifier-terser, lalu referensi
//               /js/*.js & /css/*.css ditulis-ulang ke nama ber-hash dari manifest.
//   - Aset lain (font woff2, icons, manifest.json, sw) disalin apa adanya.
// Sumber: frontend/  ->  Output: frontend-dist/
// Pakai: `npm run build`  (atau `npm run watch` untuk rebuild saat file berubah)
import { rm, mkdir, readdir, readFile, writeFile, copyFile } from "node:fs/promises";
import { watch } from "node:fs";
import path from "node:path";
import * as esbuild from "esbuild";
import { minify } from "html-minifier-terser";

const ROOT = path.resolve(".");
const SRC = path.join(ROOT, "frontend");
const OUT = path.join(ROOT, "frontend-dist");

const toPosix = (p) => p.split(path.sep).join("/");

async function listFiles(dir) {
  const ents = await readdir(dir, { recursive: true, withFileTypes: true });
  return ents.filter((e) => e.isFile()).map((e) => path.join(e.parentPath, e.name));
}

function addManifest(metafile, manifest) {
  for (const [outPath, meta] of Object.entries(metafile.outputs)) {
    if (!meta.entryPoint) continue;
    const from = "/" + toPosix(path.relative(SRC, path.resolve(meta.entryPoint)));
    const to = "/" + toPosix(path.relative(OUT, path.resolve(outPath)));
    manifest[from] = to;
  }
}

async function bundleAssets(entryPoints, sub, manifest) {
  if (!entryPoints.length) return;
  const r = await esbuild.build({
    entryPoints,
    outdir: path.join(OUT, sub),
    outbase: path.join(SRC, sub),
    entryNames: "[dir]/[name]-[hash]",
    bundle: false,
    minify: true,
    legalComments: "none",
    metafile: true,
    logLevel: "silent",
  });
  addManifest(r.metafile, manifest);
}

const HTML_MIN_OPTS = {
  collapseWhitespace: true,
  conservativeCollapse: true, // sisakan 1 spasi → aman untuk layout inline-element
  removeComments: true,
  minifyCSS: true,
  minifyJS: true,
  keepClosingSlash: true,
  caseSensitive: true,
};

async function build() {
  const t0 = Date.now();
  await rm(OUT, { recursive: true, force: true });
  await mkdir(OUT, { recursive: true });

  const allFiles = await listFiles(SRC);
  const rel = (f) => toPosix(path.relative(SRC, f));
  const isJs = (f) => rel(f).startsWith("js/") && f.endsWith(".js");
  const isCss = (f) => rel(f).startsWith("css/") && f.endsWith(".css");

  const manifest = {};
  await bundleAssets(allFiles.filter(isJs), "js", manifest);
  await bundleAssets(allFiles.filter(isCss), "css", manifest);

  // Ganti key terpanjang dulu agar tidak ada tumpang-tindih substring.
  const keys = Object.keys(manifest).sort((a, b) => b.length - a.length);

  let nHtml = 0,
    nCopy = 0,
    nFail = 0;
  for (const f of allFiles) {
    if (isJs(f) || isCss(f)) continue; // sudah ditangani esbuild
    const r = rel(f);
    const dest = path.join(OUT, r);
    await mkdir(path.dirname(dest), { recursive: true });

    if (f.toLowerCase().endsWith(".html")) {
      let html = await readFile(f, "utf8");
      try {
        html = await minify(html, HTML_MIN_OPTS);
      } catch (e) {
        nFail++;
        console.warn(`  ! minify gagal (pakai raw): ${r} — ${e.message}`);
      }
      for (const k of keys) html = html.split(k).join(manifest[k]);
      await writeFile(dest, html);
      nHtml++;
    } else {
      await copyFile(f, dest);
      nCopy++;
    }
  }

  await writeFile(
    path.join(ROOT, "build-manifest.json"),
    JSON.stringify(manifest, null, 2)
  );

  const njs = Object.keys(manifest).filter((k) => k.startsWith("/js/")).length;
  const ncss = Object.keys(manifest).filter((k) => k.startsWith("/css/")).length;
  console.log(
    `✓ build ${Date.now() - t0}ms — ${njs} js, ${ncss} css, ${nHtml} html, ${nCopy} aset lain` +
      (nFail ? `, ${nFail} html gagal minify (fallback raw)` : "")
  );
}

const WATCH = process.argv.includes("--watch");
await build();
if (WATCH) {
  console.log("👀 watching frontend/ … (Ctrl+C untuk berhenti)");
  let timer = null;
  watch(SRC, { recursive: true }, () => {
    clearTimeout(timer);
    timer = setTimeout(() => build().catch((e) => console.error(e)), 150);
  });
}
