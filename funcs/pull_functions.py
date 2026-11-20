from typing import Tuple, Dict, Union

from discord import Embed, Message
from discord.ext.commands import Bot

from comic_types.brand import Brand
from funcs.utils import week_of_date, f_date
from objects.comic import ComicMessage, Comic
from objects.configuration import Configuration


def clamp(text: str, limit: int) -> str:
    """Truncate text to limit, replacing the final character with an ellipsis if needed."""
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


async def validate_config_accessibility(bot: Bot, config: Configuration) -> Tuple[bool, str]:
    """
    Check if the bot can access and send messages to a configuration's channel.

    Returns:
        Tuple of (is_accessible, reason_if_not)
    """
    guild = bot.get_guild(config.server_id)
    if guild is None:
        return False, "Guild not found"

    channel = bot.get_channel(config.channel_id)
    if channel is None:
        return False, "Channel not found"

    perms = channel.permissions_for(guild.me)
    if not perms.send_messages:
        return False, "Missing permission: Send Messages"
    if not perms.embed_links:
        return False, "Missing permission: Embed Links"

    return True, ""


async def summary_embed(
        order: list[int],
        comics: Dict[int, Union[Comic, ComicMessage]],
        brand: Brand,
        start: Message = None):
    empty_embed = Embed(color=brand.color)

    embeds = []
    embed = empty_embed.copy()
    currently_issues = True
    n = 0
    for cid in order:
        if cid not in comics:
            continue

        comic = comics[cid]

        info = []
        if comic.writer:
            suffix = f" · [More]({comic.more})" if comic.url else ""
            limit = 1024 - len(suffix)
            writer = clamp(comic.writer, limit)
            if len(writer) < len(comic.writer):
                print(f"[Pull] Clamped field for '{comic.title}' ({len(comic.writer)} -> {limit})")
            info.append(writer)
        if comic.url:
            info.append(f"[More]({comic.more})")
        info_text = " · ".join(info) if info else "···"

        if (n == 24 or
                (comic.format != "Comic" and currently_issues) or
                len(embed) + len(comic.title) + len(info_text) > 6000):
            embeds.append(embed.copy())
            embed = empty_embed.copy()
            currently_issues = comic.format == "Comic"
            n = 0

        title = clamp(comic.title, 256)
        if len(title) < len(comic.title):
            print(f"[Pull] Clamped field for '{comic.title}' ({len(comic.title)} -> 256)")
        embed.add_field(name=title,
                        value=info_text,
                        inline=True)
        n += 1

    embeds.append(embed)

    date = week_of_date(list(comics.values()))
    embeds[0].title = f"{brand.name} Comics Releases Summary - {f_date(date)}"

    embed = empty_embed.copy()
    description = []
    if start:
        description.append(f"*Jump to the [beginning]({start.jump_url}).*")
    description.append(f"-# Data obtained from [League of Comic Geeks](https://leagueofcomicgeeks.com/).")
    embed.description = "\n".join(description)
    embeds.append(embed)

    return embeds
