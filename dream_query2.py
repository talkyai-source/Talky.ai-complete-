import sqlite3

DB = r'C:\Users\AL AZIZ TECH\.local\share\mimocode\mimocode.db'
db = sqlite3.connect(DB)
cur = db.cursor()

# Find Talky.ai project
print("=== ALL PROJECTS ===")
cur.execute("SELECT id, worktree, name FROM project")
for r in cur.fetchall():
    print(f"  id={r[0]} worktree={r[1]} name={r[2]}")

# Find sessions for Talky project (current workspace path contains Talky)
print("\n=== SESSIONS FOR Talky project (3673bc0b) ===")
cur.execute("""
    SELECT s.id, s.project_id, s.directory, s.title, s.time_created
    FROM session s
    WHERE s.project_id = '3673bc0b-f7bf-4bc0-8543-a0fabcd6e165'
    ORDER BY s.time_created DESC
    LIMIT 20
""")
for r in cur.fetchall():
    print(f"  {r[0]}: dir={r[3]} title={r[4]} time={r[5]}")

# Count messages for recent sessions
print("\n=== RECENT SESSION MESSAGE COUNTS (last 7 days) ===")
cur.execute("""
    SELECT s.id, s.title, s.project_id, s.time_created,
           COUNT(m.id) as msg_count
    FROM session s
    LEFT JOIN message m ON m.session_id = s.id
    WHERE s.time_created > ? 
    GROUP BY s.id
    ORDER BY s.time_created DESC
""")
import time
week_ago = int((time.time() - 7*86400) * 1000)
cur.execute("""
    SELECT s.id, s.title, s.project_id, s.time_created,
           COUNT(m.id) as msg_count
    FROM session s
    LEFT JOIN message m ON m.session_id = s.id
    WHERE s.time_created > ?
    GROUP BY s.id
    ORDER BY s.time_created DESC
""", (week_ago,))
for r in cur.fetchall():
    print(f"  {r[0]}: msgs={r[4]} project={str(r[2])[:8]}... title={r[1]} time={r[3]}")

db.close()
