"""Katalog hak akses FPOS.

Label dan kelompok mengikuti layar referensi iPos yang disimpan di folder
``hak_akses``. Baris yang belum mempunyai padanan aman di FPOS tetap dikirim ke
frontend, tetapi ditandai tidak tersedia agar tidak memberi kesan bahwa sebuah
checkbox sudah melindungi fitur yang sebenarnya belum ada.
"""

ACTION_LABELS = {
    "view": "Buka",
    "create": "Baru",
    "update": "Ubah",
    "delete": "Hapus",
    "lock_no_transaction": "Kunci NoTrans",
    "lock_date": "Kunci Tanggal",
    "show": "Tampil",
    "access": "Akses",
}

CRUD = ("view", "create", "update", "delete")
VIEW = ("view",)
SHOW = ("show",)
ACCESS = ("access",)


def _p(
    key,
    label,
    actions=CRUD,
    available=True,
    admin_only=False,
    admin_only_actions=(),
):
    return {
        "key": key,
        "label": label,
        "actions": list(actions),
        "available": available,
        "admin_only": admin_only,
        "admin_only_actions": list(admin_only_actions),
    }


PERMISSION_CATALOG = [
    {
        "key": "master",
        "label": "Master Data",
        "permissions": [
            _p("master.item", "Master Item"),
            _p("master.supplier", "Master Supplier"),
            _p("master.customer", "Master Pelanggan"),
            _p("master.salesperson", "Master Sales"),
            _p(
                "master.warehouse",
                "Data Dept/Gudang",
                admin_only_actions=("create", "update", "delete"),
            ),
            _p("master.item_datasheet", "DataSheet Item", VIEW, False),
            _p("master.stock_card", "Kartu Stok", VIEW),
            _p("master.discount_period", "Diskon Periode"),
            _p("master.promo_period", "Periode Promosi", VIEW, False),
            _p("master.barcode", "Barcode Item", VIEW),
            _p("master.show_cost_item", "Tampil Harga Pokok pada Master Data Item", VIEW, True, True),
            _p("master.show_cost_datasheet", "Tampil Harga Pokok pada DataSheet Item", VIEW, False),
            _p("master.show_sale_price_item", "Tampil Harga Jual pada Master Data Item", VIEW),
            _p("master.show_sale_price_search", "Tampil Harga Jual pada Pencarian Data Item", VIEW),
            _p("master.barcode_password", "Sandi Barcode", VIEW, False),
            _p("master.point_setting", "Setting Point", VIEW, False),
            _p("master.customer_group", "Group Pelanggan"),
            _p("master.region", "Wilayah dan Sub Wilayah", VIEW, False),
            _p("master.unit", "Satuan"),
            _p("master.type", "Jenis"),
            _p("master.bank", "Bank", CRUD, False),
            _p("master.brand", "Merek"),
            _p("master.shipping", "Ongkir", CRUD, False),
            _p("master.emoney", "Emoney", CRUD, False),
            _p("master.change_unit", "User Bisa Ubah Satuan Item", VIEW, False),
            _p("master.change_serial", "User Bisa Ubah Serial Item", VIEW, False),
        ],
    },
    {
        "key": "purchase",
        "label": "Pembelian",
        "permissions": [
            _p("purchase.order", "Pesanan Pembelian"),
            _p("purchase.transaction", "Pembelian"),
            _p("purchase.payable", "Bayar Hutang"),
            _p("purchase.return", "Retur Beli"),
            _p("purchase.price_history", "History Harga Beli", VIEW),
            _p("purchase.activate_price", "Aktif Harga Beli pada Pesanan Pembelian dan Pembelian", VIEW, False),
            _p("purchase.change_sale_price", "Ubah Harga Jual saat Pembelian", VIEW, False),
            _p("purchase.show_cost_search", "Tampil Harga Pokok pada Pencarian Item Pembelian dan Pesanan Pembelian", VIEW, True, True),
            _p("purchase.show_totals", "Pembelian: Tampil Harga, Potongan, Tax, Sub Total, Total", VIEW),
            _p("purchase.change_received", "Pesanan Pembelian: Dapat mengubah jumlah terima", VIEW),
            _p("purchase.order_show_totals", "Pesanan Pembelian: Tampil Harga, Potongan, Tax, Sub Total, Total", VIEW),
            _p("purchase.discount", "Aktif Potongan pada Pembelian", VIEW),
            _p("purchase.import_payment", "Modul Bayar Hutang: Dapat melakukan import data pembayaran", VIEW, False),
            _p("purchase.backdate_payment", "Modul Bayar Hutang: Dapat melakukan pembayaran transaksi backdate", VIEW, False),
        ],
    },
    {
        "key": "sales",
        "label": "Penjualan",
        "permissions": [
            _p("sales.order", "Pesanan Penjualan"),
            _p("sales.transaction", "Penjualan"),
            _p("sales.cashier", "Kasir"),
            _p("sales.trade_in", "Tukar Tambah"),
            _p("sales.receivable", "Bayar Piutang", admin_only=True),
            _p("sales.return", "Retur Jual", admin_only_actions=("update",)),
            _p("sales.commission", "Bayar Komisi Sales", CRUD, False),
            _p("sales.cash_drawer", "Kas Laci", VIEW),
            _p("sales.point_check", "Cek Point (Point Penjualan)", VIEW),
            _p("sales.take_point", "Modul Cek Point, Penjualan & Kasir: Aktif Ambil Point", VIEW, False),
            _p("sales.discount", "Aktif Potongan pada Pesanan Jual, Penjualan & Kasir", VIEW),
            _p("sales.cost_price", "Aktif Harga pada Pesanan Jual, Penjualan & Kasir", VIEW, True, True),
            _p("sales.manual_drawer", "Buka Cashdrawer Manual dari modul Penjualan & Kasir", VIEW, False),
            _p("sales.sale_price_history", "History Harga Jual", VIEW),
            _p("sales.tax_other_cost", "Tampil Kolom Pajak dan Biaya Lain", VIEW),
            _p("sales.cancel_detail", "Modul Kasir: Bisa batal, hapus detail item & transaksi", VIEW),
            _p("sales.change_capital", "Modul Kasir: Bisa menutup modal kasir", VIEW, False),
            _p("sales.pending", "Modul Kasir: Aktif Pending Kasir", VIEW, False),
            _p("sales.report", "Modul Kasir: Bisa Cetak Laporan Kasir", VIEW),
            _p("sales.continue_print", "Modul Kasir: Lanjut Cetak (Mode Cetak 1 Kali)", VIEW),
            _p("sales.minimum_profit", "Modul Kasir & Penjualan: Bisa Jual di bawah Keuntungan Minimal", VIEW, False),
            _p("sales.below_cost", "Modul Kasir & Penjualan: Bisa Jual di bawah Harga Pokok", VIEW, False),
            _p("sales.stock_item", "Modul Kasir: Harus Cetak Struk/Nota (Simpan+Cetak)", VIEW, False),
            _p("sales.credit_payment", "Modul Kasir: Aktif Pembayaran Kredit", VIEW),
            _p("sales.lock_report_date", "Kunci Tgl Modul & Tombol Laporan Daftar Kasir: Tanggal hanya bisa 1 hari", VIEW, False),
            _p("sales.lock_list_date", "Kunci Tgl Modul Daftar Penjualan: Tanggal hanya bisa 1 hari", VIEW, False),
            _p("sales.order_change_received", "Pesanan Penjualan: Dapat mengubah jumlah terima", VIEW, False),
            _p("sales.export_tax", "Ekspor CSV Faktur Pajak Keluaran", VIEW, False),
            _p("sales.import_receivable", "Modul Bayar Piutang: Dapat melakukan import data pembayaran", VIEW, False),
            _p("sales.backdate_receivable", "Modul Bayar Piutang: Dapat melakukan pembayaran transaksi backdate", VIEW, False),
            _p("sales.lock_sales_columns", "Kunci Kolom Sales 2,3,4 pada Transaksi", VIEW, False),
            _p("sales.lock_customer_column", "Kunci Kolom Pelanggan pada Modul Kasir", VIEW, False),
            _p("sales.lock_sales_edit", "Kunci Kolom Sales pada Transaksi", VIEW, False),
            _p("sales.mobile_price", "Terminal Harga (Mobile)", VIEW, False),
        ],
    },
    {
        "key": "assembly",
        "label": "Perakitan",
        "permissions": [
            _p("assembly.order", "Pesanan Perakitan"),
            _p("assembly.transaction", "Perakitan"),
            _p("assembly.finished_goods", "Proses Jadi"),
            _p("assembly.view_components", "Perakitan: Dapat melihat data komponen item rakitan", VIEW),
        ],
    },
    {
        "key": "inventory",
        "label": "Persediaan",
        "permissions": [
            _p("inventory.item_in", "Item Masuk"),
            _p("inventory.item_out", "Item Keluar"),
            _p("inventory.transfer", "Transfer Item"),
            _p("inventory.branch_transfer", "Transfer Item Beda Cabang"),
            _p("inventory.opening_stock", "Saldo Awal Item", VIEW),
            _p("inventory.stock_opname", "Stok Opname", ("view", "lock_date")),
            _p("inventory.serial", "Serial Manajemen", VIEW, False),
            _p("inventory.show_cost_in", "Tampil Harga Pokok pada Item Masuk", VIEW),
            _p("inventory.show_cost_out", "Tampil Harga Pokok Dasar pada Item Keluar", VIEW),
            _p("inventory.repair_balance", "Proses Perbaikan Saldo", VIEW, True, True),
            _p("inventory.export_transfer", "Export Import Transfer Item", VIEW, False),
            _p("inventory.export_transaction", "Export Import Transaksi", VIEW, False),
            _p("inventory.export_discount", "Export Import Diskon dan Promo", VIEW, False),
            _p("inventory.export_item", "Export Item", VIEW),
            _p("inventory.import_item", "Import Item", VIEW),
            _p("inventory.export_emoney", "Export Import E-Money", VIEW, False),
            _p("inventory.opname_delete", "Modul Opname: Bisa menghapus opname list", VIEW, False),
            _p("inventory.opname_show_stock", "Modul Opname: Tampil Jumlah Stok", VIEW),
        ],
    },
    {
        "key": "accounting",
        "label": "Akuntansi",
        "permissions": [
            _p("accounting.accounts", "Daftar Perkiraan / Akun"),
            _p("accounting.cash_in", "Kas Masuk"),
            _p("accounting.cash_out", "Kas Keluar"),
            _p("accounting.cash_transfer", "Kas Transfer"),
            _p("accounting.customer_deposit", "Deposit Pelanggan"),
            _p("accounting.supplier_deposit", "Deposit Ke Supplier"),
            _p("accounting.journal", "Jurnal"),
            _p("accounting.ledger", "Buku Besar", VIEW),
            _p("accounting.opening_balance", "Saldo Awal Perkiraan / Akun", VIEW),
            _p("accounting.account_setting", "Setting Akun", VIEW),
            _p("accounting.annual_process", "Proses Tahunan", VIEW, True, True),
            _p("accounting.cash_user_department", "Modul Kas: Tampil Data per User Departemen", VIEW, False),
            _p("accounting.cash_balance", "Modul Kas & Deposit: Tampil Saldo Kas Pada Transaksi", VIEW),
            _p("accounting.cash_flow_category", "Setting Kategori Arus Kas"),
        ],
    },
    {
        "key": "report",
        "label": "Laporan",
        "permissions": [
            _p("report.master", "Laporan Master", VIEW),
            _p("report.purchase", "Laporan Pembelian", VIEW),
            _p("report.sales", "Laporan Penjualan", VIEW),
            _p("report.assembly", "Laporan Perakitan", VIEW),
            _p("report.inventory", "Laporan Persediaan", VIEW),
            _p("report.payable", "Laporan Hutang", VIEW),
            _p("report.receivable", "Laporan Piutang", VIEW),
            _p("report.financial", "Laporan Keuangan", VIEW, True, True),
        ],
    },
    {
        "key": "menu",
        "label": "Tampilan Menu",
        "permissions": [
            _p("menu.master", "Tampil Menu Master", SHOW),
            _p("menu.purchase", "Tampil Menu Pembelian", SHOW),
            _p("menu.assembly", "Tampil Menu Perakitan", SHOW),
            _p("menu.sales", "Tampil Menu Penjualan", SHOW),
            _p("menu.inventory", "Tampil Menu Persediaan", SHOW),
            _p("menu.accounting", "Tampil Menu Akuntansi", SHOW),
            _p("menu.report", "Tampil Menu Laporan", SHOW),
            _p("menu.settings", "Tampil Menu Pengaturan", SHOW),
            _p("menu.direct_cashier", "Langsung Tampil Modul Kasir", SHOW),
            _p("menu.direct_purchase", "Langsung Tampil Modul Daftar Kasir", SHOW, False),
        ],
    },
    {
        "key": "settings",
        "label": "Pengaturan dan Lainnya",
        "permissions": [
            _p("settings.lock_account_change", "Kunci Ubah Akun Transaksi", ACCESS, False),
            _p("settings.user_management", "User Manajemen", ACCESS, True, True),
            _p("settings.mini_printer", "Mini Printer & Cust. Display", ACCESS, False),
            _p("settings.company", "Data Perusahaan", ACCESS, True, True),
            _p("settings.general", "Pengaturan Umum", ACCESS, True, True),
            _p("settings.period", "Pengaturan Periode", ACCESS, False),
            _p("settings.transaction_number", "Pengaturan Nomor Transaksi dan Master", ACCESS, False),
            _p("settings.import", "Import Data", ACCESS),
            _p("settings.export", "Export Data", ACCESS),
            _p("settings.show_stock_search", "Tampil Stok pada Semua modul Pencarian Item", ACCESS),
            _p("settings.backup", "Backup Database", ACCESS, True, True),
            _p("settings.restore", "Restore Database", ACCESS, True, True),
            _p("settings.database", "Pengaturan Database", ACCESS, False),
            _p("settings.empty_data", "Kosongkan Data", ACCESS, False),
            _p("settings.other_warehouse", "User Bisa Akses Dept/Gudang Lain", ACCESS),
            _p("settings.activity_log", "Akses Log Aktivitas", ACCESS, True, True),
            _p("settings.open_transaction", "Akses Tombol Buka Transaksi", ACCESS),
            _p("settings.open_modules", "Akses Daftar Modul Terbuka", ACCESS, False),
            _p("settings.health_analysis", "Analisa Kesalahan Sistem", ACCESS, False),
            _p("settings.auto_backup", "Pengaturan Auto Backup Database", ACCESS, True, True),
        ],
    },
    {
        "key": "ledger_access",
        "label": "Akses Akun Buku Besar",
        "permissions": [
            _p("ledger.all_accounts", "Bisa Akses Semua akun pada buku besar", ACCESS, False),
            _p("ledger.account_1", "Akun Akses 1", ACCESS, False),
            _p("ledger.account_2", "Akun Akses 2", ACCESS, False),
            _p("ledger.account_3", "Akun Akses 3", ACCESS, False),
            _p("ledger.account_4", "Akun Akses 4", ACCESS, False),
        ],
    },
]


PERMISSION_INDEX = {
    permission["key"]: permission
    for category in PERMISSION_CATALOG
    for permission in category["permissions"]
}

AVAILABLE_GRANTS = {
    (permission["key"], action)
    for permission in PERMISSION_INDEX.values()
    if permission["available"]
    for action in permission["actions"]
}
