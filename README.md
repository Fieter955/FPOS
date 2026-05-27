# iPos 5.0 — Enterprise Multi-Branch POS System

Sistem Point of Sales (POS) tingkat enterprise yang dirancang khusus untuk toko retail dan bangunan dengan dukungan Multi-Cabang, Multi-Gudang, dan Akuntansi Double-Entry otomatis.

---

## 🧠 Brain Map: Arsitektur & Struktur Sistem

Dokumentasi ini dirancang agar AI (CLI Agent) dapat memahami struktur kerja sistem secara utuh.

### 1. Stack Teknologi & Standar
- **Backend**: FastAPI (Python) + SQLAlchemy (SQLite).
- **Frontend**: Vanilla JS (ES6) + HTML5 + CSS Variables (No frameworks like React/Tailwind).
- **Database**: SQLite (`ipos.db`) menggunakan mode WAL untuk konkurensi.
- **Waktu**: Semua transaksi menggunakan zona **WITA (Asia/Makassar)**. Backend menggunakan `pytz`.

### 2. Struktur Direktori Utama
- `/backend`: Core API, Models, Schemas, dan Business Logic.
  - `/app/models.py`: Definisi tabel database (Single Source of Truth).
  - `/app/schemas.py`: Validasi data Pydantic (In/Out API).
  - `/app/routes`: Endpoint API modular (Sales, Purchase, Inventory, dll).
  - `/app/services`: Logika bisnis berat (Virtual Units, AI Engine).
- `/frontend`: Antarmuka Pengguna (UI).
  - `/js/api.js`: Wrapper untuk komunikasi ke Backend.
  - `/js/components.js`: UI Components reusable (Combobox, Grids, Manager, Toggle Button).
  - `/js/print.js`: Logika cetak thermal dan export PDF.
- `/css`: Sistem desain berbasis CSS Variables.

### 3. Alur Kerja Inti (Core Workflows)

#### A. Multi-Cabang (Multi-Branch) & Toko Utama
- **Identity**: Setiap user memiliki `active_branch_id`.
- **Toko Utama (Main Store)**: Didefinisikan secara dinamis sebagai cabang di mana `id == 1` ATAU memiliki `status == 'Toko Utama'`.
- **Logic**: Menggunakan helper global `isMainStore()` di frontend dan pengecekan status di backend untuk mengizinkan fitur pusat (monitoring setoran, approval permintaan barang) pada cabang yang ditunjuk.
- **Item Visibility (Penyaringan Master Barang)**:
  - **Pusat**: Dapat melihat dan mengelola **seluruh** master barang di sistem.
  - **Cabang**: Hanya dapat melihat barang yang **pernah dikirim** oleh Toko Utama. Secara teknis, sistem melakukan *Inner Join* antara master barang dan stok gudang cabang. Barang baru akan muncul otomatis di daftar master cabang segera setelah Mutasi Stok (Warehouse Transfer) pertama dilakukan ke gudang cabang tersebut.
- **Filtering**: Endpoint menggunakan `get_query(db, model, user)` di `auth.py` untuk memfilter data otomatis berdasarkan cabang user.
- **Accounting**: Jurnal dan Buku Kas dicatat per cabang (`branch_id`).

#### B. Siklus Pembelian (Purchase Cycle)
1. **Draft/PO**: Pesanan awal (stok belum bertambah).
2. **Purchase/Invoice**: Stok bertambah di Gudang Cabang, Jurnal Akuntansi otomatis terbentuk (Persediaan vs Hutang).
3. **Payment**: Pembayaran hutang cicil/lunas, Arus Kas berkurang, Hutang di Neraca berkurang.
4. **Export PDF**: Faktur profesional dengan alamat tujuan cabang otomatis.
5. **Dokumentasi API**: Seluruh endpoint di `backend/app/routes/purchases.py` kini telah dilengkapi dengan komentar deskriptif bahasa Indonesia untuk menjelaskan logika operasional (Request Cabang, Fulfillment PO, dan Akuntansi).
### 3. Penjualan (Sales Cycle)
1. **POS**: Transaksi kasir realtime. Scan barcode -> Pilih Group Diskon -> Bayar.
2. **Realtime Stock**: Stok dipotong dari Gudang Default Cabang (mendukung kuantitas desimal untuk multi-satuan).
3. **Auto-Journal**: Mencatat Pendapatan, Kas/Bank, dan HPP (Harga Pokok Penjualan) secara instan.

