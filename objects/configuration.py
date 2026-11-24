import datetime as dt
from enum import Enum

import asyncpg
from asyncpg import Record, Pool
from discord import app_commands, utils

from objects.brand import Brands
from comic_types.brand import Brand
from typing import List, Optional

COMIC_FORMATS = ['Print Comic', 'Digital Comic', 'Annual', 'Trade Paperback', 'Hardcover']

WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


class Format(Enum):
    FULL = "Full"
    COMPACT = "Compact"
    SUMMARY = "Summary"


format_autocomplete = [
    app_commands.Choice(name='Full', value='Full'),
    app_commands.Choice(name='Compact', value='Compact'),
    app_commands.Choice(name='Summary', value='Summary')
]


class Schedule(Enum):
    PRINT = "PRINT"
    DIGITAL_1M = "DIGITAL_1M"
    DIGITAL_3M = "DIGITAL_3M"
    DIGITAL_6M = "DIGITAL_6M"


schedule_autocomplete = [
    app_commands.Choice(name='Print', value='PRINT'),
    app_commands.Choice(name='Digital +1 Month', value='DIGITAL_1M'),
    app_commands.Choice(name='Digital +3 Months', value='DIGITAL_3M'),
    app_commands.Choice(name='Digital +6 Months', value='DIGITAL_6M'),
]

SCHEDULE_LABELS = {c.value: c.name for c in schedule_autocomplete}


class FeedNameInUse(Exception):
    """Raised when creating or renaming a feed to a name already used in the same server."""


def default_feed_name(brand: Brand, existing_names: List[str]) -> str:
    """Brand name if unused, else 'Brand (n)' with the smallest free n >= 2."""
    taken = {name.lower() for name in existing_names}
    if brand.name.lower() not in taken:
        return brand.name
    n = 2
    while f'{brand.name} ({n})'.lower() in taken:
        n += 1
    return f'{brand.name} ({n})'


class Configuration:
    def __init__(self,
                 server_id: int,
                 channel_id: int,
                 brand: Brand, *,
                 feed_id: Optional[int] = None,
                 name: Optional[str] = None,
                 _format: Format = Format.SUMMARY,
                 day: int = 1,
                 ping: Optional[int] = None,
                 pin: bool = False,
                 check_keywords: bool = False,
                 disabled_formats: Optional[List[str]] = None,
                 schedule: Schedule = Schedule.PRINT
                 ):
        self.server_id = server_id
        self.channel_id = channel_id
        self.feed_id = feed_id
        self.name = name
        self.format = _format
        self.brand = brand
        self.day = day
        self.ping = ping
        self.pin = pin
        self.check_keywords = check_keywords
        self.disabled_formats = disabled_formats if disabled_formats is not None else []
        self.schedule = schedule

    async def upload_to_sql(self, db: Pool):
        for attempt in range(2):
            async with db.acquire() as con:
                async with con.transaction():
                    feed_id = await con.fetchval(
                        'SELECT COALESCE(MAX(feed_id), 0) + 1 FROM configuration WHERE server = $1',
                        self.server_id)
                    try:
                        await con.execute(
                            "INSERT INTO configuration "
                            "(server, feed_id, brand, name, format, channel, day, ping, pin, check_key, "
                            "disabled_formats, schedule) "
                            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
                            self.server_id, feed_id, self.brand.id, self.name, self.format.name,
                            self.channel_id, self.day, self.ping, self.pin, self.check_keywords,
                            self.disabled_formats, self.schedule.value
                        )
                    except asyncpg.UniqueViolationError as e:
                        if attempt == 0 and e.constraint_name == 'configuration_pkey':  # pyright: ignore[reportAttributeAccessIssue]
                            continue  # rare feed_id race; retry with a fresh max
                        if e.constraint_name == 'configuration_server_name_key':  # pyright: ignore[reportAttributeAccessIssue]
                            raise FeedNameInUse(self.name) from e
                        raise  # other violations propagate to the editor
                    self.feed_id = feed_id
                    return

    async def edit_sql(self, db: Pool):
        try:
            await db.execute(
                "UPDATE configuration "
                "SET name = $3, format = $4, channel = $5, day = $6, ping = $7, pin = $8, check_key = $9, "
                "disabled_formats = $10, schedule = $11 "
                "WHERE (server = $1 AND feed_id = $2)",
                self.server_id, self.feed_id, self.name, self.format.name, self.channel_id, self.day,
                self.ping, self.pin, self.check_keywords, self.disabled_formats, self.schedule.value
            )
        except asyncpg.UniqueViolationError as e:
            if e.constraint_name == 'configuration_server_name_key':  # pyright: ignore[reportAttributeAccessIssue]
                raise FeedNameInUse(self.name) from e
            raise

    async def delete_from_sql(self, db: Pool):
        await db.execute(
            "DELETE FROM configuration WHERE server = $1 AND feed_id = $2",
            self.server_id, self.feed_id
        )


def config_from_record(record: Record):
    return Configuration(
        record['server'],
        record['channel'],
        brand=Brands()[record['brand']],
        feed_id=record['feed_id'],
        name=record['name'],
        _format=Format[record['format']],
        day=record['day'],
        ping=record['ping'],
        pin=record['pin'],
        check_keywords=record['check_key'],
        disabled_formats=record['disabled_formats'],
        schedule=Schedule(record['schedule'])
    )


def next_scheduled(day: int):
    now = utils.utcnow().date()
    soon = now + dt.timedelta(days=(day - now.weekday()) % 7)
    time = dt.time(hour=0, minute=0)
    combined = dt.datetime.combine(soon, time, tzinfo=dt.timezone.utc)
    if combined < utils.utcnow():
        combined += dt.timedelta(days=7)
    return combined


def prev_scheduled(day: int):
    return next_scheduled(day) - dt.timedelta(days=7)
