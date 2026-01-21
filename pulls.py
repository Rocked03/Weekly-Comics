import asyncio
import copy
import datetime as dt
import random
import traceback
from asyncio import Task
from typing import Dict, List, Any, Union, Tuple

from discord import Interaction, app_commands, utils, Activity, ActivityType, Forbidden, Embed, File, \
    TextChannel, Role
from discord.app_commands import checks
from discord.ext import commands

from comic_types.brand import Brand
from comic_types.locg import ComicDetails
from config import ADMIN_GUILD_IDS
from funcs.utils import f_date, week_of_date, is_owner
from funcs.discord_functions import on_app_command_error, cmd_ping, pin, profile_pic
from funcs.pull_functions import validate_config_accessibility, summary_embed
from funcs.postgresql import fetch_configs
from objects.brand import Brands, BrandEnum, BrandAutocomplete, Marvel
from objects.comic import Comic, ComicMessage
from objects.configuration import Configuration, Format, config_from_record, format_autocomplete, \
    WEEKDAYS, next_scheduled
from objects.keywords import fetch_keywords
from services.comic_releases import fetch_comic_releases_detailed


class PullsCog(commands.Cog, name="Pulls"):
    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.bot.tree.on_error = on_app_command_error

        self.brands = Brands()

        self.bot.comics: Dict[str, Dict[int, Comic]] = {}
        self.bot.order: Dict[str, List[int]] = {b.id: [] for b in self.brands}

        self.access_lock = asyncio.Lock()
        self.locks: Dict[int, asyncio.Lock] = {}

        self.schedule_offsets: Dict[Tuple[int, str], float] = {}

        self.feed_schedules: Dict[(int, str), Task] = {}
        self.bot.loop.create_task(self.on_startup_scheduler())

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
        while not self.bot.comics:
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
                await profile_pic(
                    list(self.bot.comics[BrandEnum.Marvel.value].values()),
                    list(self.bot.comics[BrandEnum.DC.value].values()),
                    self.bot)
            except Exception as e:
                print(f"Error while updating profile picture: {e}")
                traceback.print_exc()
                return None

    async def schedule_activity(self):
        while not self.bot.comics:
            await asyncio.sleep(10)

        while not self.bot.is_closed():
            comics = []
            for v in self.bot.comics.values():
                comics += [i.title for i in v.values()]

            title = random.choice(comics)
            a = Activity(type=ActivityType.watching, name=f"📖 {title}")

            await self.bot.change_presence(activity=a)

            await asyncio.sleep(random.randint(600, 3000))

    async def schedule_feeds(self):
        configs = await self.bot.db.fetch('SELECT * FROM configuration')
        all_configs = [config_from_record(c) for c in configs]

        await self.bot.wait_until_ready()

        # Filter out inaccessible configurations
        valid_configs = []
        inaccessible_configs = []

        for config in all_configs:
            is_accessible, reason = await validate_config_accessibility(self.bot, config)
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

    async def fetch_comics(self):
        print(f"~~ Fetching comics ~~   {utils.utcnow()}")
        self.bot.comics = {}
        self.bot.order = {}

        for current_brand in self.brands:
            print(f" > Fetching {current_brand.name}")
            try:
                comics = await fetch_comic_releases_detailed(publisher=current_brand.locg_id)
                comic_dict = {comic.id: comic for comic in comics}
                self.bot.comics[current_brand.id] = comic_dict
                self.sort_order(comic_dict, current_brand)
                date = week_of_date(comics)
                print(
                    f"   > {len(self.bot.comics[current_brand.id])} loaded for the week of {f_date(date)} ")
            except Exception as e:
                print(f"   ! Error fetching {current_brand.name} comics: {e}")
                traceback.print_exc()

        print(f"~~ Comics fetched ~~   {utils.utcnow()}")

    def sort_order(self, comic_dict: Dict[int, ComicDetails], brand: Brand):
        format_order = ["Comic", "Trade Paperback", "Hardcover"]

        self.bot.order[brand.id] = sorted(
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

            comics: Dict[int, Union[Comic, ComicMessage]] = self.bot.comics[config.brand.id].copy()

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
                            await pin(self.bot.user.id, lead_msg)
                        if channel.guild.id == 281648235557421056: print("finished pin")

                    if config.ping:
                        await channel.send(f"<@&{config.ping}>")

                    if _format in [Format.FULL, Format.COMPACT]:
                        embeds = {k: c.to_embed(_format == Format.FULL) for k, c in comics.items()}

                        instances = {}

                        for cid in self.bot.order[config.brand.id]:
                            if cid in comics:
                                try:
                                    msg = await channel.send(embed=embeds[cid])
                                    instances[cid] = comics[cid].to_instance(msg)
                                except Exception:
                                    pass

                        comics = instances

                    summary_embeds = await summary_embed(self.bot.order, comics, config.brand, lead_msg)

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
                        await pin(self.bot.user.id, lead_msg)

                else:
                    await channel.send(f"There are no {config.brand.name} comics this week.")
            except Forbidden:
                print(f"Missing permissions in {channel.guild.name} ({channel.guild.id})")

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

        if not self.bot.comics:
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

        if not self.bot.comics:
            return await interaction.followup.send("Comics are not yet fetched.")

        img = await profile_pic(
                    list(self.bot.comics[BrandEnum.Marvel.value].values()),
                    list(self.bot.comics[BrandEnum.DC.value].values()),
                    self.bot)
        await interaction.followup.send(file=File(fp=img, filename="my_file.png"))

    @app_commands.command(name="comics-this-week")
    @app_commands.choices(brand=BrandAutocomplete)
    async def comics_this_week(self, interaction: Interaction, brand: str):
        """Lists this week's comics!"""
        await interaction.response.defer(
            ephemeral=not interaction.channel.permissions_for(interaction.user).embed_links)
        b = self.brands[brand]

        if b.id not in self.bot.comics:
            return await interaction.followup.send("Comics are not yet fetched.")

        comics = self.bot.comics[b.id]

        con = await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1 and brand = $2',
            interaction.guild_id, b.id
        )

        if con:
            config = config_from_record(con[0])
            if config.check_keywords:
                kw = await fetch_keywords(self.bot.db, config.server_id)
                comics = {k: v for k, v in comics.items() if kw.check_comic(v)}

        embeds = await summary_embed(self.bot.order, comics, b)
        await interaction.followup.send(embeds=embeds)

    @app_commands.command(name="trigger-feed")
    @checks.has_permissions(manage_guild=True)
    @app_commands.choices(brand=BrandAutocomplete)
    async def trigger_feed(self, interaction: Interaction, brand: str):
        """Triggers your current feed configuration."""
        await interaction.response.defer()
        b = self.brands[brand]

        if b.id not in self.bot.comics.keys():
            return await interaction.followup.send(
                "Comics are not yet fetched. Please wait a few moments and try again.")

        con = await self.bot.db.fetch(
            'SELECT * FROM configuration WHERE server = $1 and brand = $2',
            interaction.guild_id, b.id
        )

        if not con:
            return await interaction.followup.send(
                f"You have not set up a {b.name} feed yet in this server! Use {cmd_ping(self.bot.cmds, 'setup')} to set one up!")

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
        configs = await fetch_configs(self.bot.db, interaction.guild_id)

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
            " · ".join(cmd_ping(self.bot.cmds, f"editfeed {i}") for i in ['channel', 'format', 'day', 'ping', 'pin']),
            embed=new_config.to_embed())


async def setup(bot):
    await bot.add_cog(PullsCog(bot))
