from discord import app_commands, Interaction, Embed
from discord.app_commands import checks
from discord.ext import commands

from funcs.discord_functions import cmd_ping
from objects.brand import Marvel
from objects.configuration import config_from_record
from objects.keywords import fetch_keywords, Types, sanitise, add_keyword, delete_keyword


class KeywordsCog(commands.Cog, name="Keywords"):
    """Manages keyword-based commands"""

    def __init__(self, bot):
        self.bot = bot

    kw_group = app_commands.Group(name="keywords", description="Filter your feeds by keyword.")

    @kw_group.command(name='view')
    @checks.has_permissions(manage_guild=True)
    async def kw_view(self, interaction: Interaction):
        """Lists your keywords that filter your feeds."""
        await interaction.response.defer()

        kw = await fetch_keywords(self.bot.db, interaction.guild_id)

        cons = await self.bot.db.fetch('SELECT * FROM configuration WHERE server = $1 AND check_key = $2',
                                       interaction.guild_id, True)
        configs = [config_from_record(c) for c in cons]

        e = Embed(title="Keywords", colour=Marvel().color)

        e.add_field(name="Keys (Title & Description)",
                    value=', '.join(f'`{i}`' for i in kw.keys) if kw.keys else "None")
        e.add_field(name="Creators", value=', '.join(f'`{i}`' for i in kw.creators) if kw.creators else "None")
        e.add_field(name="Feeds with keywords enabled",
                    value=', '.join(f'{c.brand.id} (<#{c.channel_id}>)' for c in
                                    configs) + f'\nEnable keywords with {cmd_ping(self.bot.cmds, "editfeed check-keywords")}',
                    inline=False)

        await interaction.followup.send(embed=e)

    @kw_group.command(name='add-key')
    @app_commands.describe(keyword="Keyword to check for in titles and descriptions.")
    @checks.has_permissions(manage_guild=True)
    async def kw_add_key(self, interaction: Interaction, keyword: str):
        """Add a keyword to be filter titles and descriptions."""
        await self.add_kw(interaction, keyword, Types.KEYS)

    @kw_group.command(name='add-creator')
    @app_commands.describe(keyword="Keyword to check for in creators.")
    @checks.has_permissions(manage_guild=True)
    async def kw_add_creator(self, interaction: Interaction, keyword: str):
        """Add a keyword to be filter creators."""
        await self.add_kw(interaction, keyword, Types.CREATORS)

    async def add_kw(self, interaction: Interaction, keyword: str, _type: Types):
        await interaction.response.defer()
        keyword = sanitise(keyword)
        success = await add_keyword(self.bot.db, interaction.guild_id, keyword, _type)

        if success:
            return await interaction.followup.send(f'Successfully added "{keyword}" to your keyword filter!')
        else:
            return await interaction.followup.send(f'"{keyword}" is already in your keyword filter!')

    @kw_group.command(name='delete-key')
    @app_commands.describe(keyword="Keyword to delete.")
    @checks.has_permissions(manage_guild=True)
    async def kw_delete_key(self, interaction: Interaction, keyword: str):
        """Delete a title/description filter keyword."""
        await self.delete_kw(interaction, keyword, Types.KEYS)

    @kw_group.command(name='delete-creator')
    @app_commands.describe(keyword="Keyword to delete.")
    @checks.has_permissions(manage_guild=True)
    async def kw_delete_creator(self, interaction: Interaction, keyword: str):
        """Delete a creator filter keyword."""
        await self.delete_kw(interaction, keyword, Types.CREATORS)

    async def delete_kw(self, interaction: Interaction, keyword: str, _type: Types):
        await interaction.response.defer()
        keyword = sanitise(keyword)
        success = await delete_keyword(self.bot.db, interaction.guild_id, keyword, _type)

        if success:
            return await interaction.followup.send(f'Successfully deleted "{keyword}" from your keyword filter!')
        else:
            return await interaction.followup.send(f'"{keyword}" is not in your keyword filter!')

    @kw_delete_key.autocomplete("keyword")
    async def kw_delete_autocomplete(self, interaction: Interaction, current: str):
        return await self.autocomplete_kw(interaction, current, _type=Types.KEYS)

    @kw_delete_creator.autocomplete("keyword")
    async def kw_delete_autocomplete(self, interaction: Interaction, current: str):
        return await self.autocomplete_kw(interaction, current, _type=Types.CREATORS)

    async def autocomplete_kw(self, interaction: Interaction, current: str, *, _type: Types):
        current = sanitise(current)
        kws = await self.bot.db.fetch('SELECT (keyword) FROM keywords WHERE server = $1 AND type = $2',
                                      interaction.guild_id, _type.value)

        kw = [i['keyword'] for i in kws if current in i['keyword']]
        kw.sort()
        kw.sort(key=lambda x: not x.startswith(current))

        return [app_commands.Choice(name=i, value=i) for i in kw][:25]

async def setup(bot):
    await bot.add_cog(KeywordsCog(bot))
