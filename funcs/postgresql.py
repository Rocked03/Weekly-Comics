from typing import Dict, List

from asyncpg import Pool
from discord.ext import commands
import asyncpg

from config import *
from objects.configuration import Configuration, config_from_record


async def fetch_raw_configs(db: Pool, server: int):
    return await db.fetch(
        'SELECT * FROM configuration WHERE server = $1', server
    )


async def fetch_configs(db: Pool, server: int) -> Dict[str, Configuration]:
    configs: List[Configuration] = [config_from_record(i) for i in await fetch_raw_configs(db, server)]
    return {c.brand.id: c for c in configs}


class PostgreSQLCog(commands.Cog, name="PostgreSQL"):
    """Loads PostgreSQL"""

    def __init__(self, bot):
        self.bot = bot

        self.credentials = postgres_credentials
        self.bot.loop.create_task(self.load_postgresql())

        self.bot.postgresql_loaded = False

    async def load_postgresql(self):
        self.bot.db = await asyncpg.create_pool(**self.credentials)
        self.bot.postgresql_loaded = True


async def setup(bot):
    await bot.add_cog(PostgreSQLCog(bot))
