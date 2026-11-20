from typing import Any

from discord import app_commands, Interaction
from discord.app_commands import checks
from discord.ext import commands

from funcs.discord_functions import cmd_ping
from funcs.postgresql import fetch_configs
from objects.config_editor import EditorState, build_editor_view


class EditConfigCog(commands.Cog, name="Edit Configuration"):
    """Command to edit feed configurations"""

    def __init__(self, bot: Any):
        self.bot = bot

    @app_commands.command(name="config")
    @checks.has_permissions(manage_guild=True)
    async def config(self, interaction: Interaction):
        """View and edit the feeds in this server."""
        assert interaction.guild_id is not None
        configs = await fetch_configs(self.bot.db, interaction.guild_id)

        if not configs:
            return await interaction.response.send_message(
                f"You have not set up any feeds yet in this server! Use {cmd_ping(self.bot.cmds, 'setup')} to set "
                f"one up!",
                ephemeral=True
            )

        state = EditorState(configs, sorted(configs)[0], guild_id=interaction.guild_id)
        view = build_editor_view(state, self.bot)
        await interaction.response.send_message(view=view, ephemeral=True)
        state.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(EditConfigCog(bot))
