# aturan

nama fungsi dan variable pake bahasa indonesia yang mudah dipahami

# 💎 GEMINI CLI: FPOS Project Instructions & Standards

Dokumen ini berisi mandat operasional dan panduan arsitektur sistem FPOS. **Mandat di sini bersifat absolut** dan harus dipatuhi oleh Gemini CLI dalam setiap interaksi.

---

## 🏗️ Architectural Overview

- **Frontend**: Vanilla JavaScript (ES6+) + HTML5.
- **Styling**: Vanilla CSS dengan CSS Variables (definisi utama di `frontend/css/style.css`).
- **Backend**: FastAPI (Python) dengan SQLAlchemy ORM.
- **Database**: SQLite (`ipos.db`) menggunakan mode WAL untuk performa tinggi.
- **Waktu**: Semua transaksi menggunakan zona **WITA (Asia/Makassar)**.

---

## 🧩 Shared UI Components (Mandatory)

Selalu gunakan komponen yang sudah ada di `frontend/js/components.js` dan `frontend/css/components.css`. **Jangan implementasi ulang.**

### 1. Searchable Dropdowns (Combobox)

- **Fungsi**: `createPremiumCombo(container, data, config)`
- **Aturan**: Wajib digunakan untuk semua pemilihan entitas (Supplier, Customer, Akun, dll). Mendukung _fixed positioning_ dan filter real-time.

### 2. Unified Purchase Grid

- **Fungsi**: `createPurchaseGrid(container, config)`
- **Mode**:
  - `isFulfillment: false` (PO/Order): 1 kolom Qty (Pesan).
  - `isFulfillment: true` (Purchase): 2 kolom Qty (Pesan vs Terima).
- **Standar Penamaan**: Gunakan `qty_ordered` dan `qty_received` agar sinkron dengan Pydantic schema.
- **Visual Standards**:
  - Kolom Qty & Margin: Lebar kompak (60px).
  - Feedback Selisih: Input `qty_received` berubah warna (border/bg orange) jika berbeda dengan `qty_ordered`.
- **Layout Kolom**: `[Barang] [Pesan] [Terima*] [Harga Beli] [Margin %] [Harga Jual] [Diskon %] [Total]`

### 3. Unified Transaction Manager (OrderManager)

- **Fungsi**: `createOrderManager(containerId, config)`
- **Lingkup**: Wajib untuk alur "Catat Pembelian" dan "Catat Penjualan".
- **Fitur**: Mengelola pemilihan Supplier/Customer, input Tanggal, Nomor Referensi, Item Grid, dan Summary Box (Grand Total) secara otomatis.
- **Aturan Kalkulasi**: Dalam mode `purchase`, Total **SELALU** dihitung berdasarkan `qty_received`.

### 4. UI Helpers Lainnya

- **Filter Bar**: `createFilterBar(container, config)` (Date Range, Status, & Premium Search).
- **Payment Modal**: `createPaymentModal(config)` (Split payment Cash/Bank & Verifikasi Saldo).
- **Barcode Scanner**: `setupBarcodeScanner(onScan, config)` (Global listener dengan deteksi kecepatan ketikan hardware).

---

## 🚀 Workflow Standards & Rules

### 1. Modularisasi & CRUD

- Pindahkan logika reusable ke `components.js` jika digunakan di 2+ halaman.
- **Modal CRUD**: Setiap form berbasis modal wajib memiliki 3 fungsi eksplisit: `open[Name]Modal()`, `edit[Name](data)`, dan `save[Name]()`.
- **Handling ID**: Pastikan fungsi `edit` menyimpan ID (misal: `editCoaId = a.id`).

### 2. Draft & Admin Rules

- Hanya `role: admin` yang boleh memproses/mengedit draft di `catat-pembelian.html`.
- **Supplier Locking**: Supplier harus di-lock (`.disable()`) saat mengedit draft yang sudah ada.
- **Change Tracking**: Tampilkan `showConfirm` jika ada perubahan item (tambah/hapus/qty) pada draft.
- **Finalisasi**: Proses draft harus mengubah status (misal ke `unpaid` untuk hutang dagang).

### 3. Discrepancy Handling (Selisih Stok)

- Jika `qty_received < qty_ordered` saat fulfillment, tawarkan opsi "Reorder Missing" untuk membuat draft baru berisi sisa barang yang belum diterima.

---

## 🏢 Multi-Branch & Accounting Standards

### 1. Data Integrity

- **Branch Address**: Setiap cabang **WAJIB** memiliki alamat lengkap.
- **Warehouse Linking**: Cabang memiliki default warehouse dengan format `WH-CBG-XXXX`.

### 2. Inter-Branch PO Workflow (Mandatory)

- **Routing**: Cabang non-pusat mengirim PO ke Main Store (ID 1) dengan `is_branch_request=true` dan `status='pending'`.
- **Simultaneous Journals** (di `journal_service.py`):
  - **Pusat (Branch 1)**: Debit `3-2200 Kirim Barang ke Cabang`, Credit `1-1100 Kas/Hutang`.
  - **Cabang Penerima**: Debit `1-1400 Persediaan`, Credit `3-2100 Transfer dari Pusat`.
- **Stock Isolation**: Stok hanya bertambah di gudang cabang tujuan (`target_branch_id`), **bukan** di stok Toko Pusat.

---

## 🧠 AI Optimization & Logic Consistency

1. **Schema Sync**: Nama field di Frontend (JS) **WAJIB** sama dengan Pydantic `schemas.py` (misal: `qty_received`, bukan `qty_diterima`).
2. **Schema Coverage**: `Update` schemas (misal: `AccountUpdate`) harus mencakup semua field yang bisa diedit di UI untuk mencegah kegagalan simpan silent.
3. **State Awareness**: Selalu filter data menggunakan `current_user.active_branch_id`.
4. **Unique Integrity**: Nama barang (`Item.name`) dan kode barang (`Item.code`) bersifat **UNIK**.

---

## 📦 System Maintenance & Build

### 1. Frontend Build

- Jalankan `npm run build` untuk memproses aset ke `frontend-dist/`.
- Backend secara otomatis menggunakan `frontend-dist/` jika aplikasi dalam bentuk frozen (`.exe`) atau env `FPOS_USE_BUILD=1` aktif.

### 2. Executable (.exe) Build

- Gunakan `PyInstaller` dengan spec file yang sudah ada.
- **Penting**: Pastikan DLL OpenSSL dari conda env diutamakan dalam PATH agar `_ssl` tidak crash.

---

## 📜 Brain Map: Arsitektur Sistem (iPos 5.0)

### Struktur Direktori Utama

- `/backend`: Core API, Models, Schemas, & Logika Bisnis.
- `/frontend`: Antarmuka Pengguna (Vanilla SPA Style).
  - `/item`: Modul manajemen barang (Fragmented HTML).
  - `/js`: Logika frontend modular.
- `/uploads`: Aset gambar dan file sistem.

### Siklus Bisnis Inti

- **Purchase**: Draft -> Invoice -> Stok & Jurnal -> Payment.
- **Sales**: POS (Realtime) -> Potong Stok -> Jurnal Otomatis.
- **Trade-In**: Mekanisme stok ganda (Barang Kembali & Barang Baru) dengan penanganan surplus/defisit otomatis ke saldo pelanggan.
- **Broken Return**: Alur retur barang rusak hasil Trade-In ke Supplier dengan validasi faktur historis.

---

_Dokumen ini diperbarui secara berkala. Gemini CLI akan menggunakan informasi ini sebagai basis pengetahuan utama._
