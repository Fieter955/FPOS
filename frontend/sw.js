// sw.js — DINONAKTIFKAN.
// Dulu service worker ini pass-through (e.respondWith(fetch(e.request))) yang hanya
// menambah overhead per-request tanpa manfaat caching, dan bikin loading/navigasi
// di browser terasa lebih lambat dari versi .exe (WebView2).
// Pendaftarannya sudah dihapus dari index.html & pos.html, dan SW lama dicopot otomatis
// oleh pembersih di js/api.js. File ini sengaja dibiarkan kosong (bukan dihapus) agar
// permintaan /sw.js dari HTML lama yang masih ter-cache tidak menghasilkan 404.
//
// Jika nanti butuh PWA installable di produksi, ganti file ini dengan strategi caching
// yang benar (precache app-shell, dan NETWORK-ONLY untuk /api/*), lalu daftarkan lagi.
