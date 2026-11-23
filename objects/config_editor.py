from typing import Any, Callable, Dict, List, Optional, Union

import discord
from discord import ButtonStyle, ChannelType, Interaction, Object, SelectOption, TextStyle, utils
from discord.ext import commands
from discord.ui import ActionRow, Button, ChannelSelect, Container, LayoutView, Modal, RoleSelect, Separator, \
    Select as StringSelect, TextDisplay, TextInput

from comic_types.brand import Brand
from funcs.discord_functions import cmd_ping
from funcs.pull_functions import clamp
from objects.brand import Brands
from objects.configuration import COMIC_FORMATS, WEEKDAYS, Format, Schedule, SCHEDULE_LABELS, Configuration, \
    next_scheduled, default_feed_name, FeedNameInUse

EDITOR_TIMEOUT = 900

SCHEDULE_HINTS = {
    Schedule.PRINT: 'Current print releases',
    Schedule.DIGITAL_1M: 'Marvel Comics App Ultimate / DCUI Ultra timing',
    Schedule.DIGITAL_3M: 'Marvel Comics App Standard / Marvel Unlimited timing',
    Schedule.DIGITAL_6M: 'DC Universe Infinite Standard timing',
}


class EditorState:
    """Session state for one ephemeral editor message."""

    def __init__(self, configs: Dict[int, Configuration], selection: Union[int, str], *,
                 guild_id: int, default_channel_id: Optional[int] = None):
        self.configs = configs                # feed id -> Configuration for existing feeds
        self.guild_id = guild_id
        self.default_channel_id = default_channel_id  # set => create mode
        if isinstance(selection, str):
            self.brand_id: str = selection     # create mode: brand of the feed being created
            self.feed_id: Optional[int] = None  # edit mode: currently selected feed id
        else:
            self.brand_id = ''
            self.feed_id = selection
        self.feed_name: Optional[str] = None   # create mode: staged custom name (None = auto default)
        self.changes: Dict[str, Any] = {}      # staged attribute values for the selected feed
        self.status: Optional[str] = None      # transient header flash message
        self.message: Optional[discord.InteractionMessage] = None

    @property
    def creating(self) -> bool:
        return self.default_channel_id is not None


def current_config(state: EditorState) -> Configuration:
    """The selected existing feed (edit mode only)."""
    return state.configs[state.feed_id]


def current_brand(state: EditorState) -> Brand:
    if state.creating:
        return Brands()[state.brand_id]
    return current_config(state).brand


def display_name(state: EditorState) -> str:
    """Name shown in the editor header for the current selection."""
    if state.creating:
        if state.feed_name is not None:
            return state.feed_name
        return default_feed_name(current_brand(state), [c.name for c in state.configs.values()])
    if state.feed_id not in state.configs:
        return '(deleted feed)'
    return effective_values(state)['name']


def base_values(state: EditorState) -> Dict[str, Any]:
    """Attribute values the selects should currently default to (before staged changes)."""
    if state.creating or state.feed_id not in state.configs:
        return {
            'name': display_name(state),
            'channel_id': state.default_channel_id,
            'format': Format.SUMMARY,
            'day': current_brand(state).default_day,
            'ping': None,
            'pin': False,
            'check_keywords': False,
            'disabled_formats': [],
            'schedule': Schedule.PRINT,
        }
    c = state.configs[state.feed_id]
    return {
        'name': c.name,
        'channel_id': c.channel_id,
        'format': c.format,
        'day': c.day,
        'ping': c.ping,
        'pin': c.pin,
        'check_keywords': c.check_keywords,
        'disabled_formats': sorted(c.disabled_formats),
        'schedule': c.schedule,
    }


def effective_values(state: EditorState) -> Dict[str, Any]:
    return {**base_values(state), **state.changes}


def changed_keys(state: EditorState) -> List[str]:
    base = base_values(state)
    return [k for k, v in state.changes.items() if v != base[k]]


def notice_view(text: str) -> LayoutView:
    view = LayoutView(timeout=None)
    view.add_item(TextDisplay(text))
    return view


class EditorView(LayoutView):
    def __init__(self, state: EditorState, bot: Optional[commands.Bot]):
        super().__init__(timeout=EDITOR_TIMEOUT)
        self.editor_state = state
        self.bot = bot

    async def on_timeout(self):
        if self.editor_state.message is None:
            return
        try:
            await self.editor_state.message.edit(
                view=notice_view('This editor has expired — rerun the command to make changes.'),
                attachments=[])
        except Exception:
            pass


async def _rerender(view: EditorView, interaction: Interaction, state: EditorState,
                    bot: Optional[commands.Bot]):
    new_view = build_editor_view(state, bot)
    view.stop()
    await interaction.response.edit_message(view=new_view)


