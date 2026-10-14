"""
fix_db.py — Jalankan sekali: python fix_db.py
Menambahkan semua kolom yang kurang ke ipos.db tanpa hapus data.
"""
import sqlite3, os, sys

# Cari file ipos.db — coba beberapa lokasi umum
candidates = [
    "ipos.db",
    "backend/ipos.db",
    "../ipos.db",
    "backend/app/ipos.db",
]
db_path = None
for p in candidates:
    if os.path.exists(p):
        db_path = p
        break

if not db_path:
    # Tanya user
    db_path = input("Ketik path lengkap ke ipos.db: ").strip().strip('"')
    if not os.path.exists(db_path):
        print(f"File tidak ditemukan: {db_path}")
        sys.exit(1)

print(f"\n✓ Database ditemukan: {db_path}\n")
conn = sqlite3.connect(db_path)
c = conn.cursor()

def cols(table):
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    except:
        return []

def add(table, col, definition):
    if col not in cols(table):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            print(f"  ADDED  → {table}.{col}")
        except Exception as e:
            print(f"  ERROR  → {table}.{col}: {e}")
    else:
        print(f"  OK     → {table}.{col}")

print("=== sales ===")
add("sales", "salesperson_id", "INTEGER")
add("sales", "shift_id",       "INTEGER")
add("sales", "created_by",     "INTEGER")
add("sales", "change",         "REAL DEFAULT 0")
add("sales", "notes",          "TEXT")

print("\n=== purchases ===")
add("purchases", "notes",       "TEXT")
add("purchases", "created_by",  "INTEGER")
add("purchases", "tax_type",    "TEXT")

print("\n=== items ===")
add("items", "barcode",       "TEXT")
add("items", "description",   "TEXT")
add("items", "updated_at",    "TEXT")
add("items", "min_stock",     "REAL DEFAULT 0")
add("items", "is_active",     "INTEGER DEFAULT 1")
add("items", "brand_id",      "INTEGER")
add("items", "tracks_serial", "INTEGER DEFAULT 0")

print("\n=== customers ===")
add("customers", "credit_limit",    "REAL DEFAULT 0")
add("customers", "deposit_balance", "REAL DEFAULT 0")
add("customers", "loyalty_points",  "REAL DEFAULT 0")
add("customers", "points",          "REAL DEFAULT 0")
add("customers", "group_id",        "INTEGER")
add("customers", "address",         "TEXT")
add("customers", "email",           "TEXT")

print("\n=== suppliers ===")
add("suppliers", "PpnSupplier",  "REAL DEFAULT 0")
add("suppliers", "credit_limit",    "REAL DEFAULT 0")
add("suppliers", "due_date",      "INTEGER DEFAULT 0")
add("suppliers", "deposit_balance", "REAL DEFAULT 0")
add("suppliers", "email",        "TEXT")
add("suppliers", "notes",        "TEXT")
add("suppliers", "is_active",    "INTEGER DEFAULT 1")
add("suppliers", "code",         "TEXT")


print("\n=== warehouses ===")
add("warehouses", "is_default", "INTEGER DEFAULT 0")
add("warehouses", "is_active",  "INTEGER DEFAULT 1")
add("warehouses", "code",       "TEXT")

print("\n=== unit_conversions ===")
add("unit_conversions", "buy_price",  "REAL DEFAULT 0")
add("unit_conversions", "sell_price", "REAL DEFAULT 0")
add("unit_conversions", "is_active",  "INTEGER DEFAULT 1")

print("\n=== delivery_notes ===")
add("delivery_notes", "signed_at",      "TEXT")
add("delivery_notes", "recipient_name", "TEXT")
add("delivery_notes", "driver_name",    "TEXT")
add("delivery_notes", "vehicle_no",     "TEXT")
add("delivery_notes", "notes",          "TEXT")

print("\n=== trade_in_return_items ===")
add("trade_in_return_items", "returned_qty", "REAL DEFAULT 0")

