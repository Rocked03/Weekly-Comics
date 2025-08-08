import traceback
from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, Greedy

from config import ADMIN_USER_IDS, ADMIN_GUILD_IDS


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ADMIN_USER_IDS


# noinspection GrazieInspection
class OwnerCog(commands.Cog, name="Owner"):
    """Owner commands"""

    def __init__(self, bot):
        self.bot = bot

        self.bot.cmds = {}
        self.bot.loop.create_task(self.load_cmds())

    async def load_cmds(self):
        cmds = await self.bot.tree.fetch_commands()
        self.bot.cmds = {i.name: i for i in cmds}

    async def cog_check(self, ctx):
        return await self.bot.is_owner(ctx.author)

    @commands.command()
    async def shutdown(self, ctx):
        """Shuts down the bot"""
        try:
            await ctx.reply("Shutting down...")
        except discord.Forbidden:
            await ctx.author.send("Shutting down...")

        print(f"Shutting down...")
        print(discord.utils.utcnow().strftime("%d/%m/%Y %I:%M:%S:%f"))

        await self.bot.close()

    @commands.group(name="cogs", aliases=["cog"])
    async def cogs(self, ctx):
        """Cog management"""
        return

    @cogs.command(name='load')
    async def load_cog(self, ctx, *, cog: str):
        """Loads cog. Remember to use dot path. e.g: cogs.owner"""
        try:
            await self.bot.load_extension(cog)
        except Exception as e:
            return await ctx.send(f'**ERROR:** {type(e).__name__} - {e}')
        else:
            await ctx.send(f'Successfully loaded `{cog}`.')
        print('---')
        print(f'{cog} was loaded.')
        print('---')

    @cogs.command(name='unload')
    async def unload_cog(self, ctx, *, cog: str):
        """Unloads cog. Remember to use dot path. e.g: cogs.owner"""
        try:
            await self.cancel_tasks(cog)
            await self.bot.unload_extension(cog)
        except Exception as e:
            return await ctx.send(f'**ERROR:** {type(e).__name__} - {e}')
        else:
            await ctx.send(f'Successfully unloaded `{cog}`.')
        print('---')
        print(f'{cog} was unloaded.')
        print('---')

    @cogs.command(name='reload')
    async def reload_cog(self, ctx, *, cog: str):
        """Reloads cog. Remember to use dot path. e.g: cogs.owner"""
        try:
            await self.cancel_tasks(cog)
            await self.bot.reload_extension(cog)
        except Exception as e:
            return await ctx.send(f'**ERROR:** {type(e).__name__} - {e}')
        else:
            await ctx.send(f'Successfully reloaded `{cog}`.')
        self.bot.recent_cog = cog
        print('---')
        print(f'{cog} was reloaded.')
        print('---')

    @commands.command(hidden=True, aliases=['crr'])
    async def cog_recent_reload(self, ctx):
        """Reloads most recent reloaded cog"""
        if not self.bot.recent_cog:
            return await ctx.send("You haven't recently reloaded any cogs.")

        return await ctx.invoke(self.reload_cog, cog=self.bot.recent_cog)

    @commands.command()
    @commands.guild_only()
    async def sync(self, ctx: Context, guilds: Greedy[discord.Object],
                   spec: Optional[Literal["~", "*", "^"]] = None) -> None:
        """
        Works like:
        !sync -> global sync
        !sync ~ -> sync current guild
        !sync * -> copies all global app commands to current guild and syncs
        !sync ^ -> clears all commands from the current guild target and syncs (removes guild commands)
        !sync id_1 id_2 -> syncs guilds with id 1 and 2
        """
        if not guilds:
            try:
                if spec == "~":
                    synced = await ctx.bot.tree.sync(guild=ctx.guild)
                elif spec == "*":
                    ctx.bot.tree.copy_global_to(guild=ctx.guild)
                    synced = await ctx.bot.tree.sync(guild=ctx.guild)
                elif spec == "^":
                    ctx.bot.tree.clear_commands(guild=ctx.guild)
                    await ctx.bot.tree.sync(guild=ctx.guild)
                    synced = []
                else:
                    synced = await ctx.bot.tree.sync()

                await ctx.send(
                    f"Synced {len(synced)} commands {'globally' if spec is None else 'to the current guild.'}"
                )
                return
            except Exception:
                traceback.print_exc()
                await ctx.send("Something went wrong!")

        ret = 0
        for guild in guilds:
            try:
                await ctx.bot.tree.sync(guild=guild)
            except discord.HTTPException:
                pass
            else:
                ret += 1

        await ctx.send(f"Synced the tree to {ret}/{len(guilds)}.")
        await self.load_cmds()

    async def cancel_tasks(self, name):
        async def canceller(_self, task):
            try:
                _self.bot.tasks[task].cancel()
            except Exception:
                pass

        if name == 'cogs.comics':
            await canceller(self, 'releases')

        if name == 'cogs.polls':
            for x in ['starts', 'ends']:
                for k, v in self.bot.tasks['poll_schedules'][x].items():
                    try:
                        v.cancel()
                    except Exception:
                        pass

        if name == 'funcs.postgresql':
            try:
                await self.bot.db.close()
            except Exception:
                print("Couldn't close PostgreSQL connection")

    @app_commands.command()
    @app_commands.guilds(*ADMIN_GUILD_IDS or None)
    @app_commands.check(is_owner)
    async def guilds(self, interaction: discord.Interaction):
        guilds = sorted(self.bot.guilds, key=lambda x: x.name)

        texts = [f'{guild.name} `{guild.id}`' for guild in guilds]
        embed = discord.Embed(title=f"{len(self.bot.guilds)} total guilds")
        embed.description = '\n'.join(texts)

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(OwnerCog(bot))