def _stage_callback(view: EditorView, state: EditorState, bot: Optional[commands.Bot],
                    key: str, transform: Callable[[list], Any], select):
    async def callback(interaction: Interaction):
        state.changes[key] = transform(select.values)
        state.status = None
        await _rerender(view, interaction, state, bot)

    return callback


def _brand_callback(view: EditorView, state: EditorState, bot: Optional[commands.Bot], select):
    async def callback(interaction: Interaction):
        state.brand_id = select.values[0]
        state.feed_name = None  # re-derive the default name for the new brand
        state.changes = {}
        state.status = None
        await _rerender(view, interaction, state, bot)

    return callback


def _feed_callback(view: EditorView, state: EditorState, bot: Optional[commands.Bot], select):
    async def callback(interaction: Interaction):
        if changed_keys(state):
            # Feed switching is locked while unsaved changes exist; ignore.
            return await _rerender(view, interaction, state, bot)
        state.feed_id = int(select.values[0])
        state.changes = {}
        state.status = None
        await _rerender(view, interaction, state, bot)

    return callback


class FeedNameModal(Modal):
    def __init__(self, state: EditorState, bot: Optional[commands.Bot], view: EditorView):
        super().__init__(title='Set Feed Name', timeout=EDITOR_TIMEOUT)
        self.editor_state = state
        self.bot = bot
        self.view_ref = view
        self.name_input = TextInput(label='Feed name (blank = automatic default)',
                                    style=TextStyle.short, required=False, max_length=100,
                                    default=display_name(state))
        self.add_item(self.name_input)

    async def on_submit(self, interaction: Interaction):
        state = self.editor_state
        value = self.name_input.value.strip()
        if state.creating:
            state.feed_name = value or None
            state.status = None
        elif not value:
            state.status = '⚠ Feed name cannot be empty.'
        elif value.lower() in {c.name.lower() for fid, c in state.configs.items() if fid != state.feed_id}:
            state.status = '⚠ That feed name is already in use in this server.'
        else:
            state.changes['name'] = value
            state.status = None
        self.view_ref.stop()
        await interaction.response.edit_message(view=build_editor_view(state, self.bot))


def _name_button(view: EditorView, state: EditorState, bot: Optional[commands.Bot]) -> Button:
    b = Button(style=ButtonStyle.primary, label='Set Name', custom_id='editor:name')

    async def callback(interaction: Interaction):
        await interaction.response.send_modal(FeedNameModal(state, bot, view))

    b.callback = callback
    return b


def _channel_select(view, state, bot, vals) -> ChannelSelect:
    s = ChannelSelect(custom_id='editor:channel', placeholder='Channel',
                      channel_types=[ChannelType.text, ChannelType.news],
                      default_values=[Object(vals['channel_id'])])
    s.callback = _stage_callback(view, state, bot, 'channel_id', lambda v: v[0].id, s)
    return s


def _format_select(view, state, bot, vals) -> StringSelect:
    s = StringSelect(custom_id='editor:format', placeholder='Feed format',
                     options=[SelectOption(label=f.value, value=f.value, default=f == vals['format'])
                              for f in Format])
    s.callback = _stage_callback(view, state, bot, 'format', lambda v: Format(v[0]), s)
    return s


def _day_select(view, state, bot, vals) -> StringSelect:
    s = StringSelect(custom_id='editor:day', placeholder='Post day',
                     options=[SelectOption(label=d, value=str(n), default=n == vals['day'])
                              for n, d in enumerate(WEEKDAYS)])
    s.callback = _stage_callback(view, state, bot, 'day', lambda v: int(v[0]), s)
    return s


def _ping_select(view, state, bot, vals) -> RoleSelect:
    s = RoleSelect(custom_id='editor:ping', placeholder='Ping role (empty = no ping)',
                   min_values=0, max_values=1,
                   default_values=[Object(vals['ping'])] if vals['ping'] else [])
    s.callback = _stage_callback(view, state, bot, 'ping', lambda v: v[0].id if v else None, s)
    return s


def _toggle_select(view, state, bot, key: str, custom_id: str, placeholder: str, current: bool) -> StringSelect:
    s = StringSelect(custom_id=custom_id, placeholder=placeholder,
                     options=[SelectOption(label=l, value=l, default=(l == 'Enable') == current)
                              for l in ('Enable', 'Disable')])
    s.callback = _stage_callback(view, state, bot, key, lambda v: v[0] == 'Enable', s)
    return s


