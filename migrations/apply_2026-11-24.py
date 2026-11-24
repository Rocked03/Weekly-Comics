import asyncio
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import postgres_credentials

SQL_PATH = Path(__file__).parent / '2026-11-24-print-digital-formats.sql'


async def main():
    conn = await asyncpg.connect(**postgres_credentials)
    before = await conn.fetch('SELECT feed_id, disabled_formats FROM configuration ORDER BY feed_id')
    print('before:')
    for row in before:
        print(f"  feed {row['feed_id']}: {row['disabled_formats']}")
    await conn.execute(SQL_PATH.read_text())
    after = await conn.fetch('SELECT feed_id, disabled_formats FROM configuration ORDER BY feed_id')
    print('after:')
    for row in after:
        print(f"  feed {row['feed_id']}: {row['disabled_formats']}")
    await conn.close()


asyncio.run(main())
