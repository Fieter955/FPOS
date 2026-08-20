# Update FPOS dari Dalam Aplikasi

## Rilis pertama kali

1. Pastikan `APP_VERSION` di `backend/app/config.py` sudah dinaikkan.
2. Jalankan script release dari root project:

```powershell
.\build-release.ps1 `
  -Version 5.0.3 `
  -DownloadUrl "https://github.com/Fieter955/FPOS/releases/download/v5.0.3/FPOS-5.0.3-Windows.zip" `
  -Notes "Inventory documents, receipt renderer, dashboard categories" `
  -CondaEnv base
```

Build release menggunakan environment Conda `base`. Jika dependensi belum ada,
pasang terlebih dahulu dari root project:

```powershell
conda run -n base python -m pip install -r backend\requirements.txt pyinstaller
```

Script release memakai isi `frontend-dist` yang sudah ada; pastikan folder itu
sudah berisi frontend terbaru sebelum build.

3. Upload ZIP ke URL yang sama dengan `DownloadUrl`.
4. Commit dan push `version.json` di root repository ke branch `main`; script
   release otomatis memperbarui file tersebut.
5. Upload `FPOS-Updater.exe` di dalam ZIP; script release sudah memasukkannya.

Paket ZIP hanya berisi kode aplikasi. Database, `.env`, `secret.key`,
`uploads`, dan `backups` tidak boleh dimasukkan ke rilis.

## Konfigurasi server client

Di file `.env` instalasi client, isi:

```dotenv
UPDATE_CHECK_URL=https://raw.githubusercontent.com/Fieter955/FPOS/main/version.json
UPDATE_ALLOW_HTTP=false
```

Setiap client cukup membuka Pengaturan → Update Aplikasi sebagai admin.
Updater akan membuat backup database, memverifikasi SHA-256, menghentikan FPOS,
mengganti kode, menjalankan health check, lalu melakukan rollback jika versi
baru gagal start.

## Catatan operasional

- Rilis harus menggunakan HTTPS.
- Jangan mengubah `secret.key` atau database saat membuat ZIP.
- Minta user menyelesaikan transaksi sebelum menekan Update Sekarang.
- Tandatangani `FPOS.exe` dan `FPOS-Updater.exe` dengan Authenticode sebelum
  distribusi produksi untuk mengurangi peringatan SmartScreen.
- Simpan ZIP dan `version.json` lama agar rollback manual tetap memungkinkan.
