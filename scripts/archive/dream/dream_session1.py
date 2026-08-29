import sqlite3, json

DB = r'C:\Users\AL AZIZ TECH\.local\share\mimocode\mimocode.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
cur = db.cursor()

# Session: Investigating call failure for campaign 50847cc9
SID = 'ses_07c051194ffesm33WOeaFym22s'
print(f"=== SESSION: {SID} ===")
cur.execute("""
    SELECT m.id, m.agent_id, json_extract(m.data, '$.role') as role,
           substr(m.data, 1, 300) as data_preview
    FROM message m
    WHERE m.session_id = ?
    ORDER BY m.time_created
""", (SID,))
for r in cur.fetchall():
    print(f"\n--- msg {r['id'][:20]} role={r['role']} agent={r['agent_id'] or 'main'} ---")

# Now get the parts for this session
cur.execute("""
    SELECT p.id, p.message_id, json_extract(p.data, '$.type') as part_type,
           json_extract(p.data, '$.text') as text,
           json_extract(p.data, '$.tool') as tool,
           substr(p.data, 1, 1500) as preview
    FROM part p
    WHERE p.session_id = ?
    ORDER BY p.time_created
""", (SID,))
for r in cur.fetchall():
    pt = r['part_type']
    if pt == 'text':
        txt = r['text'] or ''
        print(f"\n[TEXT] {txt[:500]}")
    elif pt == 'tool':
        print(f"\n[TOOL] {r['tool']}: {r['preview'][:300]}")
    elif pt == 'step-start':
        print(f"\n[STEP-START]")
    elif pt == 'step-finish':
        print(f"\n[STEP-FINISH] tokens={r['preview'][:100]}")
    else:
        print(f"\n[{pt}] {r['preview'][:200]}")

db.close()
