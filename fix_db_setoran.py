import sqlite3
import os

db_path = "backend/ipos.db"
if not os.path.exists(db_path):
    db_path = "ipos.db" # Fallback if run from backend folder

print(f"Mengupdate database: {db_path}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # 1. Tambah kolom di tabel shifts
    print("Mengecek kolom di tabel shifts...")
    cursor.execute("PRAGMA table_info(shifts)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "is_deposited" not in columns:
        print("Menambahkan kolom is_deposited ke tabel shifts...")
        cursor.execute("ALTER TABLE shifts ADD COLUMN is_deposited BOOLEAN DEFAULT 0")
    
    if "deposit_id" not in columns:
        print("Menambahkan kolom deposit_id ke tabel shifts...")
        cursor.execute("ALTER TABLE shifts ADD COLUMN deposit_id INTEGER REFERENCES branch_deposits(id)")

    # 2. Buat tabel branch_deposits
    print("Membuat tabel branch_deposits jika belum ada...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS branch_deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        branch_id INTEGER NOT NULL REFERENCES branches(id),
        date DATE NOT NULL,
        amount FLOAT NOT NULL,
        account_id INTEGER NOT NULL REFERENCES accounts(id),
        journal_id INTEGER REFERENCES journals(id),
        notes TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    print("✅ Database berhasil diperbarui!")
except Exception as e:
    print(f"❌ Error: {e}")
    conn.rollback()
finally:
    conn.close()
