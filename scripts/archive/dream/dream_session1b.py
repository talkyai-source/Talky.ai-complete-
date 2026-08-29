import sqlite3, json

DB = r'C:\Users\AL AZIZ TECH\.local\share\mimocode\mimocode.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
cur = db.cursor()

# Get the final text output from the explore agent and main agent in the call failure session
SID = 'ses_07c051194ffesm33WOeaFym22s'
print("=== CALL FAILURE SESSION - FINAL TEXT OUTPUTS ===")
cur.execute("""
    SELECT p.message_id, json_extract(p.data, '$.type') as part_type,
           json_extract(p.data, '$.text') as text,
           json_extract(m.data, '$.role') as role,
           m.agent_id
    FROM part p
    JOIN message m ON m.id = p.message_id
    WHERE p.session_id = ?
      AND json_extract(p.data, '$.type') = 'text'
      AND json_extract(p.data, '$.text') IS NOT NULL
      AND length(json_extract(p.data, '$.text')) > 100
    ORDER BY p.time_created
""", (SID,))
for r in cur.fetchall():
    txt = r['text']
    print(f"\n--- [{r['agent_id'] or 'main'}] {r['role']} ---")
    print(txt[:3000])
    if len(txt) > 3000:
        print(f"... ({len(txt)} chars total)")

db.close()