def _formats_select(view, state, bot, vals) -> StringSelect:
    enabled = set(COMIC_FORMATS) - set(vals['disabled_formats'])
    s = StringSelect(custom_id='editor:formats', placeholder='Comic formats shown in the feed',
                     min_values=0, max_values=len(COMIC_FORMATS),
                     options=[SelectOption(label=f, value=f, default=f in enabled) for f in COMIC_FORMATS])
    s.callback = _stage_callback(view, state, bot, 'disabled_formats',
                                 lambda v: sorted(set(COMIC_FORMATS) - set(v)), s)
    return s


def _schedule_select(view, state, bot, vals) -> StringSelect:
    s = StringSelect(custom_id='editor:schedule', placeholder='Schedule',
                     options=[SelectOption(label=SCHEDULE_LABELS[sch.value], value=sch.value,
                                           default=sch == vals['schedule'], description=SCHEDULE_HINTS[sch])
                              for sch in Schedule])
    s.callback = _stage_callback(view, state, bot, 'schedule', lambda v: Schedule(v[0]), s)
    return s


def _label(text: str) -> TextDisplay:
    return TextDisplay(f'-# {text}')


def keywords_mention(bot: Optional[commands.Bot]) -> str:
    bot_ref: Any = bot
    if not bot_ref:
        return '/keywords'
    return cmd_ping(bot_ref.cmds, 'keywords')


def build_editor_view(state: EditorState, bot: Optional[commands.Bot]) -> EditorView:
    brands = Brands()
    brand = current_brand(state)
    vals = effective_values(state)
    changed = changed_keys(state)
    creating = state.creating

    view = EditorView(state, bot)
    container = Container(accent_colour=brand.color)

    verb = 'first' if creating else 'next'
    title = f'## New {brand.name} Feed' if creating else f'## {display_name(state)}'
    summary = f'{brand.name} · posting in <#{vals["channel_id"]}> · {SCHEDULE_LABELS[vals["schedule"].value]}'
    header = '\n'.join([title, summary] + ([f'-# {state.status}'] if state.status else []))
    container.add_item(TextDisplay(header))

    if creating:
        brand_options = [SelectOption(label=b.name, value=b.id, default=b.id == state.brand_id)
                         for b in brands]
        brand_select = StringSelect(custom_id='editor:brand', placeholder='Brand', options=brand_options)
        brand_select.callback = _brand_callback(view, state, bot, brand_select)
        container.add_item(_label('Brand'))
        container.add_item(ActionRow(brand_select))
    else:
        feed_options = []
        for fid, c in sorted(state.configs.items()):
            ch = bot.get_channel(c.channel_id) if bot else None
            desc = f'{c.brand.name} · #{ch.name}' if ch else c.brand.name
            feed_options.append(SelectOption(label=c.name, value=str(fid), description=clamp(desc, 100),
                                             default=fid == state.feed_id))
        feed_select = StringSelect(custom_id='editor:feed', placeholder='Feed',
                                   options=feed_options, disabled=bool(changed))
        feed_select.callback = _feed_callback(view, state, bot, feed_select)
        container.add_item(_label('Feed'))
        container.add_item(ActionRow(feed_select))

    container.add_item(Separator())

    container.add_item(_label(f'Feed name · {display_name(state)}'))
    container.add_item(ActionRow(_name_button(view, state, bot)))

    container.add_item(Separator())

    container.add_item(_label('Channel'))
    container.add_item(ActionRow(_channel_select(view, state, bot, vals)))
    container.add_item(_label('Feed format'))
    container.add_item(ActionRow(_format_select(view, state, bot, vals)))
    container.add_item(TextDisplay(
        f'-# Post day · {verb} post <t:{int(next_scheduled(vals["day"]).timestamp())}:D>'))
    container.add_item(ActionRow(_day_select(view, state, bot, vals)))
    container.add_item(_label('Ping role'))
    container.add_item(ActionRow(_ping_select(view, state, bot, vals)))
    container.add_item(_label('Pin weekly post'))
    container.add_item(ActionRow(_toggle_select(view, state, bot, 'pin', 'editor:pin', 'Pin weekly post',
                                                vals['pin'])))
    container.add_item(_label(f'Keyword filter · configure with {keywords_mention(bot)}'))
    container.add_item(ActionRow(_toggle_select(view, state, bot, 'check_keywords', 'editor:keywords',
                                                'Filter by /keywords', vals['check_keywords'])))
    container.add_item(_label('Comic formats'))
    container.add_item(ActionRow(_formats_select(view, state, bot, vals)))
    container.add_item(_label('Schedule'))
    container.add_item(ActionRow(_schedule_select(view, state, bot, vals)))

    container.add_item(Separator())
    if changed:
        container.add_item(TextDisplay('⚠ You have unsaved changes — press Save to apply.'))

    save = Button(label='Save', style=ButtonStyle.green, custom_id='editor:save',
                  disabled=(not creating and not changed))
    save.callback = _save_callback(view, state, bot)
    row = ActionRow(save)
    if not creating:
        delete = Button(label='Delete Feed', style=ButtonStyle.red, custom_id='editor:delete')
        delete.callback = _delete_callback(view, state, bot)
        row.add_item(delete)
    container.add_item(row)

    view.add_item(container)
    return view


