from discord import app_commands, Interaction, TextStyle, Forbidden, Embed
from discord.ext import commands
from discord.ui import TextInput, Modal

from config import ADMIN_GUILD_IDS
from funcs.discord_functions import cmd_ping
from funcs.utils import is_owner
from objects.brand import Marvel
from objects.configuration import config_from_record


class UtilityCog(commands.Cog, name="Utility"):
    """Utility commands"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot


    @app_commands.command(name="broadcast")
    @app_commands.guilds(*ADMIN_GUILD_IDS or None)
    @app_commands.check(is_owner)
    async def broadcast(self, interaction: Interaction):
        """Opens a modal to broadcast a message to all servers."""
        class BroadcastModal(Modal, title="Broadcast Message"):
            header = TextInput(label="Header", required=False, max_length=256)
            message = TextInput(label="Message", style=TextStyle.paragraph, max_length=2000)

            async def on_submit(self, modal_interaction: Interaction):
                await modal_interaction.response.defer()

                embed = Embed(
                    title=self.header.value or None,
                    description=self.message.value,
                    color=Marvel().color,
                    timestamp=utils.utcnow()
                )
                bot = modal_interaction.client
                embed.set_footer(text=f"Broadcast from {bot.user.display_name}", icon_url=bot.user.display_avatar.url)

                con = await bot.db.fetch('SELECT * FROM configuration')
                configurations = [config_from_record(c) for c in con]
                channels = set(c.channel_id for c in configurations)

                n = 0
                for channel_id in channels:
                    channel = bot.get_channel(channel_id)
                    if channel is None:
                        continue
                    try:
                        await channel.send(embed=embed)
                        n += 1
                    except Forbidden:
                        print(f"Missing permissions in {channel.guild.name} ({channel.guild.id})")

                await modal_interaction.followup.send(
                    f"Broadcasted message to {n} channels (of {len(configurations)} configured channels).")

        await interaction.response.send_modal(BroadcastModal())


    @app_commands.command(name="about")
    async def about(self, interaction: Interaction):
        """Information about this bot."""
        embed = Embed(title="About", color=Marvel().color)
        embed.description = \
            "This bot was developed by **Rocked03#3304**. Originally created for the *Marvel Discord* " \
            "(https://discord.gg/Marvel), this bot was later expanded for public use with *Marvel* and *DC* feeds " \
            "available.\n\n" \
            f"To **set up** a feed in this server, use {cmd_ping(self.bot.cmds, 'setup')} (`Manage Server` required).\n\n" \
            "**Add this bot** to your own server: " \
            f"https://discordapp.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot&permissions={'18432'}"
        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="invite")
    async def invite(self, interaction: Interaction):
        """Invite this bot to your own server."""
        embed = Embed(title="Invite me!", color=Marvel().color)
        embed.description = \
            "**Add this bot** to your own server: " \
            f"https://discordapp.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot&permissions={'18432'}"
        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        await interaction.response.send_message(embed=embed)




async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))
