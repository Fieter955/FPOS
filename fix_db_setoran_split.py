import sqlite3
import os

db_path = "backend/ipos.db"
if not os.path.exists(db_path):
    db_path = "ipos.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    print("Memperbarui tabel branch_deposits untuk dukungan Gabungan Kas & Bank...")
    
    # Cek kolom yang ada
    cursor.execute("PRAGMA table_info(branch_deposits)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "cash_amount" not in columns:
        cursor.execute("ALTER TABLE branch_deposits ADD COLUMN cash_amount FLOAT DEFAULT 0")
    if "bank_amount" not in columns:
        cursor.execute("ALTER TABLE branch_deposits ADD COLUMN bank_amount FLOAT DEFAULT 0")
    if "bank_account_id" not in columns:
        cursor.execute("ALTER TABLE branch_deposits ADD COLUMN bank_account_id INTEGER REFERENCES accounts(id)")

    conn.commit()
    print("✅ Database berhasil diperbarui!")
except Exception as e:
    print(f"❌ Error: {e}")
finally:
    conn.close()
