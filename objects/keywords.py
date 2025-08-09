from enum import Enum
from typing import List

from asyncpg import Record, Pool

from objects.comic import Comic


def sanitise(s: str):
    return s.upper().strip()


class Types(Enum):
    KEYS = 0
    CREATORS = 1


class Keywords:
    def __init__(self, server_id: int, keys: List[str] = None, creators: List[str] = None):
        if creators is None:
            creators = []
        if keys is None:
            keys = []
        self.server_id = server_id
        self.keys = keys
        self.creators = creators

    def check_comic(self, comic: Comic):
        header = sanitise((comic.title if comic.title else "") + " " + (comic.description if comic.description else ""))

        if any(sanitise(i) in header for i in self.keys):
            return True

        creators = []
        for v in comic.creators:
            creators.append(' '.join(v.name))

        if any(sanitise(i) in ' '.join(creators) for i in self.creators):
            return True

        return False


def keywords_from_records(records: List[Record], server_id: int):
    keywords = {
        Types.KEYS: [],
        Types.CREATORS: []
    }
    for r in records:
        if r['server'] == server_id:
            keywords[Types(r['type'])].append(r['keyword'])

    return Keywords(
        server_id,
        keywords[Types.KEYS],
        keywords[Types.CREATORS]
    )


async def fetch_keywords(db: Pool, server_id: int):
    kw = await db.fetch('SELECT * FROM keywords WHERE server = $1', server_id)
    return keywords_from_records(kw, server_id)


async def add_keyword(db: Pool, server_id: int, keyword: str, _type: Types):
    keyword = sanitise(keyword)
    kw = await db.fetch('SELECT * FROM keywords WHERE server = $1 AND keyword = $2 AND type = $3',
                        server_id, keyword, _type.value)
    if kw:
        return False

    await db.execute('INSERT INTO keywords (server, keyword, type) VALUES ($1, $2, $3)',
                     server_id, keyword, _type.value)
    return True


async def delete_keyword(db: Pool, server_id: int, keyword: str, _type: Types):
    keyword = sanitise(keyword)
    kw = await db.fetch('SELECT * FROM keywords WHERE server = $1 AND keyword = $2 AND type = $3',
                        server_id, keyword, _type.value)
    if not kw:
        return False

    await db.execute('DELETE FROM keywords WHERE server = $1 AND keyword = $2 AND type = $3',
                     server_id, keyword, _type.value)
    return True
