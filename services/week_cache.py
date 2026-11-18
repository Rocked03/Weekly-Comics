import datetime as dt
import json
from dataclasses import asdict
from typing import List, Optional, Tuple

from asyncpg import Pool

from funcs.utils import from_dict
from objects.comic import Comic


def _json_default(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def serialize_comics(comics: List[Comic]) -> str:
    return json.dumps([asdict(c) for c in comics], default=_json_default)


def deserialize_comics(data: str) -> List[Comic]:
    comics = []
    for item in json.loads(data):
        item['releaseDate'] = dt.date.fromisoformat(item['releaseDate'])
        comics.append(from_dict(Comic, item))
    return comics


async def upsert_week(db: Pool, brand_id: str, week: dt.date, comics: List[Comic]):
    degraded = [str(c.id) for c in comics if c.synthesized]
    await db.execute(
        "INSERT INTO comic_cache (brand, week, comics, degraded_ids, fetched_at) "
        "VALUES ($1, $2, $3, $4, now()) "
        "ON CONFLICT (brand, week) DO UPDATE SET "
        "comics = EXCLUDED.comics, degraded_ids = EXCLUDED.degraded_ids, fetched_at = now()",
        brand_id, week, serialize_comics(comics), degraded
    )


async def fetch_week(db: Pool, brand_id: str, week: dt.date) -> Optional[Tuple[List[Comic], List[str]]]:
    row = await db.fetchrow(
        "SELECT comics, degraded_ids FROM comic_cache WHERE brand = $1 AND week = $2",
        brand_id, week
    )
    if row is None:
        return None
    return deserialize_comics(row['comics']), row['degraded_ids']
