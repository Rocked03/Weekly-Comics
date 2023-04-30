import asyncio
import copy
import datetime
import functools
import random
from asyncio import Task
from io import BytesIO
from typing import Dict, List, Any, Optional
import datetime as dt

import discord
from discord import *
from discord.app_commands import *
from discord.app_commands.tree import _log
from discord.ext import commands
from marvel.marvel import Marvel  # pip install -U git+https://github.com/Rocked03/PyMarvel#egg=PyMarvel

from funcs.profile import load_image, Profile, imager_to_bytes
from objects.comic import Comic, ComicMessage
from config import marvelKey_public, marvelKey_private
from objects.configuration import Configuration, Format, Brand, brand_colours, config_from_record, format_autocomplete, \
    brand_autocomplete, weekdays, next_scheduled, brand_links
from crawlers.dc_crawler import dc_crawl
from crawlers.marvel_crawler import marvel_crawl
from objects.keywords import fetch_keywords, sanitise, add_keyword, Types, delete_keyword


async def on_app_command_error(interaction: Interaction, error: AppCommandError):
    if isinstance(error, app_commands.errors.CheckFailure):
        return await interaction.response.send_message(f"You need Manage Server permissions to use this command!",
                                                       ephemeral=True)

    await interaction.followup.send("Something broke!")
    _log.error('Ignoring exception in command %r', interaction.command.name, exc_info=error)


async def check_brand(brand: str, interaction: discord.Interaction = None):
    if brand is None:
        return None
    if brand not in [i.value for i in dict(Brand.__members__).values()]:
        if interaction: await interaction.followup.send("That is not a valid brand.")
        return False
    return Brand(brand)


def f_date(date: dt.datetime):
    return f"{date:%d %B %Y}".lstrip("0")


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id in [204778476102877187, 226595531844091904, 281991241812541440]


