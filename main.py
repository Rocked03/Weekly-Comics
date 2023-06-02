import discord
from discord.ext import commands

from config import *


class Zelma(commands.Bot):
    async def setup_hook(self):
        initial_extensions = [
            'funcs.postgresql',
            'owner',
            'pulls'
        ]

        for extension in initial_extensions:
            await bot.load_extension(extension)


intents = discord.Intents.default()
intents.message_content = True

description = "Weekly Comics"
bot = Zelma(
    command_prefix=lambda bot, message: BOT_PREFIX,
    description=description,
    intents=intents,
    max_messages=None)

bot.recentcog = None

bot.tasks = {}


@bot.event
async def on_connect():
    print('Loaded Discord')


@bot.event
async def on_ready():
    print('------')
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print(discord.utils.utcnow().strftime("%d/%m/%Y %I:%M:%S:%f"))
    print('------')


@bot.check
async def globally_block_dms(ctx):
    return ctx.guild is not None


bot.run(TOKEN, reconnect=True)
