from typing import Any, Dict, List, Optional

import discord
from discord import ButtonStyle, Interaction, SelectOption, TextStyle, utils
from discord.ext import commands
from discord.ui import ActionRow, Button, Container, LayoutView, Modal, Select as StringSelect, Separator, \
    TextDisplay, TextInput

from objects.keywords import Types, sanitise

EDITOR_TIMEOUT = 900

TYPE_LABELS = {Types.KEYS: 'Keyword filter', Types.CREATORS: 'Creator filter'}


class KeywordEditorState:
    """Session state for one ephemeral keyword editor message."""

    def __init__(self, *, guild_id: int, keys: List[str], creators: List[str],
                 checks: Dict[str, bool], feed_labels: Dict[str, str]):
        self.guild_id = guild_id
        self.original_keys = sorted(set(keys))
        self.original_creators = sorted(set(creators))
        self.keys = list(self.original_keys)
        self.creators = list(self.original_creators)
        self.original_checks = dict(checks)
        self.checks = dict(checks)
        self.feed_labels = feed_labels          # brand id -> 'Marvel — #channel' / 'Marvel — unknown channel'
        self.pending: Dict[Types, List[str]] = {Types.KEYS: [], Types.CREATORS: []}
        self.status: Optional[str] = None
        self.message: Optional[discord.InteractionMessage] = None

    def staged_list(self, _type: Types) -> List[str]:
        return self.keys if _type is Types.KEYS else self.creators

    def set_staged_list(self, _type: Types, value: List[str]) -> None:
        if _type is Types.KEYS:
            self.keys = sorted(set(value))
        else:
            self.creators = sorted(set(value))

    def dirty(self) -> bool:
        return (set(self.keys) != set(self.original_keys)
                or set(self.creators) != set(self.original_creators)
                or self.checks != self.original_checks)


def notice_view(text: str) -> LayoutView:
    view = LayoutView(timeout=None)
    view.add_item(TextDisplay(text))
    return view


class KeywordEditorView(LayoutView):
    def __init__(self, state: KeywordEditorState, bot: Optional[commands.Bot]):
        super().__init__(timeout=EDITOR_TIMEOUT)
        self.editor_state = state
        self.bot = bot

    async def on_timeout(self):
        if self.editor_state.message is None:
            return
        try:
            await self.editor_state.message.edit(
                view=notice_view('This editor has expired — rerun /keywords to make changes.'),
                attachments=[])
        except Exception:
            pass


async def _rerender(view: KeywordEditorView, interaction: Interaction, state: KeywordEditorState,
                    bot: Optional[commands.Bot]):
    new_view = build_keyword_editor_view(state, bot)
    view.stop()
    await interaction.response.edit_message(view=new_view)


class AddKeywordsModal(Modal):
    input = TextInput(label='Comma-separated keywords', max_length=300,
                      placeholder='e.g. BATMAN, absolute carnage')

    def __init__(self, state: KeywordEditorState, bot: Optional[commands.Bot], _type: Types,
                 view: KeywordEditorView):
        title = 'Add Keywords' if _type is Types.KEYS else 'Add Creators'
        super().__init__(title=title, timeout=EDITOR_TIMEOUT)
        self.editor_state = state
        self.bot = bot
        self.kw_type = _type
        self.view_ref = view

    async def on_submit(self, interaction: Interaction):
        entered = [sanitise(part) for part in str(self.input.value).split(',')]
        entered = [part for part in entered if part]
        staged = self.editor_state.staged_list(self.kw_type)
        fresh = [k for k in entered if k not in set(staged)]
        duplicates = len(entered) - len(fresh)
        if not entered:
            self.editor_state.status = 'Nothing to add'
        elif not fresh:
            self.editor_state.status = 'Nothing to add · already in the filter'
        else:
            self.editor_state.set_staged_list(self.kw_type, staged + fresh)
            summary = f'Added {len(fresh)}'
            if duplicates:
                summary += f' · {duplicates} duplicate{"s" if duplicates != 1 else ""} ignored'
            self.editor_state.status = summary
        self.view_ref.stop()
        await interaction.response.edit_message(view=build_keyword_editor_view(self.editor_state, self.bot))


