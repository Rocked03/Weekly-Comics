from typing import Dict, Any

from discord import app_commands, Interaction, TextChannel, Role, Embed
from discord.app_commands import checks
from discord.ext import commands

from funcs.discord_functions import cmd_ping
from funcs.postgresql import fetch_configs
from objects.brand import BrandAutocomplete
from objects.configuration import format_autocomplete, WEEKDAYS, Format


class EditConfigCog(commands.Cog, name="Edit Configuration"):
    """Commands to edit feed configurations"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    edit_group = app_commands.Group(name="editfeed", description="Edit the feeds in your server.")

    @app_commands.command(name="config")
    @checks.has_permissions(manage_guild=True)
    async def config(self, interaction: Interaction):
        """Displays all configurations set in this server."""
        await interaction.response.defer()

        configs = await fetch_configs(self.bot.db, interaction.guild_id)

        if not configs:
            return await interaction.followup.send(
                f"You have not set up any feeds yet in this server! Use {cmd_ping(self.bot.cmds, 'setup')} to set one "
                f"up!"
            )

        return await interaction.followup.send(embeds=[i.to_embed() for i in configs.values()])


    @edit_group.command(name="channel")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="The channel to set the feed to.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.choices(brand=BrandAutocomplete)
    async def config_channel(self, interaction: Interaction, channel: TextChannel, brand: str = None):
        """Sets the channel of the feed. Must have Manage Server permissions."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'channel_id': channel.id})
        if txt:
            txt.append(f"Set channel to: {channel.mention}")
            await interaction.followup.send('\n'.join(txt))

    @edit_group.command(name="format")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        _format="Feed format. Use /formats to view options.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.rename(_format="format")
    @app_commands.choices(
        brand=BrandAutocomplete,
        _format=format_autocomplete
    )
    async def config_format(self, interaction: Interaction, _format: str, brand: str = None):
        """Sets the format type of the feed."""
        await interaction.response.defer()

        f = Format(_format)
        txt = await self.edit_config(interaction, brand, {'format': f})
        if txt:
            txt.append(f"Set format to: {f.value}")
            await interaction.followup.send('\n'.join(txt))

    @edit_group.command(name="day")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        day="The day to set the weekly feed. " +
            "The day the feed rolls over to the next week varies between brands.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.choices(
        brand=BrandAutocomplete,
        day=[app_commands.Choice(name=i, value=n) for n, i in enumerate(WEEKDAYS)]  # if n in [1, 2, 3]]
    )
    async def config_day(self, interaction: Interaction, day: int, brand: str = None):
        """Sets the day of the weekly feed."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'day': day})
        if txt:
            txt.append(f"Set feed weekday to: {WEEKDAYS[day]}")
            await interaction.followup.send('\n'.join(txt))

    @edit_group.command(name="ping")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        ping="The role to ping when the feed is posted. Leave empty to clear. " +
             "This role must be pingable, or the bot must have @everyone perms in the channel.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.choices(brand=BrandAutocomplete)
    async def config_ping(self, interaction: Interaction, ping: Role = None, brand: str = None):
        """Sets the role ping of the feed."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'ping': ping.id if ping else None})
        if txt:
            if ping:
                txt.append(f"Set role ping to:")
                e = Embed(description=ping.mention)
                await interaction.followup.send('\n'.join(txt), embed=e)
            else:
                txt.append(f"Cleared role ping.")
                await interaction.followup.send('\n'.join(txt))

    @edit_group.command(name="pin")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        pin="Toggle whether to pin each week's listing in the channel.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.choices(brand=BrandAutocomplete)
    async def config_pin(self, interaction: Interaction, pin: bool, brand: str = None):
        """Toggles pinning the weekly feed. Bot must have MANAGE MESSAGE perms in the feed's channel."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'pin': pin})
        if txt:
            txt.append("Enabled channel pins." if pin else "Disabled channel pins.")
            await interaction.followup.send('\n'.join(txt))

    @edit_group.command(name="check-keywords")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        keywords="Toggle whether to filter the feed by /keywords.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.choices(brand=BrandAutocomplete)
    async def config_keywords(self, interaction: Interaction, keywords: bool, brand: str = None):
        """Toggles filtering the feed by /keywords."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'check_keywords': keywords})
        if txt:
            txt.append("Enabled keyword filter." if keywords else "Disabled keyword filter.")
            await interaction.followup.send('\n'.join(txt))

    async def edit_config(self, interaction: Interaction, brand: str, attributes: Dict[str, Any]):
        b = self.brands[brand] if brand else None

        configs = await fetch_configs(self.bot.db, interaction.guild_id)
        if not configs:
            await interaction.followup.send(
                "You have not set up any feeds yet in this server! Use /setup to set one up!")
            return None
        filtered = sorted([v for k, v in configs.items() if b is None or k == b.id], key=lambda x: x.brand.id)
        if not filtered:
            await interaction.followup.send(f"You have not set up a `{b.name}` feed yet in this server!")
            return None

        for c in filtered:
            for a, v in attributes.items():
                c.__setattr__(a, v)
            await c.edit_sql(self.bot.db)

            if "day" in attributes:
                self.cancel_feed(c)
                self.schedule_feed(c)

        return [f"Edited configuration(s): {', '.join(f'`{c.brand.name}`' for c in filtered)}"]

    @edit_group.command(name="delete-feed")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(brand="The comic brand feed to delete.")
    @app_commands.choices(brand=BrandAutocomplete)
    async def delete_feed(self, interaction: Interaction, brand: str):
        """Deletes a feed. Dangerous!"""
        await interaction.response.defer()

        b = self.brands[brand]
        configs = await fetch_configs(self.bot.db, interaction.guild_id)

        if b.id not in configs:
            return await interaction.followup.send("You have not set up a feed for this brand in this server.")
        c = configs[b.id]

        await c.delete_from_sql(self.bot.db)

        self.cancel_feed(c)

        return await interaction.followup.send("**DELETED** the following feed:", embed=c.to_embed())


async def setup(bot):
    await bot.add_cog(EditConfigCog(bot))
