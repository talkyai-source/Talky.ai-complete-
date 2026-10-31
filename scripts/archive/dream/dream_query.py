import sqlite3, json

DB = r'C:\Users\AL AZIZ TECH\.local\share\mimocode\mimocode.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
cur = db.cursor()

# Get project directories
print("=== PROJECTS ===")
cur.execute("SELECT id, directory FROM project")
for r in cur.fetchall():
    print(f"  {r['id']}: dir={r['directory']}")

# Find the current project (Talky.ai)
print("\n=== Looking for Talky.ai project ===")
cur.execute("SELECT id, directory FROM project WHERE directory LIKE '%Talky%' OR directory LIKE '%talky%'")
for r in cur.fetchall():
    print(f"  {r['id']}: dir={r['directory']}")

# List recent sessions (last 7 days)
print("\n=== RECENT SESSIONS (last 7 days) ===")
cur.execute("""
    SELECT s.id, s.project_id, s.directory, s.title, s.time_created
    FROM session s
    WHERE s.time_created > datetime('now', '-7 days')
    ORDER BY s.time_created DESC
    LIMIT 30
""")
for r in cur.fetchall():
    print(f"  {r['id']}: project={r['project_id'][:8]}... dir={r['directory']} title={r['title']} time={r['time_created']}")

# Count messages per session
print("\n=== MESSAGE COUNTS PER RECENT SESSION ===")
cur.execute("""
    SELECT s.id, s.title, s.project_id,
           COUNT(m.id) as msg_count,
           MIN(s.time_created) as start_time,
           MAX(m.time_created) as last_msg_time
    FROM session s
    LEFT JOIN message m ON m.session_id = s.id
    WHERE s.time_created > datetime('now', '-7 days')
    GROUP BY s.id
    ORDER BY s.time_created DESC
""")
for r in cur.fetchall():
    print(f"  {r['id']}: msgs={r['msg_count']} project={r['project_id'][:8]}... title={r['title']}")

db.close()
