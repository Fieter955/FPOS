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