async def _update_feed(state: EditorState, bot: Any):
    """Apply staged changes to the selected existing feed (one UPDATE, reschedule if needed)."""
    config = state.configs[state.feed_id]
    changed = {k: state.changes[k] for k in changed_keys(state)}
    if not changed:
        return
    snapshot = {k: getattr(config, k) for k in changed}
    try:
        for k, v in changed.items():
            setattr(config, k, v)
        await config.edit_sql(bot.db)
    except Exception:
        for k, v in snapshot.items():  # roll back the in-memory object on DB failure
            setattr(config, k, v)
        raise
    if 'day' in changed or 'schedule' in changed:
        pulls = bot.get_cog('Pulls')
        if pulls is not None:
            try:
                pulls.cancel_feed(config)
            except KeyError:
                pass
            pulls.schedule_feed(config)
    state.changes = {}


async def _create_feed(state: EditorState, bot: Any):
    """Create the configured feed and flip the session into edit mode."""
    brand = Brands()[state.brand_id]
    vals = effective_values(state)
    existing_names = [c.name for c in state.configs.values()]
    name = state.feed_name if state.feed_name is not None else default_feed_name(brand, existing_names)
    if name.lower() in {n.lower() for n in existing_names}:
        raise FeedNameInUse(name)
    config = Configuration(
        state.guild_id, vals['channel_id'], brand=brand, name=name,
        _format=vals['format'], day=vals['day'], ping=vals['ping'], pin=vals['pin'],
        check_keywords=vals['check_keywords'], disabled_formats=vals['disabled_formats'],
        schedule=vals['schedule'])
    await config.upload_to_sql(bot.db)
    state.default_channel_id = None
    state.brand_id = ''
    state.feed_name = None
    state.feed_id = config.feed_id
    state.configs[config.feed_id] = config
    state.changes = {}
    pulls = bot.get_cog('Pulls')
    if pulls is not None:
        pulls.schedule_feed(config)


def _save_callback(view: EditorView, state: EditorState, bot: Optional[commands.Bot]):
    async def callback(interaction: Interaction):
        if state.creating:
            verb = '✓ Feed created'
        else:
            verb = '✓ Saved'
        try:
            if state.creating:
                await _create_feed(state, bot)
            else:
                await _update_feed(state, bot)
        except FeedNameInUse:
            state.status = '⚠ That feed name is already in use in this server.'
            return await _rerender(view, interaction, state, bot)
        except Exception:
            state.status = '⚠ Save failed — your changes are still staged, try again.'
            return await _rerender(view, interaction, state, bot)
        state.status = f'{verb} <t:{int(utils.utcnow().timestamp())}:R>'
        await _rerender(view, interaction, state, bot)

    return callback


def _delete_callback(view: EditorView, state: EditorState, bot: Optional[commands.Bot]):
    async def callback(interaction: Interaction):
        await interaction.response.send_modal(
            DeleteFeedModal(state, bot, current_config(state), view))

    return callback


class DeleteFeedModal(Modal):
    def __init__(self, state: EditorState, bot: Optional[commands.Bot], config: Configuration,
                 view: EditorView):
        super().__init__(title='Delete Feed')
        self.state = state
        self.bot: Any = bot
        self.config = config
        self.view = view
        self.confirm = TextInput(label='Type the feed name to confirm', style=TextStyle.short,
                                 placeholder=config.name, max_length=100)
        self.add_item(self.confirm)

    async def on_submit(self, modal_interaction: Interaction):
        if self.confirm.value.strip().lower() != self.config.name.lower():
            await modal_interaction.response.send_message(
                'Confirmation text did not match — the feed was not deleted.', ephemeral=True)
            return
        config = self.config
        try:
            await config.delete_from_sql(self.bot.db)
        except Exception:
            await modal_interaction.response.send_message(
                'Delete failed — try again.', ephemeral=True)
            return
        pulls = self.bot.get_cog('Pulls')
        if pulls is not None:
            try:
                pulls.cancel_feed(config)
            except KeyError:
                pass
        self.state.configs.pop(config.feed_id, None)
        self.state.changes = {}
        self.state.status = None
        if self.state.configs:
            self.state.feed_id = min(self.state.configs)
            self.view.stop()
            await modal_interaction.response.edit_message(view=build_editor_view(self.state, self.bot))
        else:
            self.view.stop()
            await modal_interaction.response.edit_message(
                view=notice_view('No feeds left in this server — use /setup to create one.'))
