from typing import Any, Callable, Dict, List, Optional

import discord
from discord import ButtonStyle, ChannelType, Interaction, Object, SelectOption, TextStyle, utils
from discord.ext import commands
from discord.ui import ActionRow, Button, ChannelSelect, Container, LayoutView, Modal, RoleSelect, Separator, \
    Select as StringSelect, TextDisplay, TextInput

from comic_types.brand import Brand
from objects.brand import Brands
from objects.configuration import COMIC_FORMATS, WEEKDAYS, Format, Schedule, SCHEDULE_LABELS, Configuration, \
    next_scheduled

EDITOR_TIMEOUT = 900

SCHEDULE_HINTS = {
    Schedule.PRINT: 'Current print releases',
    Schedule.DIGITAL_1M: 'Marvel Comics App Ultimate / DCUI Ultra timing',
    Schedule.DIGITAL_3M: 'Marvel Comics App Standard / Marvel Unlimited timing',
    Schedule.DIGITAL_6M: 'DC Universe Infinite Standard timing',
}


class EditorState:
    """Session state for one ephemeral editor message."""

    def __init__(self, configs: Dict[str, Configuration], brand_id: str, *,
                 guild_id: int, default_channel_id: Optional[int] = None):
        self.configs = configs                # brand id -> Configuration for existing feeds
        self.brand_id = brand_id              # currently selected brand id
        self.guild_id = guild_id
        self.default_channel_id = default_channel_id  # set => create mode
        self.changes: Dict[str, Any] = {}     # staged attribute values for the selected brand
        self.status: Optional[str] = None     # transient header flash message
        self.message: Optional[discord.InteractionMessage] = None

    @property
    def creating(self) -> bool:
        return self.default_channel_id is not None


