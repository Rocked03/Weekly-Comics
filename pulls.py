import asyncio
import copy
import datetime as dt
import functools
import random
import traceback
from asyncio import Task
from io import BytesIO
from typing import Dict, List, Any, Union, Tuple

from discord import Interaction, app_commands, utils, Activity, ActivityType, Message, Forbidden, Embed, File, \
    TextChannel, Role, TextStyle, RateLimited, HTTPException
from discord.app_commands import AppCommandError, checks
from discord.app_commands.tree import _log
from discord.ext import commands
from discord.ui import Modal, TextInput

from comic_types.brand import Brand
from comic_types.locg import ComicDetails
from config import ADMIN_USER_IDS, ADMIN_GUILD_IDS
from funcs.profile import load_image, Profile, imager_to_bytes
from objects.brand import Brands, BrandEnum, BrandAutocomplete, Marvel
from objects.comic import Comic, ComicMessage
from objects.configuration import Configuration, Format, config_from_record, format_autocomplete, \
    WEEKDAYS, next_scheduled
from objects.keywords import fetch_keywords, sanitise, add_keyword, Types, delete_keyword
from services.comic_releases import fetch_comic_releases_detailed


async def on_app_command_error(interaction: Interaction, error: AppCommandError):
    if isinstance(error, app_commands.errors.CheckFailure):
        return await interaction.response.send_message(f"You need Manage Server permissions to use this command!",
                                                       ephemeral=True)

    await interaction.followup.send("Something broke!")
    _log.error('Ignoring exception in command %r', interaction.command.name, exc_info=error)


async def check_brand(brand: str, interaction: Interaction = None):
    brands = Brands()
    if brand is None:
        return None
    if brand not in brands:
        if interaction:
            await interaction.followup.send("That is not a valid brand.")
        return False
    return brands[brand]


def f_date(date: dt.date):
    return f"{date:%d %B %Y}".lstrip("0")


def week_of_date(comics: List[ComicDetails]) -> dt.date:
    return min(c.releaseDate for c in comics if c.format == "Comic") if comics else dt.date.today()


def is_owner(interaction: Interaction) -> bool:
    return interaction.user.id in ADMIN_USER_IDS


