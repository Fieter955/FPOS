// service-worker.js — DINONAKTIFKAN.
// Dulu strategi cache-first yang bisa menyajikan "/" & "/index.html" versi BASI
// (kode lama tetap jalan walau sudah update). Pendaftarannya sudah dihapus dari index.html,
// dan SW lama dicopot otomatis oleh pembersih di js/api.js.
// Dibiarkan kosong (bukan dihapus) agar /service-worker.js dari HTML lama yang masih
// ter-cache tidak 404.