def base_values(state: EditorState) -> Dict[str, Any]:
    """Attribute values the selects should currently default to (before staged changes)."""
    if state.creating or state.brand_id not in state.configs:
        return {
            'channel_id': state.default_channel_id,
            'format': Format.SUMMARY,
            'day': Brands()[state.brand_id].default_day,
            'ping': None,
            'pin': False,
            'check_keywords': False,
            'disabled_formats': [],
            'schedule': Schedule.PRINT,
        }
    c = state.configs[state.brand_id]
    return {
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
        if not state.creating and changed_keys(state):
            # Brand switching is locked while unsaved changes exist; ignore.
            return await _rerender(view, interaction, state, bot)
        state.brand_id = select.values[0]
        state.changes = {}
        state.status = None
        await _rerender(view, interaction, state, bot)

    return callback


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
                     options=[SelectOption(label=d, value=str(n), default=n == vals['day'],
                                           description=f'Next: <t:{int(next_scheduled(n).timestamp())}:D>')
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


def build_editor_view(state: EditorState, bot: Optional[commands.Bot]) -> EditorView:
    brands = Brands()
    brand = brands[state.brand_id]
    vals = effective_values(state)
    changed = changed_keys(state)
    creating = state.creating

    view = EditorView(state, bot)
    container = Container(accent_colour=brand.color)

    verb = 'first' if creating else 'next'
    title = f'## New {brand.name} Feed' if creating else f'## {brand.name} Feed Configuration'
    summary = (f'Posting in <#{vals["channel_id"]}> · {verb} post '
               f'<t:{int(next_scheduled(vals["day"]).timestamp())}:D> · {SCHEDULE_LABELS[vals["schedule"].value]}')
    header = '\n'.join([title, summary] + ([f'-# {state.status}'] if state.status else []))
    container.add_item(TextDisplay(header))

    if creating:
        brand_options = [SelectOption(label=b.name, value=b.id) for b in brands if b.id not in state.configs]
    else:
        brand_options = [SelectOption(label=b.name, value=b.id) for b in brands if b.id in state.configs]
    for o in brand_options:
        o.default = o.value == state.brand_id
    brand_select = StringSelect(custom_id='editor:brand', placeholder='Feed', options=brand_options,
                                disabled=(not creating and bool(changed)))
    brand_select.callback = _brand_callback(view, state, bot, brand_select)
    container.add_item(ActionRow(brand_select))

    container.add_item(Separator())

    container.add_item(ActionRow(_channel_select(view, state, bot, vals)))
    container.add_item(ActionRow(_format_select(view, state, bot, vals)))
    container.add_item(ActionRow(_day_select(view, state, bot, vals)))
    container.add_item(ActionRow(_ping_select(view, state, bot, vals)))
    container.add_item(ActionRow(_toggle_select(view, state, bot, 'pin', 'editor:pin', 'Pin weekly post',
                                                vals['pin'])))
    container.add_item(ActionRow(_toggle_select(view, state, bot, 'check_keywords', 'editor:keywords',
                                                'Filter by /keywords', vals['check_keywords'])))
    container.add_item(ActionRow(_formats_select(view, state, bot, vals)))
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
    config = state.configs[state.brand_id]
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
            pulls.cancel_feed(config)
            pulls.schedule_feed(config)
    state.changes = {}


async def _create_feed(state: EditorState, bot: Any):
    """Create the configured feed and flip the session into edit mode."""
    brand = Brands()[state.brand_id]
    vals = effective_values(state)
    config = Configuration(
        state.guild_id, vals['channel_id'], brand=brand,
        _format=vals['format'], day=vals['day'], ping=vals['ping'], pin=vals['pin'],
        check_keywords=vals['check_keywords'], disabled_formats=vals['disabled_formats'],
        schedule=vals['schedule'])
    await config.upload_to_sql(bot.db)
    state.default_channel_id = None
    state.configs[brand.id] = config
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
        except Exception:
            state.status = '⚠ Save failed — your changes are still staged, try again.'
            return await _rerender(view, interaction, state, bot)
        state.status = f'{verb} {utils.utcnow().strftime("%H:%M UTC")}'
        await _rerender(view, interaction, state, bot)

    return callback


def _delete_callback(view: EditorView, state: EditorState, bot: Optional[commands.Bot]):
    async def callback(interaction: Interaction):
        await interaction.response.send_modal(DeleteFeedModal(state, bot, Brands()[state.brand_id], view))

    return callback


class DeleteFeedModal(Modal):
    def __init__(self, state: EditorState, bot: Optional[commands.Bot], brand: Brand, view: EditorView):
        super().__init__(title=f'Delete {brand.name} Feed')
        self.state = state
        self.bot: Any = bot
        self.brand = brand
        self.view = view
        self.confirm = TextInput(label=f'Type "{brand.name}" to confirm', style=TextStyle.short, max_length=32)
        self.add_item(self.confirm)

    async def on_submit(self, modal_interaction: Interaction):
        if self.confirm.value.strip().lower() != self.brand.name.lower():
            await modal_interaction.response.send_message(
                'Confirmation text did not match — the feed was not deleted.', ephemeral=True)
            return
        config = self.state.configs.get(self.brand.id)
        try:
            if config is not None:
                await config.delete_from_sql(self.bot.db)
        except Exception:
            await modal_interaction.response.send_message(
                'Delete failed — try again.', ephemeral=True)
            return
        pulls = self.bot.get_cog('Pulls')
        if pulls is not None and config is not None:
            try:
                pulls.cancel_feed(config)
            except KeyError:
                pass
        self.state.configs.pop(self.brand.id, None)
        self.state.changes = {}
        self.state.status = None
        if self.state.configs:
            self.state.brand_id = sorted(self.state.configs)[0]
            self.view.stop()
            await modal_interaction.response.edit_message(view=build_editor_view(self.state, self.bot))
        else:
            self.view.stop()
            await modal_interaction.response.edit_message(
                view=notice_view('No feeds left in this server — use /setup to create one.'))
