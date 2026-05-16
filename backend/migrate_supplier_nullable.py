import sqlite3
import os

db_path = "backend/ipos.db"
if not os.path.exists(db_path):
    print("Database not found!")
    exit(1)

conn = sqlite3.connect(db_path)
c = conn.cursor()

try:
    # 1. Get current columns
    c.execute("PRAGMA table_info(purchases)")
    columns_info = c.fetchall()
    
    # Check if supplier_id is already nullable
    # 0|id|INTEGER|1||1 -> idx 3 is notnull
    for col in columns_info:
        if col[1] == 'supplier_id' and col[3] == 0:
            print("supplier_id is already nullable. Skipping.")
            conn.close()
            exit(0)

    print("Migrating purchases table to make supplier_id nullable...")

    # 2. Get full original schema to be safe
    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='purchases'")
    create_sql = c.fetchone()[0]
    
    # 3. Create new schema (manually for precision)
    # Based on PRAGMA output and SQLAlchemy model:
    # 0|id|INTEGER|1||1
    # 1|number|VARCHAR(50)|1||0
    # 2|date|DATE|1||0
    # 3|supplier_id|INTEGER|1||0  <-- CHANGE THIS TO 0
    # 4|subtotal|FLOAT|0||0
    # 5|discount|FLOAT|0||0
    # 6|tax|FLOAT|0||0
    # 7|total|FLOAT|0||0
    # 8|paid|FLOAT|0||0
    # 9|status|VARCHAR(20)|0||0
    # 10|notes|TEXT|0||0
    # 11|created_by|INTEGER|0||0
    # 12|created_at|DATETIME|0|CURRENT_TIMESTAMP|0
    # 13|branch_id|INTEGER|1||0
    # 14|is_branch_request|BOOLEAN|0||0
    # 15|target_branch_id|INTEGER|0||0

    new_create_sql = """
    CREATE TABLE purchases_new (
        id INTEGER NOT NULL, 
        number VARCHAR(50) NOT NULL, 
        date DATE NOT NULL, 
        supplier_id INTEGER, 
        subtotal FLOAT, 
        discount FLOAT, 
        tax FLOAT, 
        total FLOAT, 
        paid FLOAT, 
        status VARCHAR(20), 
        notes TEXT, 
        created_by INTEGER, 
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
        branch_id INTEGER NOT NULL, 
        is_branch_request BOOLEAN, 
        target_branch_id INTEGER, 
        PRIMARY KEY (id), 
        UNIQUE (number), 
        FOREIGN KEY(supplier_id) REFERENCES suppliers (id), 
        FOREIGN KEY(created_by) REFERENCES users (id), 
        FOREIGN KEY(branch_id) REFERENCES branches (id), 
        FOREIGN KEY(target_branch_id) REFERENCES branches (id)
    );
    """
    
    c.execute(new_create_sql)
    
    # 4. Copy data
    cols_commas = "id, number, date, supplier_id, subtotal, discount, tax, total, paid, status, notes, created_by, created_at, branch_id, is_branch_request, target_branch_id"
    c.execute(f"INSERT INTO purchases_new ({cols_commas}) SELECT {cols_commas} FROM purchases")
    
    # 5. Drop old table and rename
    c.execute("DROP TABLE purchases")
    c.execute("ALTER TABLE purchases_new RENAME TO purchases")
    
    # 6. Recreate index
    c.execute("CREATE INDEX ix_purchases_id ON purchases (id)")
    
    conn.commit()
    print("Migration successful! supplier_id is now nullable.")

except Exception as e:
    conn.rollback()
    print(f"Migration failed: {e}")
finally:
    conn.close()
