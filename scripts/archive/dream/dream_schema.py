import sqlite3

DB = r'C:\Users\AL AZIZ TECH\.local\share\mimocode\mimocode.db'
db = sqlite3.connect(DB)
cur = db.cursor()

# List all tables
print("=== TABLES ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print(tables)

# Schema for each table
for t in tables:
    print(f"\n=== SCHEMA: {t} ===")
    cur.execute(f"PRAGMA table_info({t})")
    for r in cur.fetchall():
        print(f"  {r[1]} ({r[2]})")

db.close()