def _list_select(view: KeywordEditorView, state: KeywordEditorState, _type: Types) -> StringSelect:
    staged = state.staged_list(_type)
    s = StringSelect(custom_id='kw:list-keys' if _type is Types.KEYS else 'kw:list-creators',
                     placeholder=TYPE_LABELS[_type], min_values=1, max_values=min(25, len(staged)),
                     options=[SelectOption(label=k) for k in staged[:25]])

    async def callback(interaction: Interaction):
        state.pending[_type] = list(s.values)
        await interaction.response.defer()  # deferred update: keep ticks visible, no re-render

    s.callback = callback
    return s


def _add_button(view: KeywordEditorView, state: KeywordEditorState, bot: Optional[commands.Bot],
                _type: Types) -> Button:
    b = Button(style=ButtonStyle.primary, label='Add…',
               custom_id='kw:add-keys' if _type is Types.KEYS else 'kw:add-creators')

    async def callback(interaction: Interaction):
        await interaction.response.send_modal(AddKeywordsModal(state, bot, _type, view))

    b.callback = callback
    return b


def _remove_button(view: KeywordEditorView, state: KeywordEditorState, _type: Types) -> Button:
    b = Button(style=ButtonStyle.danger, label='Remove',
               custom_id='kw:remove-keys' if _type is Types.KEYS else 'kw:remove-creators',
               disabled=not state.staged_list(_type))

    async def callback(interaction: Interaction):
        pending = state.pending[_type]
        if not pending:
            state.status = 'Select keywords first'
        else:
            staged = state.staged_list(_type)
            keep = [k for k in staged if k not in set(pending)]
            state.set_staged_list(_type, keep)
            state.pending[_type] = []
            state.status = f'Removed {len(staged) - len(keep)}'
        await _rerender(view, interaction, state, state_bot(view))

    b.callback = callback
    return b


def state_bot(view: KeywordEditorView) -> Optional[commands.Bot]:
    return view.bot


def _feeds_select(view: KeywordEditorView, state: KeywordEditorState) -> StringSelect:
    options = [SelectOption(label=state.feed_labels[b], value=b, default=state.checks[b])
               for b in sorted(state.checks)]
    s = StringSelect(custom_id='kw:feeds', placeholder='Feeds with the keyword filter enabled',
                     min_values=0, max_values=len(options), options=options)

    async def callback(interaction: Interaction):
        selected = set(s.values)
        for brand_id in state.checks:
            state.checks[brand_id] = brand_id in selected
        state.status = None
        await _rerender(view, interaction, state, state_bot(view))

    s.callback = callback
    return s


def _label(text: str) -> TextDisplay:
    return TextDisplay(f'-# {text}')


def _save_button(view: KeywordEditorView, state: KeywordEditorState, bot: Optional[commands.Bot]) -> Button:
    b = Button(style=ButtonStyle.success, label='Save', custom_id='kw:save', disabled=not state.dirty())

    async def callback(interaction: Interaction):
        state.status = 'Save is not wired yet'
        await _rerender(view, interaction, state, bot)

    b.callback = callback
    return b


def build_keyword_editor_view(state: KeywordEditorState, bot: Optional[commands.Bot]) -> KeywordEditorView:
    view = KeywordEditorView(state, bot)
    container = Container()

    header_lines = ['## Keyword Filter']
    if state.status:
        header_lines.append(f'-# {state.status}')
    container.add_item(TextDisplay('\n'.join(header_lines)))

    def section(_type: Types) -> None:
        staged = state.staged_list(_type)
        container.add_item(_label(f'{TYPE_LABELS[_type]} ({len(staged)})'))
        if staged:
            container.add_item(ActionRow(_list_select(view, state, _type)))
            if len(staged) > 25:
                container.add_item(_label(f'showing 25 of {len(staged)}'))
        else:
            container.add_item(_label('None yet — add some'))
        container.add_item(ActionRow(_add_button(view, state, bot, _type),
                                     _remove_button(view, state, _type)))

    section(Types.KEYS)
    container.add_item(Separator())
    section(Types.CREATORS)
    container.add_item(Separator())

    if state.feed_labels:
        enabled = sum(state.checks.values())
        container.add_item(_label(f'Feeds ({enabled} of {len(state.checks)} enabled)'))
        container.add_item(ActionRow(_feeds_select(view, state)))
    else:
        container.add_item(_label('No feeds yet — create one with /setup'))

    container.add_item(Separator())
    if state.dirty():
        container.add_item(_label('⚠ You have unsaved changes — press Save to apply'))
    container.add_item(ActionRow(_save_button(view, state, bot)))

    view.add_item(container)
    return view