print("\n=== licenses ===")
add("licenses", "max_users",   "INTEGER DEFAULT 3")
add("licenses", "owner_name",  "TEXT")
add("licenses", "owner_email", "TEXT")

print("\n=== shifts ===")
add("shifts", "total_sales",       "REAL DEFAULT 0")
add("shifts", "total_cash_sales",  "REAL DEFAULT 0")
add("shifts", "transaction_count", "INTEGER DEFAULT 0")
add("shifts", "notes",             "TEXT")
add("shifts", "difference",        "REAL")
add("shifts", "system_cash",       "REAL")
add("shifts", "closing_cash",      "REAL")

print("\n=== consignment_ins / consignment_outs ===")
for t in ["consignment_ins", "consignment_outs"]:
    add(t, "total_amount", "REAL DEFAULT 0")
    add(t, "status",       "TEXT DEFAULT 'active'")

print("\n=== stock_movements ===")
add("stock_movements", "description", "TEXT")
add("stock_movements", "reference",   "TEXT")
add("stock_movements", "warehouse_id", "INTEGER")
add("stock_movements", "counterparty_warehouse_id", "INTEGER")

print("\n=== inventory_document_lines ===")
add("inventory_document_lines", "entered_item_id", "INTEGER")
add("inventory_document_lines", "entered_qty", "REAL")
add("inventory_document_lines", "conversion_factor", "REAL DEFAULT 1")

print("\n=== warehouse_transfers ===")
add("warehouse_transfers", "cancelled_at", "DATETIME")
add("warehouse_transfers", "cancelled_by", "INTEGER")
add("warehouse_transfers", "cancellation_reason", "TEXT")
add("warehouse_transfers", "reversal_journal_id", "INTEGER")
add("warehouse_transfers", "can_cancel", "INTEGER DEFAULT 1")

print("\n=== warehouse_transfer_items ===")
add("warehouse_transfer_items", "entered_item_id", "INTEGER")
add("warehouse_transfer_items", "entered_qty", "REAL")
add("warehouse_transfer_items", "conversion_factor", "REAL DEFAULT 1")
add("warehouse_transfer_items", "serials_json", "TEXT")

print("\n=== purchase_items ===")
add("purchase_items", "disc3", "REAL DEFAULT 0")
add("purchase_items", "disc4", "REAL DEFAULT 0")
add("purchase_items", "serials_json", "TEXT")
add("purchase_items", "entered_item_id", "INTEGER")
add("purchase_items", "entered_qty", "REAL")
add("purchase_items", "conversion_factor", "REAL DEFAULT 1")

print("\n=== purchase_return_items ===")
add("purchase_return_items", "inventory_quarantine_id", "INTEGER")

print("\n=== sale_returns ===")
add("sale_returns", "reason", "TEXT")
add("sale_returns", "status", "TEXT DEFAULT 'posted'")
add("sale_returns", "cancelled_by", "INTEGER")
add("sale_returns", "cancelled_at", "DATETIME")
add("sale_returns", "cancellation_reason", "TEXT")
add("sale_returns", "reversal_journal_id", "INTEGER")

print("\n=== purchase_returns ===")
add("purchase_returns", "reason", "TEXT")
add("purchase_returns", "status", "TEXT DEFAULT 'posted'")
add("purchase_returns", "cancelled_by", "INTEGER")
add("purchase_returns", "cancelled_at", "DATETIME")
add("purchase_returns", "cancellation_reason", "TEXT")
add("purchase_returns", "reversal_journal_id", "INTEGER")

print("\n=== sale_items ===")
add("sale_items", "buy_price", "REAL DEFAULT 0")
add("sale_items", "serials_json", "TEXT")

print("\n=== return_items ===")
add("sale_return_items", "serials_json", "TEXT")
add("purchase_return_items", "serials_json", "TEXT")
add("sale_return_items", "carrying_cost", "REAL DEFAULT 0")
add("purchase_return_items", "carrying_cost", "REAL DEFAULT 0")
conn.commit()
conn.close()
print("\n✅ Selesai! Semua kolom sudah lengkap. Restart server FastAPI sekarang.\n")
