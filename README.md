# FPOS

## Pola fokus modal master dari Form Barang

Jika pengguna menambah data master dari dalam form Barang (misalnya Jenis/Kategori,
Merek, Satuan, atau Supplier), setelah data berhasil disimpan:

1. Simpan data master dan refresh pilihan terkait.
2. Tutup modal master terlebih dahulu.
3. Tampilkan kembali modal Form Barang tanpa menginisialisasi ulang atau menghapus isinya.
4. Kembalikan fokus ke kontrol yang memicu modal tersebut (`fKat`, `fMerek`, `fSat`,
   atau `fSupplierContext`). Gunakan `setTimeout(..., 0)` setelah modal dibuka agar
   fokus bawaan `openModal()` tidak menimpa fokus tersebut.

Jangan membuka Form Barang sebelum modal master ditutup dan jangan membiarkan fokus
hilang setelah proses simpan. Pola ini memastikan penekanan Enter berikutnya tetap
berada di bagian form yang sedang dikerjakan, bukan kembali ke Nama Barang.

## Build release Windows

Build release wajib menggunakan environment Conda `base`. Environment `ipos` dan
`freelance` tidak digunakan lagi.

Pastikan dependensi backend dan PyInstaller sudah terpasang di `base`:

```powershell
conda run -n base python -m pip install -r backend\requirements.txt pyinstaller
```

Dari root project, jalankan build dengan versi dan URL ZIP yang sesuai:

```powershell
.\build-release.ps1 `
  -Version 5.0.3 `
  -DownloadUrl "https://github.com/Fieter955/FPOS/releases/download/v5.0.3/FPOS-5.0.3-Windows.zip" `
  -Notes "Update PPN setting non PKP" `
  -CondaEnv base
```

Script release memakai isi `frontend-dist` yang sudah ada dan tidak menjalankan
build frontend dengan Node/npm. Pastikan folder tersebut sudah berisi frontend
terbaru sebelum membuat release.

Hasil build berada di `rilis\generated`. Paket update hanya berisi executable,
folder `_internal`, `frontend-dist`, dan `FPOS-Updater.exe`; jangan memasukkan
database, `.env`, `secret.key`, `uploads`, atau `backups`.
