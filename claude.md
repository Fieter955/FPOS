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

## 🧮 Integritas Costing Persediaan (FIFO) & Jurnal–Neraca

> Hasil audit menyeluruh + **Fase 4** (Jun 2026). Bagian ini **WAJIB dipatuhi** saat menyentuh akuntansi/persediaan. Rincian historis ada di memory `fpos-accounting-audit.md` & `fpos-fifo-costing-migration.md`.

### 1. Model Costing (FIFO per-batch)

- `StockBatch` = lapisan persediaan (item, warehouse, supplier, purchase_item, `unit_cost`, `qty_received`, `qty_remaining`, `received_date`). `SaleItemBatch` = alokasi jual→batch. Service: `backend/app/services/inventory_fifo.py`.
- HPP saat jual = Σ(qty × `unit_cost`) batch tertua dulu (`consume_fifo`). `Item.buy_price` = **legacy/tampilan saja**, BUKAN sumber HPP.
- **DUA invarian wajib dijaga:**
  1. **Kuantitas**: `Σ StockBatch.qty_remaining == WarehouseStock.stock` per (gudang,item). Pantau: `GET /inventory/fifo-drift`.
  2. **Nilai**: `Σ(qty_remaining × unit_cost) == saldo GL 1-1400` per cabang. Pantau: `GET /inventory/value-drift`.
- Penjualan menurunkan GL `1-1400` & nilai batch **bersamaan** → tidak pernah bikin selisih nilai. Selisih hanya lahir di **pembelian** & **setup awal**.

### 2. Mandat Jurnal Otomatis

- `create_auto_journal` (`accounting.py`) punya gerbang balance (Dr=Cr) + akun-harus-ada. **JANGAN bypass.**
- **Jalur yang memutasi stok WAJIB posting jurnal ATOMIC**: panggil `pastikan_akun_ada(db, [...])` di AWAL (sebelum stok berubah), posting jurnal DI DALAM transaksi yang sama, lalu commit **sekali**. **JANGAN** bungkus jurnal dengan `try/except: print` yang menelan error (bikin stok pindah tanpa GL → laporan salah diam-diam). Pola benar: `sales.py`, `returns.py`, `inventory.py` (opname).

### 3. Neraca: Laba Ditahan DINAMIS (JANGAN jadikan jurnal penutup)

- `get_balance_sheet` menghitung Laba Ditahan tahun-tahun lalu secara **live** = Σ(pendapatan−beban) s/d akhir tahun sebelumnya (baris tampilan kode `3-1300*`). Sistem **TIDAK** memposting jurnal penutup; `close_books` hanya **mengunci** periode.
- **MANDAT**: JANGAN menambahkan jurnal penutup tahunan. Itu **bentrok** dengan restatement Fase 3 (koreksi HPP back-date ke periode tutup). Pendekatan dinamis = self-healing & selalu balance lintas tahun.

### 4. Perubahan Audit (F1–F6) & Fase 4

| Kode         | Isi                                                                                                                                                                                                                        | File utama                                                          |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------ | ------------------------------------------- |
| **F1**       | Laba Ditahan dinamis → neraca balance lintas tahun                                                                                                                                                                         | `accounting.py` `get_balance_sheet`                                 |
| **F2**       | Jurnal retur & opname jadi **atomic** + helper `pastikan_akun_ada`                                                                                                                                                         | `returns.py`, `inventory.py`, `accounting.py`                       |
| **F3**       | Blokir set `opening_balance` lewat editor akun (arahkan ke Setup Saldo Awal/Jurnal Manual)                                                                                                                                 | `accounting.py` `create_account`/`update_account`                   |
| **F4**       | Arus kas: pasangan HPP↔Persediaan diabaikan **hanya** pada jurnal ber-HPP (penjualan); beli persediaan **tunai** tetap jadi arus keluar                                                                                    | `accounting.py` `get_cash_flow_statement`                           |
| **F6**       | COGS retur jual = biaya lapisan FIFO **nyata** (`restore_sale_return` kembalikan biaya dipulihkan)                                                                                                                         | `inventory_fifo.py`, `returns.py`                                   |
| **Fase 4-A** | Biaya batch pembelian = biaya **landed** = `net_per_unit × (subtotal−diskon+pajak)/subtotal` → sama dgn debit GL `1-1400` (selisih baru tidak lahir)                                                                       | `purchase_flow.py` `receive_branch_stock`                           |
| **Fase 4-B** | True-up selisih lama → endpoint `POST /accounting/reconcile-inventory-value?dry_run=` + **tombol UI** "⚖️ Samakan Nilai Persediaan" (tab Tutup Buku). Selisih → Modal Transisi `3-1999` (**tanpa** hit laba). Idempoten (  | selisih                                                             | <1 dilewati) | `accounting.py`, `frontend/accounting.html` |
| **Fase 4-C** | Jurnal retur beli konsisten landed: mode gudang → barang keluar di biaya landed, selisih → `4-2000`/`5-1200`, **tanpa** kaki pajak `5-2000`; mode tanpa gudang (`total_carrying=None`) → pajak ke `5-2000` (perilaku lama) | `journal_service.py` `create_purchase_return_journal`, `returns.py` |
| **Fase 4-D** | Diagnostik selisih **nilai** `GET /inventory/value-drift` (beda dari `/fifo-drift` yang cek kuantitas)                                                                                                                     | `inventory.py`                                                      |

### 5. Akun kunci (Chart of Accounts)

`1-1400` Persediaan · `5-1100` HPP · `1-1600` Saldo Supplier (aset, debit) · `2-1300` Saldo Customer · `4-1100` Penjualan · `4-1200` Retur Penjualan · `4-2000` Diskon Pembelian (untung retur) · `5-1200` Beban Susut & Selisih Persediaan (rugi retur) · `5-2000` Beban Pajak · `3-1999` Modal Transisi (setup awal / true-up) · `3-1300` Laba Ditahan.

> Catatan: template `get_default_accounts()` masih menamai `1-1600`/`2-1300` dgn label lama ("Perlengkapan Toko"/"Uang Muka Penjualan") — **tipe sudah benar**, hanya label kosmetik (DB live sudah "Saldo di Supplier/Customer").

### 6. Operasional & uji

- **Uji integritas WAJIB di SALINAN** `ipos.db` (pakai sqlite `.backup()`); DB asli **jangan dimutasi** tanpa izin pemilik.
- **True-up selisih** (~Rp2,9jt di data live, belum dijalankan): pemilik memicu via tombol — "Cek Selisih" (dry-run) → "Samakan Sekarang". Aman diulang.
- Tabel `StockBatch`/`SaleItemBatch`/`Restatement` auto-create via `Base.metadata.create_all`; batch pembukaan di-seed di `main.py` `seed_opening_stock_batches` (dari `Item.buy_price`).
- **Komunikasi ke pemilik (Fieter)**: pakai bahasa awam, hindari jargon (GL, batch, kode akun) — dia pemilik bisnis, bukan akuntan/programmer.

---

_Dokumen ini diperbarui secara berkala. Gemini CLI akan menggunakan informasi ini sebagai basis pengetahuan utama._
