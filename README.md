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
  - `/js/components.js`: UI Components reusable (Combobox, Grids, Manager).
  - `/js/print.js`: Logika cetak thermal dan export PDF.
- `/css`: Sistem desain berbasis CSS Variables.

### 3. Alur Kerja Inti (Core Workflows)

#### A. Multi-Cabang (Multi-Branch)
- **Identity**: Setiap user memiliki `active_branch_id`.
- **Filtering**: Endpoint menggunakan `get_query(db, model, user)` di `auth.py` untuk memfilter data otomatis berdasarkan cabang user.
- **Accounting**: Jurnal dan Buku Kas dicatat per cabang (`branch_id`).

#### B. Siklus Pembelian (Purchase Cycle)
1. **Draft/PO**: Pesanan awal (stok belum bertambah).
2. **Purchase/Invoice**: Stok bertambah di Gudang Cabang, Jurnal Akuntansi otomatis terbentuk (Persediaan vs Hutang).
3. **Payment**: Pembayaran hutang cicil/lunas, Arus Kas berkurang, Hutang di Neraca berkurang.
4. **Export PDF**: Faktur profesional dengan alamat tujuan cabang otomatis.

#### C. Siklus Penjualan (Sales Cycle)
1. **POS**: Transaksi kasir realtime. Scan barcode -> Pilih Group Diskon -> Bayar.
2. **Realtime Stock**: Stok dipotong dari Gudang Default Cabang (mendukung kuantitas desimal untuk multi-satuan).
3. **Auto-Journal**: Mencatat Pendapatan, Kas/Bank, dan HPP (Harga Pokok Penjualan) secara instan.

#### D. Inventaris & Multi-Satuan
- **Virtual Variants**: Barang bisa memiliki satuan berbeda (Pcs vs Dus). Penjualan satuan kecil otomatis memotong stok induk secara proporsional (desimal).
- **Stock Movement**: Setiap perubahan stok wajib dicatat di `stock_movements` untuk audit trail (Before/After).

### 4. Skema Database (Highlight)
- `users`: Autentikasi dan hak akses cabang.
- `items`: Master barang (HPP, Harga Jual, Barcode, Satuan).
- `branches`: Data toko (Nama, Alamat Mandatory, Telepon).
- `purchases` & `purchase_items`: Transaksi masuk + tracking diskon bertingkat (`disc1`, `disc2`).
- `sales` & `sale_items`: Transaksi keluar.
- `journals` & `journal_entry_lines`: Double-entry bookkeeping system.
- `warehouses`: Pemisahan fisik stok dalam satu cabang.

### 5. Panduan AI CLI (Development Rules)
- **Schema First**: Jika menambah kolom di `models.py`, pastikan update `schemas.py` dan jalankan script migrasi SQL.
- **Branch Awareness**: Selalu gunakan `branch_id` saat membuat transaksi atau mengambil data.
- **UI Consistency**: Gunakan `createPremiumCombo` untuk dropdown dan `createOrderManager` untuk transaksi baru.
- **Event Handler Precision**: Pastikan handler `oninput` atau `onclick` di HTML sesuai dengan nama fungsi di JavaScript (misal: `hitungGrandTotal` vs `hitungTotal`).
- **State-Based UI**: Tombol interaktif (seperti Export PDF) harus memiliki validasi internal (cek ID transaksi) dan memberikan feedback (toast) jika kondisi belum terpenuhi, daripada hanya di-disable tanpa penjelasan.
- **Documentation**: Update `dokumentasi development.md` untuk setiap perubahan logika dan `GEMINI.md` untuk standar arsitektur.

---
*README ini adalah panduan navigasi utama untuk pemeliharaan dan pengembangan fitur baru.*