class PullsCog(commands.Cog, name="Pulls"):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.bot.tree.on_error = on_app_command_error

        self.brands = Brands()

        self.comics: Dict[str, Dict[int, Comic]] = {}
        self.order: Dict[str, List[int]] = {b.id: [] for b in self.brands}

        self.access_lock = asyncio.Lock()
        self.locks: Dict[int, asyncio.Lock] = {}

        self.schedule_offsets: Dict[Tuple[int, str], float] = {}

        self.feed_schedules: Dict[(int, str), Task] = {}
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
            print(f"Starting scheduled crawl.")
            try:
                await self.fetch_comics()
            except Exception:
                traceback.print_exc()

            now = utils.utcnow()
            time = dt.datetime.combine(now, dt.time(0), tzinfo=dt.timezone.utc)
            time -= dt.timedelta(minutes=15)
            sleep_duration = time - utils.utcnow()
            while sleep_duration.total_seconds() <= 0:
                sleep_duration += dt.timedelta(days=1)
            print(f"Next crawl in {sleep_duration.total_seconds()}s (in {sleep_duration})")
            await asyncio.sleep(sleep_duration.total_seconds())
            print(f"Next crawl initiating:")

    async def schedule_pfp(self):
        while not self.comics:
            await asyncio.sleep(60)

        while not self.bot.is_closed():
            now = utils.utcnow()
            time = dt.datetime.combine(now, dt.time(0), tzinfo=dt.timezone.utc)
            time -= dt.timedelta(minutes=10)
            sleep_duration = time - utils.utcnow()
            if sleep_duration.total_seconds() <= 0:
                sleep_duration += dt.timedelta(days=1)
            await asyncio.sleep(sleep_duration.total_seconds())

            try:
                await self.profile_pic()
            except Exception as e:
                print(f"Error while updating profile picture: {e}")
                traceback.print_exc()
                return None

    async def schedule_activity(self):
        while not self.comics:
            await asyncio.sleep(10)

        while not self.bot.is_closed():
            comics = []
            for v in self.comics.values():
                comics += [i.title for i in v.values()]

            title = random.choice(comics)
            a = Activity(type=ActivityType.watching, name=f"📖 {title}")

            await self.bot.change_presence(activity=a)

            await asyncio.sleep(random.randint(600, 3000))

    async def validate_config_accessibility(self, config: Configuration) -> Tuple[bool, str]:
        """
        Check if the bot can access and send messages to a configuration's channel.

        Returns:
            Tuple of (is_accessible, reason_if_not)
        """
        guild = self.bot.get_guild(config.server_id)
        if guild is None:
            return False, "Guild not found"

        channel = self.bot.get_channel(config.channel_id)
        if channel is None:
            return False, "Channel not found"

        perms = channel.permissions_for(guild.me)
        if not perms.send_messages:
            return False, "Missing permission: Send Messages"
        if not perms.embed_links:
            return False, "Missing permission: Embed Links"

        return True, ""

    async def schedule_feeds(self):
        configs = await self.bot.db.fetch('SELECT * FROM configuration')
        all_configs = [config_from_record(c) for c in configs]

        await self.bot.wait_until_ready()

        # Filter out inaccessible configurations
        valid_configs = []
        inaccessible_configs = []

        for config in all_configs:
            is_accessible, reason = await self.validate_config_accessibility(config)
            if is_accessible:
                valid_configs.append(config)
            else:
                inaccessible_configs.append((config, reason))

        # Log inaccessible configurations
        if inaccessible_configs:
            print("[Pull Feed Scheduler] Inaccessible configurations:")
            for config, reason in inaccessible_configs:
                print(f"  - Server {config.server_id}, Brand {config.brand.name}: {reason}")

            guild_not_found = [str(config.server_id) for config, reason in inaccessible_configs if
                               reason == "Guild not found"]
            if guild_not_found:
                print(f"[Pull Feed Scheduler] Guild not found IDs: {' '.join(guild_not_found)}")

        # Group valid configs by day
        by_day: Dict[int, List[Configuration]] = {}
        for config in valid_configs:
            by_day.setdefault(config.day, []).append(config)

        # Calculate offsets for each day
        self.schedule_offsets: Dict[Tuple[int, str], float] = {}
        for day_configs in by_day.values():
            offsets = await self.calculate_schedule_offsets(day_configs)
            self.schedule_offsets.update(offsets)

        # Schedule all valid feeds
        for config in valid_configs:
            self.schedule_feed(config)

        print(f"[Pull Feed Scheduler] Scheduled {len(valid_configs)} feeds, "
              f"skipped {len(inaccessible_configs)} inaccessible")

    async def calculate_schedule_offsets(self, configs: List[Configuration]) -> Dict[Tuple[int, str], float]:
        """Calculate time offsets for configs to spread out execution."""
        # Configuration for timing (easily adjustable)
        COMPACT_INTERVAL = 0.5  # seconds between compact feeds
        SUMMARY_INTERVAL = 0.5  # seconds between summary feeds
        FULL_INTERVAL = 15.0  # seconds for full feeds

        # Sort by priority: format type, then server size (member count)
        def get_priority(cfg: Configuration) -> Tuple[int, int]:
            guild = self.bot.get_guild(cfg.server_id)
            member_count = guild.member_count if guild else 0

            # Lower number = higher priority
            format_priority = {
                Format.COMPACT: 0,
                Format.SUMMARY: 1,
                Format.FULL: 2
            }
            return format_priority.get(cfg.format, 3), -member_count

        sorted_configs = sorted(configs, key=get_priority)

        # Assign offsets
        offsets = {}
        current_offset = 0.0

        for config in sorted_configs:
            offsets[(config.server_id, config.brand.id)] = current_offset

            if config.format == Format.FULL:
                current_offset += FULL_INTERVAL
            elif config.format == Format.COMPACT:
                current_offset += COMPACT_INTERVAL
            else:  # SUMMARY
                current_offset += SUMMARY_INTERVAL

        return offsets

    def schedule_feed(self, config: Configuration):
        try:
            self.feed_schedules[(config.server_id, config.brand.id)] = self.bot.loop.create_task(self.scheduler(config))
        except AttributeError:
            pass

    async def scheduler(self, config: Configuration):
        # Get the base scheduled time
        base_time = next_scheduled(config.day)

        # Add the offset for this specific config (0.0 if not found)
        offset = self.schedule_offsets.get((config.server_id, config.brand.id), 0.0)
        scheduled_time = base_time + dt.timedelta(seconds=offset)

        sleep_duration = scheduled_time - utils.utcnow()

        if sleep_duration.total_seconds() > 0:
            print(f"[Pull Feed Scheduler] ({config.server_id}, {config.brand.name}) "
                  f"Timer: {sleep_duration} (offset: {offset:.1f}s)")
            await asyncio.sleep(sleep_duration.total_seconds())

        print(f"[Pull Feed Scheduler] ({config.server_id}, {config.brand.name}) Executing. {utils.utcnow()}")
        try:
            await self.send_comics(config)
        except KeyError:
            print(f"[Pull Feed Scheduler] ({config.server_id}, {config.brand.name}) KeyError, "
                  f"probably comics not fetched yet.")

        await self.scheduler(config)

    def cancel_feed(self, config: Configuration):
        self.feed_schedules[(config.server_id, config.brand.id)].cancel()
        print(f"[Pull Feed Scheduler] ({config.server_id}, {config.brand.name}) Cancelled.")

    async def pin(self, msg: Message):
        if msg.guild.id == 281648235557421056: print("pin start")
        try:
            pins = list(reversed(await msg.channel.pins()))
            if len(pins) >= 50:
                try:
                    p = next(i for i in pins if i.author.id == self.bot.user.id)
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

    async def fetch_comics(self):
        print(f"~~ Fetching comics ~~   {utils.utcnow()}")
        self.comics = {}
        self.order = {}

        for current_brand in self.brands:
            print(f" > Fetching {current_brand.name}")
            try:
                comics = await fetch_comic_releases_detailed(publisher=current_brand.locg_id)
                comic_dict = {comic.id: comic for comic in comics}
                self.comics[current_brand.id] = comic_dict
                self.sort_order(comic_dict, current_brand)
                date = week_of_date(comics)
                print(
                    f"   > {len(self.comics[current_brand.id])} loaded for the week of {f_date(date)} ")
            except Exception as e:
                print(f"   ! Error fetching {current_brand.name} comics: {e}")
                traceback.print_exc()

        print(f"~~ Comics fetched ~~   {utils.utcnow()}")

    def sort_order(self, comic_dict: Dict[int, ComicDetails], brand: Brand):
        format_order = ["Comic", "Trade Paperback", "Hardcover"]

        self.order[brand.id] = sorted(
            comic_dict.keys(),
            key=lambda x: (
                format_order.index(comic_dict[x].format) if comic_dict[x].format in format_order else len(format_order),
                comic_dict[x].title,
                comic_dict[x].releaseDate,
            )
        )

    async def send_comics(self, config: Configuration):
        await self.check_lock(config.channel_id)
        async with self.locks[config.channel_id]:
            _format = config.format

            channel = self.bot.get_channel(config.channel_id)
            if channel is None:
                print(f"Channel {config.channel_id} not found for {config.brand.name} feed in {config.server_id}.")
                return

            comics: Dict[int, Union[Comic, ComicMessage]] = self.comics[config.brand.id].copy()

            if config.check_keywords:
                kw = await fetch_keywords(self.bot.db, config.server_id)
                comics = {k: v for k, v in comics.items() if kw.check_comic(v)}

            try:
                if comics:
                    lead_msg = None
                    if _format in [Format.FULL, Format.COMPACT]:
                        date = week_of_date(list(comics.values()))
                        lead_msg = await channel.send(f"## {config.brand.name} Comics - {f_date(date)}")
                        if config.pin:
                            await self.pin(lead_msg)
                        if channel.guild.id == 281648235557421056: print("finished pin")

                    if config.ping:
                        await channel.send(f"<@&{config.ping}>")

                    if _format in [Format.FULL, Format.COMPACT]:
                        embeds = {k: c.to_embed(_format == Format.FULL) for k, c in comics.items()}

                        instances = {}

                        for cid in self.order[config.brand.id]:
                            if cid in comics:
                                try:
                                    msg = await channel.send(embed=embeds[cid])
                                    instances[cid] = comics[cid].to_instance(msg)
                                except Exception:
                                    pass

                        comics = instances

                    summary_embeds = await self.summary_embed(comics, config.brand, lead_msg)

                    embed_selection: List[Embed] = []
                    first_msg = None

                    for embed in summary_embeds:
                        if sum(len(e) for e in embed_selection) + len(embed) > 6000:
                            msg = await channel.send(embeds=embed_selection)
                            if first_msg is None:
                                first_msg = msg
                            embed_selection = []
                        embed_selection.append(embed)

                    if embed_selection:
                        msg = await channel.send(embeds=embed_selection)
                        if first_msg is None:
                            first_msg = msg

                    if config.pin and _format == Format.SUMMARY and first_msg:
                        await self.pin(first_msg)

                else:
                    await channel.send(f"There are no {config.brand.name} comics this week.")
            except Forbidden:
                print(f"Missing permissions in {channel.guild.name} ({channel.guild.id})")

    async def summary_embed(self, comics: Dict[int, Union[Comic, ComicMessage]], brand: Brand,
                            start: Message = None):
        empty_embed = Embed(color=brand.color)

        embeds = []
        embed = empty_embed.copy()
        currently_issues = True
        n = 0
        for cid in self.order[brand.id]:
            if cid not in comics:
                continue

            comic = comics[cid]

            info = []
            if comic.writer:
                info.append(f"{comic.writer}")
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

            embed.add_field(name=comic.title,
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

    async def profile_pic(self):
        m_ims = random.sample([i.coverImage for i in self.comics[BrandEnum.Marvel.value].values() if i.coverImage], 2)
        d_ims = random.sample([i.coverImage for i in self.comics[BrandEnum.DC.value].values() if i.coverImage], 2)
        ims = [await load_image(i) for i in m_ims] + [await load_image(i) for i in d_ims]

        p = Profile(ims, 1200, 70, 300, 600,
                    bg=(255, 255, 255, 240),
                    round_corners=20)

        fp = functools.partial(imager_to_bytes, p)
        img: BytesIO = await self.bot.loop.run_in_executor(None, fp)

        await self.bot.user.edit(avatar=copy.copy(img).read())
        return img

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Clean up configurations when the bot leaves a server."""
        try:
            # Fetch all configurations for this server
            configs = await self.bot.db.fetch(
                'SELECT * FROM configuration WHERE server = $1',
                guild.id
            )

            if not configs:
                return

            # Cancel all scheduled feeds and delete from database
            for record in configs:
                config = config_from_record(record)
                try:
                    self.cancel_feed(config)
                except (KeyError, AttributeError):
                    pass

            # Delete all configurations from database
            await self.bot.db.execute(
                'DELETE FROM configuration WHERE server = $1',
                guild.id
            )

            print(f"[Guild Remove] Left server {guild.id} ({guild.name}). "
                  f"Cleaned up {len(configs)} feed(s).")

        except Exception as e:
            print(f"[Guild Remove] Error cleaning up server {guild.id}: {e}")
            traceback.print_exc()

    @app_commands.command(name="debug")
    @app_commands.guilds(*ADMIN_GUILD_IDS or None)
    @app_commands.check(is_owner)
    async def debug(self, interaction: Interaction):
        """Debug command, dev-only."""
        await interaction.response.defer()

        if not self.comics:
            return await interaction.followup.send("Comics are not yet fetched.")

        con = await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1 AND brand = $2',
            interaction.guild_id, Marvel.id
        )

        for c in con:
            await self.send_comics(config_from_record(c))

        await interaction.followup.send("Done.")

    @app_commands.command(name="debug-profile")
    @app_commands.guilds(*ADMIN_GUILD_IDS or None)
    @app_commands.check(is_owner)
    async def debug_profile(self, interaction: Interaction):
        """Debug command, dev-only."""
        await interaction.response.defer()

        if not self.comics:
            return await interaction.followup.send("Comics are not yet fetched.")

        img = await self.profile_pic()
        await interaction.followup.send(file=File(fp=img, filename="my_file.png"))

    async def fetch_raw_configs(self, server: int):
        return await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1', server
        )

    async def fetch_configs(self, server: int) -> Dict[str, Configuration]:
        configs: List[Configuration] = [config_from_record(i) for i in await self.fetch_raw_configs(server)]
        return {c.brand.id: c for c in configs}

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

    @app_commands.command(name="comics-this-week")
    @app_commands.choices(brand=BrandAutocomplete)
    async def comics_this_week(self, interaction: Interaction, brand: str):
        """Lists this week's comics!"""
        await interaction.response.defer(
            ephemeral=not interaction.channel.permissions_for(interaction.user).embed_links)
        b = self.brands[brand]

        if b.id not in self.comics:
            return await interaction.followup.send("Comics are not yet fetched.")

        comics = self.comics[b.id]

        con = await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1 and brand = $2',
            interaction.guild_id, b.id
        )

        if con:
            config = config_from_record(con[0])
            if config.check_keywords:
                kw = await fetch_keywords(self.bot.db, config.server_id)
                comics = {k: v for k, v in comics.items() if kw.check_comic(v)}

        embeds = await self.summary_embed(comics, b)
        await interaction.followup.send(embeds=embeds)

    @app_commands.command(name="trigger-feed")
    @checks.has_permissions(manage_guild=True)
    @app_commands.choices(brand=BrandAutocomplete)
    async def trigger_feed(self, interaction: Interaction, brand: str):
        """Triggers your current feed configuration."""
        await interaction.response.defer()
        b = self.brands[brand]

        if b.id not in self.comics.keys():
            return await interaction.followup.send(
                "Comics are not yet fetched. Please wait a few moments and try again.")

        con = await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1 and brand = $2',
            interaction.guild_id, b.id
        )

        if not con:
            return await interaction.followup.send(
                f"You have not set up a {b.name} feed yet in this server! Use {self.cmd_ping('setup')} to set one up!")

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
        _format="Feed format. Use /formats to view options. Summary is default."
    )
    @app_commands.rename(_format="format")
    @app_commands.choices(
        brand=BrandAutocomplete,
        _format=format_autocomplete
    )
    async def setup(self, interaction: Interaction, brand: str, channel: TextChannel = None,
                    _format: str = "Summary"):
        """Sets up a comic pulls feed."""
        await interaction.response.defer()

        b = self.brands[brand]
        f = Format(_format)
        configs = await self.fetch_configs(interaction.guild_id)

        if channel is None:
            channel = interaction.channel

        if b.id in configs:
            return await interaction.followup.send("You have already set up a feed for this brand in this server.")

        new_config = Configuration(
            interaction.guild_id,
            channel.id,
            brand=b, _format=f, day=b.default_day
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
    async def config(self, interaction: Interaction):
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

        configs = await self.fetch_configs(interaction.guild_id)
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
        configs = await self.fetch_configs(interaction.guild_id)

        if b.id not in configs:
            return await interaction.followup.send("You have not set up a feed for this brand in this server.")
        c = configs[b.id]

        await c.delete_from_sql(self.bot.db)

        self.cancel_feed(c)

        return await interaction.followup.send("**DELETED** the following feed:", embed=c.to_embed())

    @app_commands.command(name="formats")
    async def formats(self, interaction: Interaction):
        """Lists the Format comic_types available for feeds."""
        await interaction.response.defer(ephemeral=True)

        embeds = []

        comics = list(self.comics[BrandEnum.Marvel.id].values())
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

        summaries = await self.summary_embed({i.id: i for i in samples}, self.brands.Marvel)
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
    async def kw_view(self, interaction: Interaction):
        """Lists your keywords that filter your feeds."""
        await interaction.response.defer()

        kw = await fetch_keywords(self.bot.db, interaction.guild_id)

        cons = await self.bot.db.fetch('SELECT * FROM configuration WHERE server = $1 AND check_key = $2',
                                       interaction.guild_id, True)
        configs = [config_from_record(c) for c in cons]

        e = Embed(title="Keywords", colour=Marvel.color)

        e.add_field(name="Keys (Title & Description)",
                    value=', '.join(f'`{i}`' for i in kw.keys) if kw.keys else "None")
        e.add_field(name="Creators", value=', '.join(f'`{i}`' for i in kw.creators) if kw.creators else "None")
        e.add_field(name="Feeds with keywords enabled",
                    value=', '.join(f'{c.brand.id} (<#{c.channel_id}>)' for c in
                                    configs) + f'\nEnable keywords with {self.cmd_ping("editfeed check-keywords")}',
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

    @app_commands.command(name="about")
    async def about(self, interaction: Interaction):
        """Information about this bot."""
        embed = Embed(title="About", color=Marvel().color)
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
    async def invite(self, interaction: Interaction):
        """Invite this bot to your own server."""
        embed = Embed(title="Invite me!", color=Marvel().color)
        embed.description = \
            "**Add this bot** to your own server: " \
            f"https://discordapp.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot&permissions={'18432'}"
        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(PullsCog(bot))
