# Panduan Uji Persediaan FPOS

Selalu lakukan pengujian pada salinan database, bukan database operasional.

## Persiapan

1. Tutup transaksi aktif atau ambil backup SQLite yang konsisten.
2. Jalankan `python backend/inventory_audit.py backend/backups/nama-salinan.db --json`.
3. Catat stok gudang, nilai FIFO, mutasi, dan jurnal sebelum pengujian.
4. Gunakan dua cabang, dua gudang, barang biasa, barang berseri, satuan turunan,
   dan minimal dua batch FIFO dengan harga berbeda.

## Checklist transaksi

| Fitur | Skenario utama | Hasil yang wajib |
|---|---|---|
| Mutasi Stok | Filter tanggal/tipe/barang; masuk, keluar, transfer, retur, reversal | Urutan benar, satu baris per kejadian, jumlah sebelum-sesudah benar, tidak bocor ke cabang lain |
| Stok Menipis | Stok di atas, sama, bawah minimum, nol, negatif, varian satuan | Tab, dashboard, laporan, dan notifikasi menghasilkan daftar serta jumlah yang sama |
| Item Masuk | Multi-item, pecahan, serial, gudang non-default, batal | Stok dan FIFO bertambah; mutasi dan jurnal tercatat; pembatalan mengembalikan semuanya |
| Item Keluar | Keluar melewati dua harga FIFO, stok pas/kurang, serial | FIFO tertua dipakai; stok kurang ditolak; reversal mengembalikan batch yang sama |
| Saldo Awal | Barang baru, pengulangan, modal nol, import | Hanya sekali per barang-gudang; jurnal ke Modal Transisi; duplikasi ditolak |
| Stok Opname | Surplus, susut, nol, tanpa selisih, token lama | Stok menjadi jumlah fisik; surplus memakai HPP sistem; susut memakai FIFO; perubahan bersamaan ditolak |
| Perbaikan Saldo | FIFO kurang/lebih, cache pusat salah, selisih nilai | Preview tidak menulis; token lama ditolak; hasil akhir stok=FIFO dan nilai buku=nilai FIFO |

## Pemeriksaan setelah setiap transaksi

- `GET /inventory/fifo-drift` harus menghasilkan `count: 0`.
- `GET /inventory/value-drift` harus menghasilkan `seimbang: true`.
- Total debit dan kredit jurnal harus sama.
- Gudang dan cabang yang tidak dipilih tidak boleh berubah.
- Bila salah satu baris atau jurnal gagal, tidak boleh ada dokumen, mutasi, FIFO,
  serial, maupun stok yang tersimpan sebagian.
- Pembatalan harus memulihkan keadaan persis, atau ditolak sebelum data berubah.

## Urutan regression test

```powershell
Set-Location backend
python -m unittest discover -s tests -p "test_*.py"
Set-Location ..
$testFiles = Get-ChildItem -LiteralPath tests -Filter *.test.mjs | ForEach-Object { $_.FullName }
node --test $testFiles
npm run test:inventory-e2e
npm run build
```

Untuk setiap bug, simpan bukti: data awal, langkah reproduksi, hasil aktual,
hasil seharusnya, data akhir, dan regression test yang sebelumnya gagal.
