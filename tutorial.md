# Tutorial Cepat FPOS — Uji Coba

> Paket ini hanya untuk uji coba karena masih memakai login bawaan `admin/admin123`. Jangan gunakan untuk toko produksi atau menyimpan data penting.

## 1. Ekstrak ZIP

1. Klik kanan file ZIP FPOS.
2. Pilih **Extract All**.
3. Buka folder hasil ekstrak.
4. Pindahkan folder `FPOS` ke:

   ```text
   C:\FPOS
   ```

Jangan menjalankan aplikasi langsung dari dalam ZIP, flashdisk, atau folder OneDrive.

## 2. Instal Tailscale

1. Unduh Tailscale dari [https://tailscale.com/download/windows](https://tailscale.com/download/windows).
2. Instal Tailscale seperti aplikasi biasa.
3. Buka Tailscale dan login.
4. Pastikan statusnya **Connected**.

## 3. Jalankan FPOS

1. Klik dua kali:

   ```text
   C:\FPOS\FPOS.exe
   ```

2. Jika muncul pertanyaan auto-start, pilih **Yes** jika FPOS ingin otomatis berjalan saat Windows menyala.
3. Tunggu sampai jendela **Eva Store** terbuka.
4. Login dengan:

   ```text
   Username: admin
   Password: admin123
   ```

5. Biarkan FPOS tetap terbuka.

## 4. Aktifkan Tailscale pada port 8010

1. Klik **Start** Windows.
2. Cari `PowerShell`.
3. Klik kanan **Windows PowerShell**, lalu pilih **Run as Administrator**.
4. Jalankan perintah berikut:

   ```powershell
   tailscale status
   tailscale funnel --bg 8010
   tailscale funnel status
   ```

5. Jika browser membuka halaman persetujuan Funnel, setujui.
6. Salin URL yang muncul, misalnya:

   ```text
   https://nama-laptop.nama-tailnet.ts.net
   ```

URL tersebut digunakan untuk membuka FPOS dari perangkat lain.

## 5. Uji dari HP atau laptop lain

1. Buka URL `https://....ts.net` yang muncul tadi.
2. Login menggunakan `admin/admin123`.
3. Pastikan halaman FPOS dapat dibuka.

Laptop server harus tetap menyala, FPOS harus tetap berjalan, dan internet harus terhubung.

## Jika ada masalah

### PowerShell menampilkan `Access is denied`

Buka PowerShell menggunakan **Run as Administrator**.

### URL tidak dapat dibuka

Pastikan FPOS masih berjalan, lalu periksa port:

```powershell
Test-NetConnection 127.0.0.1 -Port 8010
```

Nilai `TcpTestSucceeded` harus `True`.

### Funnel tidak aktif

Jalankan kembali:

```powershell
tailscale funnel reset
tailscale funnel --bg 8010
tailscale funnel status
```

### FPOS gagal dibuka

Periksa:

```text
C:\FPOS\error_log.txt
```
