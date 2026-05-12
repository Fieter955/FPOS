import sqlite3
import os

db_path = "backend/ipos.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("--- RECENT JOURNAL ENTRIES ---")
cursor.execute("""
    SELECT j.id, j.number, j.description, j.branch_id,
           l.debit_account_id, l.credit_account_id, l.amount
    FROM journals j
    JOIN journal_entry_lines l ON j.id = l.journal_id
    ORDER BY j.id DESC LIMIT 10
""")

for row in cursor.fetchall():
    print(row)

print("\n--- ACCOUNT NORMAL BALANCES ---")
cursor.execute("""
    SELECT id, code, name, normal_balance FROM accounts WHERE code IN ('1-1100', '3-2300', '3-2400')
""")
for row in cursor.fetchall():
    print(row)

conn.close()
