# FPOS License

Layanan pusat yang menyimpan lisensi seumur hidup untuk satu instalasi server FPOS.
API, dashboard Fieter, dan SQLite berjalan dalam satu container kecil.

## Railway

1. Buat service dari repository ini, set root directory ke `/fposLicense`, dan set
   config file path ke `/fposLicense/railway.toml`.
2. Tambahkan volume dengan mount path `/data`.
3. Isi seluruh secret dari `.env.example`, termasuk `FPOS_LICENSE_ENVIRONMENT=production`;
   jangan commit nilainya.
4. Generate domain, set healthcheck `/health`, pilih satu replica, lalu aktifkan Serverless.
5. Salin public key dari `GET /api/v1/public-key` ke konfigurasi setiap FPOS.

Database tidak boleh diletakkan di image atau environment variable. Backup volume secara
berkala dan ekspor file SQLite sebelum memindahkan layanan ke host Linux lain.

## Lokal

```powershell
docker compose up --build
```

Dashboard tersedia di `http://localhost:8080/admin` dan menggunakan HTTP Basic Auth.
