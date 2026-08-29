import sqlite3, time

DB = r'C:\Users\AL AZIZ TECH\.local\share\mimocode\mimocode.db'
db = sqlite3.connect(DB)
cur = db.cursor()

TALKY_PROJECT = '3673bc0b-f7bf-4bc0-8543-a0fabcd6e165'

# Sessions for Talky project
print("=== SESSIONS FOR TALKY PROJECT ===")
cur.execute("""
    SELECT id, title, directory, time_created
    FROM session
    WHERE project_id = ?
    ORDER BY time_created DESC
""", (TALKY_PROJECT,))
sessions = cur.fetchall()
for s in sessions:
    print(f"  {s[0]}: title={s[1]} dir={s[2]} time={s[3]}")

# Message counts per Talky session
print("\n=== MESSAGE COUNTS ===")
for s in sessions:
    sid = s[0]
    cur.execute("SELECT COUNT(*) FROM message WHERE session_id = ?", (sid,))
    cnt = cur.fetchone()[0]
    print(f"  {sid}: {cnt} messages")

# Recent sessions (last 14 days) for context
print("\n=== ALL RECENT SESSIONS (14 days) ===")
two_weeks_ago = int((time.time() - 14*86400) * 1000)
cur.execute("""
    SELECT s.id, s.project_id, s.title, s.time_created,
           COUNT(m.id) as msg_count
    FROM session s
    LEFT JOIN message m ON m.session_id = s.id
    WHERE s.time_created > ?
    GROUP BY s.id
    ORDER BY s.time_created DESC
""", (two_weeks_ago,))
for r in cur.fetchall():
    print(f"  {r[0]}: msgs={r[4]} project={str(r[1])[:8]}... title={r[2]} time={r[3]}")

# Check current session
print("\n=== CURRENT SESSION CHECKPOINT FILES ===")
db.close()
