"""
fix_db.py - Jalankan SEKALI: python fix_db.py
Menambah semua kolom yang kurang tanpa hapus data.
"""
import sqlite3, os, sys

candidates = ["ipos.db","backend/ipos.db","../ipos.db","backend/app/ipos.db"]
db_path = None
for p in candidates:
    if os.path.exists(p): db_path = p; break

if not db_path:
    db_path = input("Path ke ipos.db: ").strip().strip('"')
    if not os.path.exists(db_path):
        print("File tidak ditemukan:", db_path); sys.exit(1)

print(f"\n✓ Database: {db_path}\n")
conn = sqlite3.connect(db_path)
c = conn.cursor()

def cols(t):
    try: return [r[1] for r in c.execute(f"PRAGMA table_info({t})").fetchall()]
    except: return []

def tbl(t):
    return bool(c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone())

def add(table, col, defn):
    if col not in cols(table):
        try: c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}"); print(f"  ADDED  {table}.{col}")
        except Exception as e: print(f"  ERROR  {table}.{col}: {e}")
    else: print(f"  ok     {table}.{col}")

print("=== shifts ===")
add("shifts","status","TEXT DEFAULT 'open'")
add("shifts","total_sales","REAL DEFAULT 0")
add("shifts","total_cash_sales","REAL DEFAULT 0")
add("shifts","transaction_count","INTEGER DEFAULT 0")
add("shifts","notes","TEXT")
add("shifts","difference","REAL DEFAULT 0")
add("shifts","system_cash","REAL DEFAULT 0")
add("shifts","closing_cash","REAL DEFAULT 0")
add("shifts","user_name","TEXT")
# patch existing rows
try:
    c.execute("UPDATE shifts SET status='closed' WHERE closed_at IS NOT NULL AND (status IS NULL OR status='')")
    c.execute("UPDATE shifts SET status='open' WHERE closed_at IS NULL AND (status IS NULL OR status='')")
    print("  patched existing rows")
except: pass

print("\n=== sales ===")
add("sales","salesperson_id","INTEGER")
add("sales","shift_id","INTEGER")
add("sales","created_by","INTEGER")
add("sales","change","REAL DEFAULT 0")
add("sales","notes","TEXT")

print("\n=== sale_returns ===")
if tbl("sale_returns"):
    add("sale_returns","reason","TEXT")
    add("sale_returns","notes","TEXT")
    add("sale_returns","created_at","TEXT")
    add("sale_returns","total","REAL DEFAULT 0")

print("\n=== purchase_returns ===")
if tbl("purchase_returns"):
    add("purchase_returns","reason","TEXT")
    add("purchase_returns","notes","TEXT")
    add("purchase_returns","created_at","TEXT")
    add("purchase_returns","total","REAL DEFAULT 0")

print("\n=== purchases ===")
add("purchases","notes","TEXT")
add("purchases","created_by","INTEGER")

print("\n=== items ===")
add("items","barcode","TEXT")
add("items","description","TEXT")
add("items","updated_at","TEXT")
add("items","min_stock","REAL DEFAULT 0")
add("items","is_active","INTEGER DEFAULT 1")

print("\n=== customers ===")
add("customers","credit_limit","REAL DEFAULT 0")
add("customers","loyalty_points","REAL DEFAULT 0")
add("customers","points","REAL DEFAULT 0")
add("customers","group_id","INTEGER")
add("customers","address","TEXT")
add("customers","email","TEXT")
add("customers","total_purchase","REAL DEFAULT 0")

print("\n=== suppliers ===")
add("suppliers","credit_limit","REAL DEFAULT 0")
add("suppliers","email","TEXT")
add("suppliers","notes","TEXT")
add("suppliers","is_active","INTEGER DEFAULT 1")
add("suppliers","code","TEXT")

print("\n=== warehouses ===")
add("warehouses","is_default","INTEGER DEFAULT 0")
add("warehouses","is_active","INTEGER DEFAULT 1")
add("warehouses","code","TEXT")
add("warehouses","location","TEXT")

print("\n=== unit_conversions ===")
add("unit_conversions","buy_price","REAL DEFAULT 0")
add("unit_conversions","sell_price","REAL DEFAULT 0")
add("unit_conversions","is_active","INTEGER DEFAULT 1")
add("unit_conversions","conversion_factor","REAL DEFAULT 1")
add("unit_conversions","unit_name","TEXT")
add("unit_conversions","item_id","INTEGER")

if tbl("delivery_notes"):
    print("\n=== delivery_notes ===")
    add("delivery_notes","signed_at","TEXT")
    add("delivery_notes","recipient_name","TEXT")
    add("delivery_notes","delivery_address","TEXT")
    add("delivery_notes","driver_name","TEXT")
    add("delivery_notes","vehicle_no","TEXT")
    add("delivery_notes","notes","TEXT")
    add("delivery_notes","status","TEXT DEFAULT 'pending'")
    add("delivery_notes","sale_id","INTEGER")

if tbl("consignment_ins"):
    print("\n=== consignment_ins ===")
    add("consignment_ins","total_amount","REAL DEFAULT 0")
    add("consignment_ins","status","TEXT DEFAULT 'active'")
    add("consignment_ins","notes","TEXT")

if tbl("consignment_outs"):
    print("\n=== consignment_outs ===")
    add("consignment_outs","total_amount","REAL DEFAULT 0")
    add("consignment_outs","status","TEXT DEFAULT 'active'")
    add("consignment_outs","notes","TEXT")
    add("consignment_outs","destination","TEXT")

if tbl("stock_movements"):
    print("\n=== stock_movements ===")
    add("stock_movements","description","TEXT")
    add("stock_movements","reference","TEXT")
    add("stock_movements","notes","TEXT")

if tbl("trade_ins"):
    print("\n=== trade_ins ===")
    add("trade_ins","return_subtotal","REAL DEFAULT 0")
    add("trade_ins","new_subtotal","REAL DEFAULT 0")
    add("trade_ins","difference","REAL DEFAULT 0")
    add("trade_ins","notes","TEXT")
    add("trade_ins","customer_id","INTEGER")

if tbl("licenses"):
    print("\n=== licenses ===")
    add("licenses","max_users","INTEGER DEFAULT 3")
    add("licenses","owner_name","TEXT")
    add("licenses","owner_email","TEXT")

conn.commit(); conn.close()
print("\n✅ Selesai! Restart server FastAPI.\n")
