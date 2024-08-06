import datetime as dt
from enum import Enum

import discord
from asyncpg import Record, Pool
from discord import app_commands


class Brand(Enum):
    MARVEL = "Marvel"
    DC = "DC"


brand_autocomplete = [
    app_commands.Choice(name='Marvel', value='Marvel'),
    app_commands.Choice(name='DC', value='DC')
]

brand_colours = {
    Brand.MARVEL: 0xec1d24,
    Brand.DC: 0x0074e8
}

brand_links = {
    Brand.MARVEL: "Marvel.com",
    Brand.DC: "DC.com"
}

brand_default_days = {
    Brand.MARVEL: 1,
    Brand.DC: 4
}


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
                 format: Format = Format.SUMMARY,
                 day: int = 1,
                 ping: int = None,
                 pin: bool = False,
                 check_keywords: bool = False
                 ):
        self.server_id = server_id
        self.channel_id = channel_id
        self.format = format
        self.brand = brand
        self.day = day
        self.ping = ping
        self.pin = pin
        self.check_keywords = check_keywords

    def to_embed(self):
        embed = discord.Embed(
            title=f"{self.brand.value} Configuration",
            color=brand_colours[self.brand]
        )
        embed.add_field(name="Channel", value=f"<#{self.channel_id}>")
        embed.add_field(name="Format", value=f"{self.format.value}")
        embed.add_field(name="Next Scheduled Day", value=f"<t:{int(next_scheduled(self.day).timestamp())}:D>")
        embed.add_field(name="Ping Role", value=f"<@&{self.ping}>" if self.ping else None)
        embed.add_field(name="Channel Pin", value="Enabled" if self.pin else "Disabled")
        embed.set_footer(text=f"{self.server_id} · {self.brand.value}")
        return embed

    async def upload_to_sql(self, db: Pool):
        await db.execute(
            "INSERT INTO configuration " +
            "(server, brand, format, channel, day, ping, pin, check_key) " +
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            self.server_id, self.brand.name, self.format.name, self.channel_id, self.day, self.ping, self.pin,
            self.check_keywords
        )

    async def edit_sql(self, db: Pool):
        await db.execute(
            "UPDATE configuration " +
            "SET format = $3, channel = $4, day = $5, ping = $6, pin = $7, check_key = $8 " +
            "WHERE (server = $1 AND brand = $2)",
            self.server_id, self.brand.name,
            self.format.name, self.channel_id, self.day, self.ping, self.pin, self.check_keywords
        )

    async def delete_from_sql(self, db: Pool):
        await db.execute(
            "DELETE FROM configuration WHERE server = $1 AND brand = $2",
            self.server_id, self.brand.name
        )


def config_from_record(record: Record):
    return Configuration(
        record['server'],
        record['channel'],
        brand=Brand[record['brand']],
        format=Format[record['format']],
        day=record['day'],
        ping=record['ping'],
        pin=record['pin'],
        check_keywords=record['check_key']
    )


def next_scheduled(day: int):
    now = dt.datetime.utcnow().date()
    soon = now + dt.timedelta(days=(day - now.weekday()) % 7)
    time = dt.time(hour=1, minute=30)
    combined = dt.datetime.combine(soon, time, tzinfo=dt.timezone.utc)
    if combined < discord.utils.utcnow():
        combined += dt.timedelta(days=7)
    return combined


def prev_scheduled(day: int):
    return next_scheduled(day) - dt.timedelta(days=7)


weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
