import datetime as dt
from enum import Enum

import discord
from asyncpg import Record, Pool
from discord import app_commands, utils

from objects.brand import Brands
from comic_types.brand import Brand
from typing import List

COMIC_FORMATS = ['Comic', 'Annual', 'Digital Chapter', 'Trade Paperback', 'Hardcover']

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


class Configuration:
    def __init__(self,
                 server_id: int,
                 channel_id: int,
                 brand: Brand, *,
                 _format: Format = Format.SUMMARY,
                 day: int = 1,
                 ping: int = None,
                 pin: bool = False,
                 check_keywords: bool = False,
                 disabled_formats: List[str] = None
                 ):
        self.server_id = server_id
        self.channel_id = channel_id
        self.format = _format
        self.brand = brand
        self.day = day
        self.ping = ping
        self.pin = pin
        self.check_keywords = check_keywords
        self.disabled_formats = disabled_formats if disabled_formats is not None else []

    def to_embed(self):
        embed = discord.Embed(
            title=f"{self.brand.name} Configuration",
            color=self.brand.color
        )
        embed.add_field(name="Channel", value=f"<#{self.channel_id}>")
        embed.add_field(name="Format", value=f"{self.format.value}")
        embed.add_field(name="Next Scheduled Day", value=f"<t:{int(next_scheduled(self.day).timestamp())}:D>")
        embed.add_field(name="Ping Role", value=f"<@&{self.ping}>" if self.ping else None)
        embed.add_field(name="Channel Pin", value="Enabled" if self.pin else "Disabled")
        enabled_formats = [f for f in COMIC_FORMATS if f not in self.disabled_formats]
        embed.add_field(name="Formats", value=", ".join(enabled_formats) if enabled_formats else "None")
        embed.set_footer(text=f"{self.server_id} · {self.brand.name}")
        return embed

    async def upload_to_sql(self, db: Pool):
        await db.execute(
            "INSERT INTO configuration " +
            "(server, brand, format, channel, day, ping, pin, check_key, disabled_formats) " +
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            self.server_id, self.brand.id, self.format.name, self.channel_id, self.day, self.ping, self.pin,
            self.check_keywords, self.disabled_formats
        )

    async def edit_sql(self, db: Pool):
        await db.execute(
            "UPDATE configuration " +
            "SET format = $3, channel = $4, day = $5, ping = $6, pin = $7, check_key = $8, disabled_formats = $9 " +
            "WHERE (server = $1 AND brand = $2)",
            self.server_id, self.brand.id,
            self.format.name, self.channel_id, self.day, self.ping, self.pin, self.check_keywords,
            self.disabled_formats
        )

    async def delete_from_sql(self, db: Pool):
        await db.execute(
            "DELETE FROM configuration WHERE server = $1 AND brand = $2",
            self.server_id, self.brand.id
        )


def config_from_record(record: Record):
    return Configuration(
        record['server'],
        record['channel'],
        brand=Brands()[record['brand']],
        _format=Format[record['format']],
        day=record['day'],
        ping=record['ping'],
        pin=record['pin'],
        check_keywords=record['check_key'],
        disabled_formats=record['disabled_formats']
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
