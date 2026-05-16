import sqlite3
import os

db_path = "backend/ipos.db"
if not os.path.exists(db_path):
    print("Database not found!")
    exit(1)

conn = sqlite3.connect(db_path)
c = conn.cursor()

try:
    # Check if from_po_id already exists
    c.execute("PRAGMA table_info(purchases)")
    columns = [col[1] for col in c.fetchall()]
    
    if "from_po_id" in columns:
        print("Column from_po_id already exists in purchases table. Skipping.")
    else:
        print("Adding from_po_id column to purchases table...")
        c.execute("ALTER TABLE purchases ADD COLUMN from_po_id INTEGER REFERENCES purchases(id)")
        conn.commit()
        print("Column from_po_id added successfully.")

except Exception as e:
    conn.rollback()
    print(f"Migration failed: {e}")
finally:
    conn.close()
