import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "ipos.db")

def cleanup_duplicate_items():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Identify duplicates and delete them, keeping the one with the MIN(id)
    print("Finding and deleting duplicate items...")
    cursor.execute('''
        SELECT name, COUNT(*) as c, MIN(id) 
        FROM items 
        GROUP BY name 
        HAVING c > 1
    ''')
    duplicates = cursor.fetchall()
    
    if duplicates:
        for dup in duplicates:
            print(f"Found duplicate for '{dup[0]}' (Keeping ID: {dup[2]}). Deleting others...")
            cursor.execute('''
                DELETE FROM items
                WHERE name = ? AND id != ?
            ''', (dup[0], dup[2]))
        
        print(f"Successfully deleted duplicates for {len(duplicates)} item names.")
    else:
        print("No duplicate items found.")

    # 2. Add UNIQUE index on the name column
    print("Applying UNIQUE index on the name column...")
    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_items_name_unique ON items (name)")
        print("UNIQUE index applied successfully.")
    except Exception as e:
        print(f"Error creating unique index: {e}")

    conn.commit()
    conn.close()
    print("Done!")

if __name__ == "__main__":
    cleanup_duplicate_items()