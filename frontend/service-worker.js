const CACHE_NAME = "ipos-cache-v1";
const urlsToCache = [
  "/",
  "/index.html",
  "/manifest.json",
  // Tambahkan file CSS atau JS utama Anda di sini nanti
];

// Saat aplikasi diinstall, simpan file-file penting
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(urlsToCache);
    }),
  );
});

// Saat aplikasi memanggil data, cek dulu di penyimpanan (cache)
self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      if (response) {
        return response; // Gunakan file tersimpan agar cepat
      }
      return fetch(event.request); // Ambil dari server jika belum ada
    }),
  );
});