#### D. Siklus Tukar Tambah (Trade-In Cycle)
Fitur Tukar Tambah memungkinkan pelanggan mengembalikan barang lama dan mengambil barang baru dalam satu transaksi terpadu:
1. **Mekanisme Stok Ganda**:
   - **Barang Kembali**: Menambah stok gudang (sebagai barang masuk) dengan pencatatan kondisi (bagus/rusak).
   - **Barang Baru**: Mengurangi stok gudang (sebagai barang keluar).
2. **Perhitungan Selisih Otomatis**: 
   - Sistem menghitung `Total Nilai Baru - Total Nilai Kembali`.
   - **Selisih Positif**: Pelanggan membayar sisa ke toko.
   - **Selisih Negatif**: Toko mengembalikan uang ke pelanggan.
3. **Integritas Data & Branch Awareness**:
   - Setiap mutasi stok (`StockMovement`) yang dihasilkan dari tukar tambah kini wajib mencatat `branch_id` untuk memastikan laporan persediaan per cabang akurat.
   - Sinkronisasi field name antara frontend (`return_price`, `sell_price`) dan backend Pydantic schemas untuk mencegah error validasi data.
4. **Detail Transaksi**: Riwayat lengkap tersedia dengan rincian barang yang masuk dan keluar beserta harga per itemnya.

#### E. Siklus Retur Barang Rusak (Broken Items Return)
Khusus untuk barang yang masuk melalui Tukar Tambah dengan kondisi **Rusak/Damaged**, sistem menyediakan alur khusus untuk mengembalikan barang tersebut ke Supplier:
1. **Tracking Status**: Setiap barang rusak hasil Tukar Tambah dilacak menggunakan kolom `returned_qty` di backend untuk mengetahui berapa banyak yang sudah diproses retur ke supplier.
2. **Alur Retur ke Supplier**:
   - **Tab Retur Barang Rusak**: Menyediakan daftar inventori barang rusak yang siap diretur.
   - **Proses Manual**: Karena barang rusak berasal dari pelanggan, pengguna harus memilih Supplier secara manual. 
   - **Validasi Riwayat**: Sistem mewajibkan pemilihan Faktur Pembelian historis (icon 📜) dari supplier tersebut untuk memastikan integritas data (barang hanya bisa diretur ke supplier tempat kita membelinya).
   - **Integrasi Stok**: Proses ini secara otomatis memotong stok inventori barang rusak dan membuat record `PurchaseReturn` di sistem akuntansi.
3. **Visual Feedback**: Label status akan berubah otomatis menjadi "Retur Sebagian" atau "Retur Full" pada daftar barang rusak segera setelah transaksi berhasil diproses.

### 4. Fitur Barcode Scanner (Global Auto-Cursor)
- **Logika Deteksi**: Menggunakan komponen `setupBarcodeScanner` yang mendeteksi kecepatan ketikan (hardware scanner) vs pengetikan manual manusia menggunakan jeda waktu (`Date.now()`).
- **Auto-Cursor**: Scanner bekerja secara global di background. Pengguna tidak perlu memfokuskan kursor pada input box tertentu; hasil scan akan langsung diproses oleh halaman yang aktif (POS, Pembelian, atau PO).
- **Fallback**: Jika scanner gagal atau ingin input manual, pengguna tetap bisa menggunakan bar pencarian seperti biasa.

### 5. Skema Database (Highlight)

