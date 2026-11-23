from typing import Any

from discord import app_commands, Interaction
from discord.app_commands import checks
from discord.ext import commands

from funcs.postgresql import fetch_configs
from objects.keywords import fetch_keywords
from objects.keyword_editor import KeywordEditorState, build_keyword_editor_view


class KeywordsCog(commands.Cog, name="Keywords"):
    """Manages keyword-based commands"""

    def __init__(self, bot: Any):
        self.bot = bot

    @app_commands.command(name="keywords", description="Edit the keyword filter applied to your feeds.")
    @checks.has_permissions(manage_guild=True)
    async def keywords(self, interaction: Interaction):
        """Open the keyword filter editor."""
        assert interaction.guild_id is not None
        kw = await fetch_keywords(self.bot.db, interaction.guild_id)
        configs = await fetch_configs(self.bot.db, interaction.guild_id)

        checks_map = {}
        feed_labels = {}
        for feed_id, config in configs.items():
            checks_map[feed_id] = config.check_keywords
            channel = interaction.guild.get_channel(config.channel_id) if interaction.guild else None
            suffix = f'#{channel.name}' if channel else 'unknown channel'
            feed_labels[feed_id] = f'{config.name} — {suffix}'

        state = KeywordEditorState(guild_id=interaction.guild_id, keys=kw.keys, creators=kw.creators,
                                   checks=checks_map, feed_labels=feed_labels)
        view = build_keyword_editor_view(state, self.bot)
        await interaction.response.send_message(view=view, ephemeral=True)
        state.message = await interaction.original_response()


async def setup(bot):
    await bot.add_cog(KeywordsCog(bot))