class PullsCog(commands.Cog, name="Pulls"):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.bot.tree.on_error = on_app_command_error

        self.comics: Dict[Brand, Dict[int, Comic]] = {}
        self.order = {b: [] for b in Brand}
        self.copyright = {b: None for b in Brand}
        self.date: Dict[Brand, Optional[dt.datetime]] = {b: None for b in Brand}

        self.access_lock = asyncio.Lock()
        self.locks: Dict[int, asyncio.Lock] = {}

        self.bot.loop.create_task(self.fetch_comics())

        self.feed_schedules: Dict[(int, Brand), Task] = {}
        self.bot.loop.create_task(self.on_startup_scheduler())

    def cmd_ping(self, cmd: str):
        first = cmd.split(' ')[0]
        return f"</{cmd}:{self.bot.cmds[first].id}>"

    async def check_lock(self, id_: int):
        async with self.access_lock:
            if id_ not in self.locks:
                self.locks[id_] = asyncio.Lock()

    async def on_startup_scheduler(self):
        while not self.bot.postgresql_loaded:
            await asyncio.sleep(0.1)
        self.bot.loop.create_task(self.schedule_feeds())
        self.bot.loop.create_task(self.schedule_crawl())
        self.bot.loop.create_task(self.schedule_pfp())
        self.bot.loop.create_task(self.schedule_activity())

    async def schedule_crawl(self):
        while not self.bot.is_closed():
            now = dt.datetime.utcnow().date()
            time = dt.datetime.combine(now, dt.time(0), tzinfo=dt.timezone.utc)
            time -= dt.timedelta(minutes=30)
            sleep_duration = time - discord.utils.utcnow()
            if sleep_duration.total_seconds() <= 0:
                sleep_duration += dt.timedelta(days=1)
            await asyncio.sleep(sleep_duration.total_seconds())

            await self.fetch_comics()

    async def schedule_pfp(self):
        while not self.comics:
            await asyncio.sleep(60)

        while not self.bot.is_closed():
            now = dt.datetime.utcnow().date()
            time = dt.datetime.combine(now, dt.time(0), tzinfo=dt.timezone.utc)
            time -= dt.timedelta(minutes=15)
            sleep_duration = time - discord.utils.utcnow()
            if sleep_duration.total_seconds() <= 0:
                sleep_duration += dt.timedelta(days=1)
            await asyncio.sleep(sleep_duration.total_seconds())

            await self.profile_pic()

    async def schedule_activity(self):
        while not self.comics:
            await asyncio.sleep(10)

        while not self.bot.is_closed():
            comics = []
            for v in self.comics.values():
                comics += [i.title for i in v.values()]

            title = random.choice(comics)
            a = discord.Activity(type=discord.ActivityType.watching, name=f"📖 {title}")

            await self.bot.change_presence(activity=a)

            await asyncio.sleep(random.randint(600, 3000))

    async def schedule_feeds(self):
        configs = await self.bot.db.fetch('SELECT * FROM configuration')

        for c in configs:
            self.schedule_feed(config_from_record(c))

    def schedule_feed(self, config: Configuration):
        self.feed_schedules[(config.server_id, config.brand)] = self.bot.loop.create_task(self.scheduler(config))

    async def scheduler(self, config: Configuration):
        time = next_scheduled(config.day)
        sleep_duration = time - discord.utils.utcnow()

        if sleep_duration.total_seconds() > 0:
            print(f"[Pull Feed Scheduler] ({config.server_id}, {config.brand.name}) Timer: {sleep_duration} ({time})")
            await asyncio.sleep(sleep_duration.total_seconds())

            print(
                f"[Pull Feed Scheduler] ({config.server_id}, {config.brand.name}) Executing. {discord.utils.utcnow()}")
            await self.send_comics(config)

        await self.scheduler(config)

    def cancel_feed(self, config: Configuration):
        self.feed_schedules[(config.server_id, config.brand)].cancel()
        print(f"[Pull Feed Scheduler] ({config.server_id}, {config.brand.name}) Cancelled.")

    async def pin(self, msg: discord.Message):
        try:
            pins = list(reversed(await msg.channel.pins()))
            if len(pins) >= 50:
                try:
                    p = next(i for i in pins if i.author.id == self.bot.user.id)
                    await p.unpin()
                except StopIteration:
                    return None
            await msg.pin()

            async for m in msg.channel.history(limit=1):
                await m.delete()

        except discord.Forbidden:
            pass

    async def fetch_comics(self):
        print(f"~~ Fetching comics ~~   {discord.utils.utcnow()}")

        print(" > Fetching Marvel")
        self.comics[Brand.MARVEL] = await marvel_crawl(Marvel(marvelKey_public, marvelKey_private))

        print(" > Fetching DC")
        self.comics[Brand.DC] = await dc_crawl()

        for b, c in self.comics.items():
            if c:
                self.date[b] = dt.datetime(1, 1, 1)
                for i in c.values():
                    if i.date > self.date[b]:
                        self.date[b] = i.date
            else:
                self.date[b] = None

            self.order[b] = sorted([k for k, v in c.items() if (self.date[b] - v.date) <= datetime.timedelta(days=7)],
                                   key=lambda x: c[x].title)

            if c:
                self.copyright[b] = c[self.order[b][0]].copyright

        print(f"~~ Comics fetched ~~   {discord.utils.utcnow()}")

    async def send_comics(self, config: Configuration):
        await self.check_lock(config.channel_id)
        async with self.locks[config.channel_id]:
            format = config.format

            channel = self.bot.get_channel(config.channel_id)

            comics: Dict[int, Comic | ComicMessage] = self.comics[config.brand].copy()

            if config.check_keywords:
                kw = await fetch_keywords(self.bot.db, config.server_id)
                comics = {k: v for k, v in comics.items() if kw.check_comic(v)}

            if comics:
                lead_msg = None
                if format in [Format.FULL, Format.COMPACT]:
                    lead_msg = await channel.send(f"## {config.brand.value} Comics - {f_date(self.date[config.brand])}")
                    if config.pin:
                        await self.pin(lead_msg)

                if config.ping:
                    await channel.send(f"<@&{config.ping}>")

                if format in [Format.FULL, Format.COMPACT]:
                    embeds = {k: c.to_embed(format == Format.FULL) for k, c in comics.items()}

                    instances = {}

                    for cid in self.order[config.brand]:
                        if cid in comics:
                            msg = await channel.send(embed=embeds[cid])
                            instances[cid] = comics[cid].to_instance(msg)

                    comics = instances

                summary_embeds = await self.summary_embed(comics, config.brand, lead_msg)

                summ_msg = await channel.send(embeds=summary_embeds)
                if config.pin and format == Format.SUMMARY:
                    await self.pin(summ_msg)

            else:
                await channel.send(f"There are no {config.brand.value} comics this week.")

    async def summary_embed(self, comics: Dict[int, Comic | ComicMessage], brand: Brand, start: discord.Message = None):
        empty_embed = discord.Embed(color=brand_colours[brand])

        embeds = []
        embed = empty_embed.copy()
        for n, cid in enumerate(self.order[brand]):
            if not n % 25:
                if n:
                    embeds.append(embed.copy())
                embed = empty_embed.copy()

            if cid in comics:
                c = comics[cid]

                info = []
                if c.writer():
                    info.append(f"{c.writer()}")
                if c.url:
                    info.append(f"[More]({c.more()})")

                embed.add_field(name=c.title,
                                value=" · ".join(info) if info else "···",
                                inline=True)
        embeds.append(embed)

        embeds[0].title = f"{brand.value} Comics Releases Summary - {f_date(self.date[brand])}"
        if self.copyright[brand]:
            embeds[-1].set_footer(text=self.copyright[brand])

        embed = empty_embed.copy()
        embed.set_footer(text=f"Data obtained by Rocked03#3304 from {brand_links[brand]}.")
        if start:
            embed.description = f"*Jump to the [beginning]({start.jump_url}).*"
        embeds.append(embed)

        return embeds

    async def profile_pic(self):
        m_ims = random.sample([i.image_url for i in self.comics[Brand.MARVEL].values() if i.image_url], 2)
        d_ims = random.sample([i.image_url for i in self.comics[Brand.DC].values() if i.image_url], 2)
        ims = [await load_image(i) for i in m_ims] + [await load_image(i) for i in d_ims]

        p = Profile(ims, 1200, 70, 300, 600,
                    bg=(255, 255, 255, 240),
                    round_corners=20)

        fp = functools.partial(imager_to_bytes, p)
        img: BytesIO = await self.bot.loop.run_in_executor(None, fp)

        await self.bot.user.edit(avatar=copy.copy(img).read())
        return img

    @app_commands.command(name="debug")
    @app_commands.check(is_owner)
    async def debug(self, interaction: discord.Interaction):
        """Debug command, ignore."""
        await interaction.response.defer()

        if not self.comics:
            return await interaction.followup.send("Comics are not yet fetched.")

        con = await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1 AND brand = $2',
            interaction.guild_id, Brand.MARVEL.name
        )

        for c in con:
            await self.send_comics(config_from_record(c))

        await interaction.followup.send("Done.")

    @app_commands.command(name="debug-profile")
    @app_commands.check(is_owner)
    async def debug_profile(self, interaction: discord.Interaction):
        """Debug command, ignore."""
        await interaction.response.defer()

        if not self.comics:
            return await interaction.followup.send("Comics are not yet fetched.")

        img = await self.profile_pic()
        await interaction.followup.send(file=discord.File(fp=img, filename="my_file.png"))


    async def fetch_raw_configs(self, server: int):
        return await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1', server
        )

    async def fetch_configs(self, server: int) -> Dict[Brand, Configuration]:
        configs: List[Configuration] = [config_from_record(i) for i in await self.fetch_raw_configs(server)]
        return {c.brand: c for c in configs}

    @app_commands.command(name="comics-this-week")
    @app_commands.choices(brand=brand_autocomplete)
    async def comics_this_week(self, interaction: discord.Interaction, brand: str):
        """Lists this week's comics!"""
        await interaction.response.defer(
            ephemeral=not interaction.channel.permissions_for(interaction.user).embed_links)
        b = Brand(brand)

        if b not in self.comics:
            return await interaction.followup.send("Comics are not yet fetched.")

        embeds = await self.summary_embed(self.comics[b], b)
        await interaction.followup.send(embeds=embeds)

    @app_commands.command(name="trigger-feed")
    @checks.has_permissions(manage_guild=True)
    @app_commands.choices(brand=brand_autocomplete)
    async def trigger_feed(self, interaction: discord.Interaction, brand: str):
        """Triggers your current feed configuration."""
        await interaction.response.defer()
        b = Brand(brand)

        if not self.comics:
            return await interaction.followup.send(
                "Comics are not yet fetched. Please wait a few moments and try again.")

        con = await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1 and brand = $2',
            interaction.guild_id, b.name
        )

        if not con:
            return await interaction.followup.send(
                f"You have not set up a {b.value} feed yet in this server! Use {self.cmd_ping('setup')} to set one up!")

        configs = [config_from_record(c) for c in con]
        for c in configs:
            await self.send_comics(c)

        await interaction.followup.send(
            f"Feed successfully triggered in {', '.join(f'<#{cc.channel_id}>' for cc in configs)}")

    @app_commands.command(name="setup")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        brand="The comic brand to receive a feed from.",
        channel="Channel to set up the feed. Leave empty to set up in THIS channel.",
        format="Feed format. Use /formats to view options. Summary is default."
    )
    @app_commands.choices(
        brand=brand_autocomplete,
        format=format_autocomplete
    )
    async def setup(self, interaction: discord.Interaction, brand: str, channel: discord.TextChannel = None,
                    format: str = "Summary"):
        """Sets up a comic pulls feed."""
        await interaction.response.defer()

        b = Brand(brand)
        f = Format(format)
        configs = await self.fetch_configs(interaction.guild_id)

        if channel is None: channel = interaction.channel

        if b in configs:
            return await interaction.followup.send("You have already set up a feed for this brand in this server.")

        new_config = Configuration(
            interaction.guild_id,
            channel.id,
            brand=b, format=f
        )

        await new_config.upload_to_sql(self.bot.db)

        self.schedule_feed(new_config)

        return await interaction.followup.send(
            f"Set up the following feed in **this** channel ({channel.mention}). \n" +
            f"To edit the feed settings, use these commands: \n" +
            " · ".join(self.cmd_ping(f"editfeed {i}") for i in ['channel', 'format', 'day', 'ping', 'pin']),
            embed=new_config.to_embed())

    @app_commands.command(name="config")
    @checks.has_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction):
        """Displays all configurations set in this server."""
        await interaction.response.defer()

        configs = await self.fetch_configs(interaction.guild_id)

        if not configs:
            return await interaction.followup.send(
                f"You have not set up any feeds yet in this server! Use {self.cmd_ping('setup')} to set one up!"
            )

        return await interaction.followup.send(embeds=[i.to_embed() for i in configs.values()])

    edit_group = app_commands.Group(name="editfeed", description="Edit the feeds in your server.")

    @edit_group.command(name="channel")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        channel="The channel to set the feed to.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.choices(brand=brand_autocomplete)
    async def config_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, brand: str = None):
        """Sets the channel of the feed. Must have Manage Server permissions."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'channel_id': channel.id})
        if txt:
            txt.append(f"Set channel to: {channel.mention}")
            await interaction.followup.send('\n'.join(txt))

    @edit_group.command(name="format")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        format="Feed format. Use /formats to view options.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.choices(
        brand=brand_autocomplete,
        format=format_autocomplete
    )
    async def config_format(self, interaction: discord.Interaction, format: str, brand: str = None):
        """Sets the format type of the feed."""
        await interaction.response.defer()

        f = Format(format)
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
        brand=brand_autocomplete,
        day=[app_commands.Choice(name=i, value=n) for n, i in enumerate(weekdays)]  # if n in [1, 2, 3]]
    )
    async def config_day(self, interaction: discord.Interaction, day: int, brand: str = None):
        """Sets the day of the weekly feed."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'day': day})
        if txt:
            txt.append(f"Set feed weekday to: {weekdays[day]}")
            await interaction.followup.send('\n'.join(txt))

    @edit_group.command(name="ping")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(
        ping="The role to ping when the feed is posted. Leave empty to clear. " +
             "This role must be pingable, or the bot must have @everyone perms in the channel.",
        brand="The brand feed to set. Leave empty to edit all feed configurations."
    )
    @app_commands.choices(brand=brand_autocomplete)
    async def config_ping(self, interaction: discord.Interaction, ping: discord.Role = None, brand: str = None):
        """Sets the role ping of the feed."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'ping': ping.id if ping else None})
        if txt:
            if ping:
                txt.append(f"Set role ping to:")
                e = discord.Embed(description=ping.mention)
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
    @app_commands.choices(brand=brand_autocomplete)
    async def config_pin(self, interaction: discord.Interaction, pin: bool, brand: str = None):
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
    @app_commands.choices(brand=brand_autocomplete)
    async def config_keywords(self, interaction: discord.Interaction, keywords: bool, brand: str = None):
        """Toggles filtering the feed by /keywords."""
        await interaction.response.defer()

        txt = await self.edit_config(interaction, brand, {'check_keywords': keywords})
        if txt:
            txt.append("Enabled keyword filter." if keywords else "Disabled keyword filter.")
            await interaction.followup.send('\n'.join(txt))

    async def edit_config(self, interaction: discord.Interaction, brand: str, attributes: Dict[str, Any]):
        b = Brand(brand) if brand else None

        configs = await self.fetch_configs(interaction.guild_id)
        if not configs:
            await interaction.followup.send(
                "You have not set up any feeds yet in this server! Use /commandhere to set one up!")
            return None
        filtered = sorted([v for k, v in configs.items() if b is None or k == b], key=lambda x: x.brand.value)
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

        return [f"Edited configuration(s): {', '.join(f'`{c.brand.value}`' for c in filtered)}"]

    @edit_group.command(name="delete-feed")
    @checks.has_permissions(manage_guild=True)
    @app_commands.describe(brand="The comic brand feed to delete.")
    @app_commands.choices(brand=brand_autocomplete)
    async def delete_feed(self, interaction: discord.Interaction, brand: str):
        """Deletes a feed. Dangerous!"""
        await interaction.response.defer()

        b = Brand(brand)
        configs = await self.fetch_configs(interaction.guild_id)

        if b not in configs:
            return await interaction.followup.send("You have not set up a feed for this brand in this server.")
        c = configs[b]

        await c.delete_from_sql(self.bot.db)

        self.cancel_feed(c)

        return await interaction.followup.send("**DELETED** the following feed:", embed=c.to_embed())

    @app_commands.command(name="formats")
    async def formats(self, interaction: discord.Interaction):
        """Lists the Format types available for feeds."""
        await interaction.response.defer(ephemeral=True)

        embeds = []

        comics = list(self.comics[Brand.MARVEL].values())
        samples = random.sample(comics, len(comics) if 4 > len(comics) else 4)

        meddle: Comic = copy.copy(random.choice(samples))
        meddle.creators = {"Writer": ["Rocked03"], "Artist": ["Rocked03"]}
        meddle.price = 99.99
        meddle.page_count = 99
        meddle.copyright = "This isn't a real comic (aside from the cover and links)."

        meddle.title = "Full Format"
        meddle.description = "The 'Full' Format lists all comics in an embed like this one, giving all details and a " \
                             "full-sized cover image, followed by the Summary embed."
        embeds.append(meddle.to_embed())

        meddle.title = "Compact Format"
        meddle.description = "The 'Compact' Format is similar to the 'Full', however the cover image is a small " \
                             "thumbnail, and some details (non-primary creators, etc.) are omitted for brevity. " \
                             "Also followed by the 'Summary' embed."
        embeds.append(meddle.to_embed(False))

        summaries = await self.summary_embed({i.id: i for i in samples}, Brand.MARVEL)
        summ = summaries[0]
        summ.title = "Summary Format"
        summ.insert_field_at(0, name="This displays all comics",
                             value="Each comic and author is listed as an easy summary, attached to all formats.")
        summ.insert_field_at(1, name="'More' jumps dynamically",
                             value="Sends you to higher embed if 'Full' or 'Compact', " +
                                   "or directly to the website if only 'Summary'")
        summ.set_footer(text="This is an abbreviated version of Summary - " +
                             "the real one often has around two dozen items.")
        embeds.append(summ)

        await interaction.followup.send(embeds=embeds)

    kw_group = app_commands.Group(name="keywords", description="Filter your feeds by keyword.")

    @kw_group.command(name='view')
    @checks.has_permissions(manage_guild=True)
    async def kw_view(self, interaction: discord.Interaction):
        """Lists your keywords that filter your feeds."""
        await interaction.response.defer()

        kw = await fetch_keywords(self.bot.db, interaction.guild_id)

        cons = await self.bot.db.fetch('SELECT * FROM configuration WHERE server = $1 AND check_key = $2',
                                       interaction.guild_id, True)
        configs = [config_from_record(c) for c in cons]

        e = discord.Embed(title="Keywords", colour=brand_colours[Brand.MARVEL])

        e.add_field(name="Keys (Title & Description)",
                    value=', '.join(f'`{i}`' for i in kw.keys) if kw.keys else "None")
        e.add_field(name="Creators", value=', '.join(f'`{i}`' for i in kw.creators) if kw.creators else "None")
        e.add_field(name="Feeds with keywords enabled",
                    value=', '.join(f'{c.brand.value} (<#{c.channel_id}>)' for c in
                                    configs) + f'\nEnable keywords with {self.cmd_ping("editfeed check-keywords")}',
                    inline=False)

        await interaction.followup.send(embed=e)

    @kw_group.command(name='add-key')
    @app_commands.describe(keyword="Keyword to check for in titles and descriptions.")
    @checks.has_permissions(manage_guild=True)
    async def kw_add_key(self, interaction: discord.Interaction, keyword: str):
        """Add a keyword to be filter titles and descriptions."""
        await self.add_kw(interaction, keyword, Types.KEYS)

    @kw_group.command(name='add-creator')
    @app_commands.describe(keyword="Keyword to check for in creators.")
    @checks.has_permissions(manage_guild=True)
    async def kw_add_creator(self, interaction: discord.Interaction, keyword: str):
        """Add a keyword to be filter creators."""
        await self.add_kw(interaction, keyword, Types.CREATORS)

    async def add_kw(self, interaction: discord.Interaction, keyword: str, type: Types):
        await interaction.response.defer()
        keyword = sanitise(keyword)
        success = await add_keyword(self.bot.db, interaction.guild_id, keyword, type)

        if success:
            return await interaction.followup.send(f'Successfully added "{keyword}" to your keyword filter!')
        else:
            return await interaction.followup.send(f'"{keyword}" is already in your keyword filter!')

    @kw_group.command(name='delete-key')
    @app_commands.describe(keyword="Keyword to delete.")
    @checks.has_permissions(manage_guild=True)
    async def kw_delete_key(self, interaction: discord.Interaction, keyword: str):
        """Delete a title/description filter keyword."""
        await self.delete_kw(interaction, keyword, Types.KEYS)

    @kw_group.command(name='delete-creator')
    @app_commands.describe(keyword="Keyword to delete.")
    @checks.has_permissions(manage_guild=True)
    async def kw_delete_creator(self, interaction: discord.Interaction, keyword: str):
        """Delete a creator filter keyword."""
        await self.delete_kw(interaction, keyword, Types.CREATORS)

    async def delete_kw(self, interaction: discord.Interaction, keyword: str, type: Types):
        await interaction.response.defer()
        keyword = sanitise(keyword)
        success = await delete_keyword(self.bot.db, interaction.guild_id, keyword, type)

        if success:
            return await interaction.followup.send(f'Successfully deleted "{keyword}" from your keyword filter!')
        else:
            return await interaction.followup.send(f'"{keyword}" is not in your keyword filter!')

    @kw_delete_key.autocomplete("keyword")
    async def kw_delete_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.autocomplete_kw(interaction, current, type=Types.KEYS)

    @kw_delete_creator.autocomplete("keyword")
    async def kw_delete_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.autocomplete_kw(interaction, current, type=Types.CREATORS)

    async def autocomplete_kw(self, interaction: discord.Interaction, current: str, *, type: Types):
        current = sanitise(current)
        kws = await self.bot.db.fetch('SELECT (keyword) FROM keywords WHERE server = $1 AND type = $2',
                                      interaction.guild_id, type.value)

        kw = [i['keyword'] for i in kws if current in i['keyword']]
        kw.sort()
        kw.sort(key=lambda x: not x.startswith(current))

        return [app_commands.Choice(name=i, value=i) for i in kw][:25]

    @app_commands.command(name="about")
    async def about(self, interaction: discord.Interaction):
        """Information about this bot."""
        embed = discord.Embed(title="About", color=brand_colours[Brand.MARVEL])
        embed.description = \
            "This bot was developed by **Rocked03#3304**. Originally created for the *Marvel Discord* " \
            "(https://discord.gg/Marvel), this bot was later expanded for public use with *Marvel* and *DC* feeds " \
            "available.\n\n" \
            f"To **set up** a feed in this server, use {self.cmd_ping('setup')} (`Manage Server` required).\n\n" \
            "**Add this bot** to your own server: " \
            f"https://discordapp.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot&permissions={'18432'}"
        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="invite")
    async def invite(self, interaction: discord.Interaction):
        """Invite this bot to your own server."""
        embed = discord.Embed(title="Invite me!", color=brand_colours[Brand.MARVEL])
        embed.description = \
            "**Add this bot** to your own server: " \
            f"https://discordapp.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot&permissions={'18432'}"
        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(PullsCog(bot))
