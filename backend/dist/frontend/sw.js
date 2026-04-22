// sw.js
const CACHE_NAME = "eva-store-v1";
self.addEventListener("install", (e) => {
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(clients.claim());
});
self.addEventListener("fetch", (e) => {
  // Biarkan semua request lewat (bisa dioptimasi nanti)
  e.respondWith(fetch(e.request));
});
