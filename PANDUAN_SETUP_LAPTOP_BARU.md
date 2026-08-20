# Panduan Setup FPOS di Laptop Baru

Panduan ini dipakai ketika laptop baru akan menjadi **server FPOS mandiri** dengan kondisi berikut:

- database laptop baru terpisah dari laptop lama;
- perangkat pengguna membuka FPOS melalui URL publik;
- perangkat pengguna tidak perlu memasang Tailscale;
- laptop server tetap harus memasang dan terhubung ke Tailscale;
- koneksi publik menggunakan **Tailscale Funnel** ke aplikasi lokal pada port `8010`.

> **Penting:** Tailscale Funnel membuka halaman login FPOS ke internet. Lakukan setup pertama secara lokal dan ganti seluruh password bawaan sebelum Funnel diaktifkan.

## Daftar isi

1. [Gambaran proses](#1-gambaran-proses)
2. [Menyiapkan paket rilis bersih](#2-menyiapkan-paket-rilis-bersih)
3. [Memasang FPOS di laptop baru](#3-memasang-fpos-di-laptop-baru)
4. [Setup pertama dan pengamanan akun](#4-setup-pertama-dan-pengamanan-akun)
5. [Memasang dan mengaktifkan Tailscale Funnel](#5-memasang-dan-mengaktifkan-tailscale-funnel)
6. [Pengujian](#6-pengujian)
7. [Auto-start dan operasional harian](#7-auto-start-dan-operasional-harian)
8. [Backup](#8-backup)
9. [Pemecahan masalah](#9-pemecahan-masalah)
10. [Checklist serah-terima](#10-checklist-serah-terima)

## 1. Gambaran proses

```text
Siapkan paket bersih
        ↓
Salin ke C:\FPOS di laptop baru
        ↓
Jalankan secara lokal dengan Funnel masih mati
        ↓
Ganti password bawaan dan isi data toko
        ↓
Instal/login Tailscale di laptop server
        ↓
Aktifkan Funnel untuk port 8010
        ↓
Uji URL publik dari perangkat lain
```

Aplikasi hanya mendengarkan `127.0.0.1:8010`. Tailscale menjadi penghubung HTTPS dari URL `*.ts.net` ke port lokal tersebut. Port `8010` tidak perlu dibuka pada router atau diteruskan melalui Windows Firewall.

## 2. Menyiapkan paket rilis bersih

Bagian ini dilakukan di laptop pengembangan. Jangan mengirim folder `backend\dist\FPOS` yang lama secara langsung karena folder tersebut dapat berisi database, kunci, konfigurasi, dan data instalasi sebelumnya.

### 2.1 Bangun versi terbaru

Buka PowerShell pada root proyek, lalu jalankan:

```powershell
npm run build
Set-Location backend
conda run -n ipos python -m PyInstaller --clean --noconfirm FPOS.spec
conda run -n ipos python -m PyInstaller --clean --noconfirm FPOS-Updater.spec
Set-Location ..
```

Hasil executable berada di `backend\dist\FPOS`. Frontend terbaru berada di `frontend-dist`.

### 2.2 Buat folder paket baru

Buat folder kosong yang belum pernah dipakai, misalnya `C:\tmp\FPOS-rilis-baru`:

```powershell
$paketRilis = "C:\tmp\FPOS-rilis-baru"
if (Test-Path -LiteralPath $paketRilis) {
    throw "Folder paket sudah ada. Gunakan nama folder baru agar data lama tidak ikut."
}

New-Item -ItemType Directory -Path $paketRilis
Copy-Item -LiteralPath "backend\dist\FPOS\FPOS.exe" -Destination $paketRilis
Copy-Item -LiteralPath "backend\dist\FPOS\_internal" -Destination $paketRilis -Recurse
Copy-Item -LiteralPath "backend\dist\FPOS-Updater.exe" -Destination $paketRilis
Copy-Item -LiteralPath "frontend-dist" -Destination "$paketRilis\frontend-dist" -Recurse
New-Item -ItemType Directory -Path "$paketRilis\uploads"
Copy-Item -LiteralPath "backend\.env.example" -Destination "$paketRilis\.env"
```

Folder paket bersih hanya boleh berisi:

```text
FPOS-rilis-baru/
├── FPOS.exe
├── FPOS-Updater.exe
├── _internal/
├── frontend-dist/
├── uploads/
└── .env
```

Pastikan file berikut **tidak ikut**:

- `ipos.db`, `ipos.db-wal`, atau `ipos.db-shm`;
- `secret.key`;
- `.autostart_configured`;
- `error_log.txt` atau file log lain;
- folder `backups`;
- folder `uploads` milik toko lain;
- `.env` dari instalasi yang sudah berjalan.

> Jangan mengambil hanya `FPOS.exe`. Mode build aplikasi ini adalah `onedir`, sehingga folder `_internal` dan `frontend-dist` juga wajib ikut.

## 3. Memasang FPOS di laptop baru

1. Pastikan laptop menggunakan Windows 10 atau Windows 11 dan memiliki akses Administrator.
2. Buat folder `C:\FPOS`.
3. Salin seluruh isi paket rilis bersih ke `C:\FPOS`.
4. Jangan menaruh database aktif di Desktop/Documents yang disinkronkan OneDrive, Google Drive, atau layanan sinkronisasi lain.
5. Buka `C:\FPOS\.env` memakai Notepad.

Untuk setup pertama, pastikan nilai jaringan seperti berikut:

```dotenv
DATABASE_URL=sqlite:///./ipos.db
SECRET_KEY=
TAILSCALE_PUBLIC=false
ALLOWED_ORIGINS=*
```

Aturan pengisian:

- Biarkan `SECRET_KEY=` kosong. Aplikasi akan membuat `secret.key` acak saat pertama dijalankan.
- Biarkan `TAILSCALE_PUBLIC=false` sampai semua password bawaan sudah diganti.
- Isi `VENDOR_BOOTSTRAP_PASSWORD` dengan password kuat dan unik **sebelum aplikasi pertama kali dijalankan**.
- Isi konfigurasi Drive, email, Telegram, atau AI hanya jika fitur tersebut memang digunakan.

Contoh password awal vendor pada `.env`:

```dotenv
VENDOR_BOOTSTRAP_PASSWORD=GantiDenganPasswordUnikYangKuat
```

Jangan memakai contoh tersebut sebagai password asli.

## 4. Setup pertama dan pengamanan akun

1. Klik dua kali `C:\FPOS\FPOS.exe`.
2. Jika Windows menampilkan pertanyaan auto-start, pilih **Yes** untuk laptop yang memang akan menjadi server tetap.
3. Tunggu jendela **Eva Store** terbuka.
4. Login secara lokal menggunakan akun administrator awal.
5. Buka **Pengaturan → Password**, lalu ganti password administrator bawaan.
6. Buka halaman **Pengguna**, lalu pastikan akun `Fieter` juga memakai password kuat dan unik.
7. Nonaktifkan akun lain yang tidak dipakai.
8. Selesaikan data toko/cabang dan pengaturan dasar.
9. Tutup lalu buka kembali FPOS, kemudian pastikan password baru dapat dipakai.

Jangan melanjutkan ke Funnel jika masih ada akun dengan password bawaan.

## 5. Memasang dan mengaktifkan Tailscale Funnel

### 5.1 Instal dan login

1. Unduh Tailscale dari [halaman resmi Tailscale untuk Windows](https://tailscale.com/download/windows).
2. Instal Tailscale pada laptop server.
3. Login ke akun/tailnet yang akan menjadi pemilik server tersebut.
4. Gunakan nama perangkat yang mudah dikenali, misalnya nama toko atau lokasi laptop.
5. Pastikan ikon Tailscale menunjukkan status terhubung.

Perangkat pelanggan yang hanya membuka URL Funnel tidak perlu memasang atau login Tailscale.

### 5.2 Aktifkan mode publik di FPOS

Tutup FPOS, lalu ubah konfigurasi berikut di `C:\FPOS\.env`:

```dotenv
TAILSCALE_PUBLIC=true
ALLOWED_ORIGINS=*
```

Simpan file, kemudian buka kembali `FPOS.exe` dan biarkan aplikasi tetap berjalan.

### 5.3 Aktifkan Funnel

Buka **PowerShell sebagai Administrator**, kemudian jalankan:

```powershell
tailscale status
tailscale funnel --bg 8010
tailscale funnel status
```

Saat pertama kali Funnel diaktifkan, Tailscale dapat membuka halaman persetujuan di browser. Selesaikan persetujuan tersebut. Funnel memerlukan MagicDNS, HTTPS, dan izin Funnel pada tailnet; perintah resmi Tailscale biasanya membantu mengaktifkan persyaratan tersebut.

Jika perintah `tailscale` tidak ditemukan, gunakan lokasi executable lengkap:

```powershell
& "C:\Program Files\Tailscale\tailscale.exe" status
& "C:\Program Files\Tailscale\tailscale.exe" funnel --bg 8010
& "C:\Program Files\Tailscale\tailscale.exe" funnel status
```

Output akan menampilkan alamat seperti:

```text
https://nama-perangkat.nama-tailnet.ts.net
```

Simpan URL tersebut sebagai alamat FPOS untuk instalasi ini. URL terikat pada perangkat Tailscale; mengganti identitas atau nama perangkat dapat mengubah alamat yang digunakan.

Rujukan resmi:

- [Tailscale Funnel](https://tailscale.com/kb/1223/funnel)
- [Perintah Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
- [Instalasi Tailscale](https://tailscale.com/docs/install)

## 6. Pengujian

### 6.1 Uji lokal

Dengan FPOS masih berjalan, buka PowerShell:

```powershell
Test-NetConnection 127.0.0.1 -Port 8010
```

Nilai `TcpTestSucceeded` harus `True`.

### 6.2 Uji publik

1. Matikan Wi-Fi pada ponsel dan gunakan data seluler, atau gunakan perangkat lain yang tidak memasang Tailscale.
2. Buka URL HTTPS `*.ts.net` yang diperoleh dari `tailscale funnel status`.
3. Pastikan halaman login FPOS tampil.
4. Login menggunakan akun yang passwordnya sudah diganti.
5. Buat satu transaksi/data uji yang aman, muat ulang halaman, dan pastikan datanya tetap tersimpan.
6. Pastikan URL `/docs`, `/redoc`, dan `/openapi.json` tidak terbuka pada mode publik.

## 7. Auto-start dan operasional harian

Konfigurasi `--bg` membuat Funnel tetap tersimpan pada Tailscale. Namun FPOS juga harus berjalan agar URL dapat digunakan.

- Pilih **Yes** pada pertanyaan auto-start ketika FPOS pertama kali dijalankan.
- Pastikan Tailscale otomatis aktif dan terhubung setelah Windows login.
- Jangan mematikan laptop server pada jam operasional.
- Pastikan koneksi internet laptop stabil.
- Setelah listrik mati atau Windows restart, cek kembali URL dari perangkat lain.

Jika pertanyaan auto-start pernah dijawab **No**, hapus file `C:\FPOS\.autostart_configured`, lalu buka kembali FPOS agar pertanyaannya muncul lagi.

### 7.1 Hubungkan agen printer struk

Struk transaksi diproses oleh agen printer Windows, bukan dialog print browser.
Lakukan langkah ini pada setiap cabang yang memiliki printer kasir:

1. Login sebagai administrator, pilih cabang aktif, lalu buka **Pengaturan > Printer**.
2. Isi identitas toko, footer, lebar kertas 58/80 mm, dan pilihan cetak otomatis,
   lalu simpan. Pengaturan berlaku untuk seluruh kasir pada cabang aktif.
3. Klik **Buat / Rotasi Token Agen** dan salin token saat ditampilkan. Token lama
   langsung tidak berlaku setelah rotasi.
4. Jalankan `iPos5_Printer.exe` pada komputer yang tersambung ke printer. Isi URL
   server FPOS (cukup sampai nama host), tempel token, pilih nama printer Windows,
   lalu aktifkan auto-start bila diperlukan.
5. Kembali ke **Pengaturan > Printer**, klik **Test Print**, dan pastikan status job
   berubah dari `pending`/`processing` menjadi `done`.

Untuk mengganti URL, token, atau printer pada agen yang sudah dikonfigurasi, jalankan:

```powershell
.\iPos5_Printer.exe --setup
```

Gunakan token berbeda untuk setiap cabang. Jangan mengirim token melalui grup publik
atau menyalin `printer_config.json` ke komputer yang tidak dipercaya.

## 8. Backup

Instalasi ini memakai database sendiri di:

```text
C:\FPOS\ipos.db
```

Foto barang dan file unggahan berada di:

```text
C:\FPOS\uploads
```

Ketentuan backup:

- gunakan fitur backup aplikasi untuk database;
- backup folder `uploads` secara terpisah karena foto barang tidak disimpan di dalam database;
- jangan menyalin `ipos.db` secara manual ketika aplikasi sedang menulis transaksi;
- jika harus menyalin manual, tutup FPOS terlebih dahulu dan pastikan prosesnya benar-benar berhenti;
- jangan menjalankan database langsung dari folder sinkronisasi cloud.

## 9. Pemecahan masalah

### Perintah Tailscale menghasilkan `Access is denied`

Tutup terminal, kemudian buka **PowerShell → Run as Administrator** dan ulangi perintah.

### `tailscale status` menyatakan belum login

Buka aplikasi Tailscale, login kembali, dan pastikan perangkat muncul pada daftar mesin di akun Tailscale.

### Funnel belum menghasilkan URL

Pastikan FPOS sedang berjalan, lalu jalankan sebagai Administrator:

```powershell
tailscale funnel reset
tailscale funnel --bg 8010
tailscale funnel status
```

### URL muncul tetapi halaman tidak dapat dibuka

Periksa server lokal:

```powershell
Test-NetConnection 127.0.0.1 -Port 8010
```

Jika hasilnya `False`, buka kembali `C:\FPOS\FPOS.exe`. Periksa juga:

```text
C:\FPOS\error_log.txt
```

### URL menampilkan halaman tetapi login gagal

- pastikan username dan password berasal dari instalasi laptop baru;
- database instalasi ini terpisah dari laptop lama;
- password atau transaksi dari laptop lama tidak otomatis tersedia di sini.

### URL berubah atau tidak berlaku setelah Tailscale diinstal ulang

Jalankan kembali:

```powershell
tailscale funnel --bg 8010
tailscale funnel status
```

Gunakan URL terbaru yang ditampilkan.

### Perubahan `.env` tidak diterapkan

Tutup FPOS sepenuhnya, simpan `.env`, lalu buka kembali aplikasi. Konfigurasi dibaca ketika aplikasi mulai berjalan.

## 10. Checklist serah-terima

### Paket dan data

- [ ] FPOS dipasang di `C:\FPOS`, bukan folder OneDrive.
- [ ] Paket tidak membawa database, kunci, log, backup, atau upload toko lain.
- [ ] `ipos.db` dan `secret.key` baru berhasil dibuat.
- [ ] Database laptop baru benar-benar terpisah dari laptop lama.

### Keamanan

- [ ] Password administrator bawaan sudah diganti.
- [ ] Password akun `Fieter` sudah kuat dan unik.
- [ ] Akun yang tidak dipakai sudah dinonaktifkan/dihapus.
- [ ] Funnel baru diaktifkan setelah pengamanan akun selesai.
- [ ] URL publik hanya diberikan kepada pihak yang berkepentingan.

### Tailscale dan koneksi

- [ ] Tailscale terpasang dan login pada laptop server.
- [ ] `tailscale status` berhasil.
- [ ] `tailscale funnel status` mengarah ke port lokal `8010`.
- [ ] URL HTTPS berhasil dibuka dari perangkat tanpa Tailscale.
- [ ] Login dan penyimpanan data berhasil diuji.

### Operasional

- [ ] Auto-start FPOS sudah aktif.
- [ ] FPOS dan Tailscale kembali berjalan setelah Windows restart.
- [ ] Prosedur backup database dan folder `uploads` sudah disiapkan.
- [ ] URL, lokasi aplikasi, dan penanggung jawab laptop sudah dicatat.
- [ ] Token agen printer dibuat untuk cabang yang benar dan disimpan hanya di komputer printer.
- [ ] Test Print selesai dengan status `done` dan cetak otomatis sudah diuji.
