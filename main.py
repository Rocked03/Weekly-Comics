import discord
from discord.ext import commands

from config import BOT_PREFIX, TOKEN


class Zelma(commands.Bot):
    async def setup_hook(self):
        initial_extensions = [
            'funcs.postgresql',
            'owner',
            'pulls',
            'cogs.keywords',
            'cogs.edit_config',
            'cogs.utility'
        ]

        for extension in initial_extensions:
            await bot.load_extension(extension)


intents = discord.Intents.default()

description = "Weekly Comics"
bot = Zelma(
    command_prefix=commands.when_mentioned_or(BOT_PREFIX),
    description=description,
    intents=intents,
    max_messages=None)

bot.recent_cog = None

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
