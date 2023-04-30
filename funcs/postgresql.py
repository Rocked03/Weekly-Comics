from discord.ext import commands
import asyncpg

from config import *


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