#### D. Inter-Branch PO (Pemenuhan Permintaan Cabang)
- **Workflow**: Cabang membuat Request (`is_branch_request=true`) -> Pusat proses di `po.html` -> Simpan Draft atau Bayar.
- **Reciprocal Accounting**: 
  - Saat Pusat memproses PO untuk Cabang, sistem otomatis mencatat **dua jurnal sekaligus**:
    1. **Toko Pusat**: Mendebit akun `3-2200 Kirim Barang ke Cabang` dan mengkredit Kas/Hutang.
    2. **Cabang Penerima**: Mendebit akun `1-1400 Persediaan` dan mengkredit `3-2100 Transfer dari Pusat` (Hutang Antar Kantor).
- **Stock Isolation**: Stok hanya bertambah di gudang cabang tujuan (`target_branch_id`), bukan di Toko Pusat, untuk mencegah data persediaan yang keliru.
- **Visibility**: Faktur pemenuhan milik Pusat otomatis disembunyikan dari daftar pembelian reguler (`purchases.html`) milik Cabang agar tidak membingungkan, namun tetap terlihat di **Riwayat Request Saya** pada `po.html`.

### 4. Skema Database (Highlight)
- `users`: Autentikasi dan hak akses cabang.
- `items`: Master barang (HPP, Harga Jual, Barcode, Satuan).
- `branches`: Data toko (Nama, Alamat Mandatory, Telepon).
- `purchases` & `purchase_items`: Transaksi masuk + tracking diskon bertingkat (`disc1`, `disc2`).
- `sales` & `sale_items`: Transaksi keluar.
- `journals` & `journal_entry_lines`: Double-entry bookkeeping system.
- `warehouses`: Pemisahan fisik stok dalam satu cabang.

### 5. Keamanan & Integritas Data
- **Unique Constraint**: Nama barang (`Item.name`) dan kode barang (`Item.code`) bersifat **UNIK**. Sistem akan menolak pembuatan barang dengan nama yang sama untuk mencegah duplikasi data dan kerancuan stok.
- **Audit Logs**: Setiap tindakan penting (Create, Update, Delete) dicatat di tabel `audit_logs` lengkap dengan ID User dan detail perubahannya.
- **Book Closing**: Transaksi pada tanggal yang sudah dilakukan "Tutup Buku" tidak dapat diubah atau dihapus untuk menjaga konsistensi laporan keuangan.

### 6. Pemeliharaan (Maintenance)
Jika sistem mendeteksi adanya data ganda pada versi lama sebelum aturan "Unique Name" diterapkan, gunakan skrip pembersih berikut:
```bash
# Masuk ke folder backend
cd backend
# Jalankan skrip pembersihan duplikat
python cleanup_duplicates.py
```
*Skrip ini akan menghapus barang duplikat (mempertahankan yang tertua) dan menerapkan Unique Index pada database.*

### 7. Panduan AI CLI (Development Rules)
- **Schema First**: Jika menambah kolom di `models.py`, pastikan update `schemas.py` dan jalankan script migrasi SQL.
- **Branch Awareness**: Selalu gunakan `branch_id` saat membuat transaksi atau mengambil data.
- **UI Consistency**: Gunakan `createPremiumCombo` untuk dropdown dan `createOrderManager` untuk transaksi baru.
- **Event Handler Precision**: Pastikan handler `oninput` atau `onclick` di HTML sesuai dengan nama fungsi di JavaScript (misal: `hitungGrandTotal` vs `hitungTotal`).
- **State-Based UI**: Tombol interaktif (seperti Export PDF) harus memiliki validasi internal (cek ID transaksi) dan memberikan feedback (toast) jika kondisi belum terpenuhi, daripada hanya di-disable tanpa penjelasan.
- **Documentation**: Update `dokumentasi development.md` untuk setiap perubahan logika dan `GEMINI.md` untuk standar arsitektur.

---
*README ini adalah panduan navigasi utama untuk pemeliharaan dan pengembangan fitur baru.*
