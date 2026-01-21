import copy
import functools
import random
from io import BytesIO
from typing import Dict, List

from discord import Interaction, app_commands, Message, Forbidden, RateLimited, HTTPException
from discord.app_commands import AppCommandError
from discord.app_commands.tree import _log

from funcs.profile import load_image, Profile, imager_to_bytes
from objects.comic import Comic


async def on_app_command_error(interaction: Interaction, error: AppCommandError):
    if isinstance(error, app_commands.errors.CheckFailure):
        return await interaction.response.send_message(f"You need Manage Server permissions to use this command!",
                                                       ephemeral=True)

    await interaction.followup.send("Something broke!")
    _log.error('Ignoring exception in command %r', interaction.command.name, exc_info=error)


def cmd_ping(all_commands: Dict, command: str):
    first = command.split(' ')[0]
    return f"</{command}:{all_commands[first].id}>"


async def pin(bot_id: int, msg: Message):
    if msg.guild.id == 281648235557421056: print("pin start")
    try:
        pins = list(reversed(await msg.channel.pins()))
        if len(pins) >= 50:
            try:
                p = next(i for i in pins if i.author.id == bot_id)
                await p.unpin()
            except StopIteration:
                return None
        await msg.pin()
        if msg.guild.id == 281648235557421056: print("pinned")

        async for m in msg.channel.history(limit=1):
            await m.delete()

        if msg.guild.id == 281648235557421056: print("deleted pin message")

    except (Forbidden, RateLimited, HTTPException):
        pass


async def profile_pic(marvel_comics: List[Comic], dc_comics: List[Comic], bot) -> BytesIO:
    m_ims = random.sample([i.coverImage for i in marvel_comics if i.coverImage], 2)
    d_ims = random.sample([i.coverImage for i in dc_comics if i.coverImage], 2)
    ims = [await load_image(i) for i in m_ims] + [await load_image(i) for i in d_ims]

    p = Profile(ims, 1200, 70, 300, 600,
                bg=(255, 255, 255, 240),
                round_corners=20)

    fp = functools.partial(imager_to_bytes, p)
    img: BytesIO = await bot.loop.run_in_executor(None, fp)

    await bot.user.edit(avatar=copy.copy(img).read())
    return img
