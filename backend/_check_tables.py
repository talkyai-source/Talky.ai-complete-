import asyncio, asyncpg, os
from dotenv import load_dotenv
load_dotenv()

async def main():
    try:
        dsn = os.getenv('DATABASE_URL', 'postgresql://talkyai:talkyai@localhost:5432/talkyai')
        conn = await asyncpg.connect(dsn)
        rows = await conn.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
        for r in rows:
            print(r['table_name'])
        await conn.close()
    except Exception as e:
        print('DB error:', e)

asyncio.run(main())
