from dataclasses import dataclass

from discord.app_commands import Choice


@dataclass
class Brand:
    id: str
    name: str
    color: int
    default_day: int
    autocomplete_choice: Choice
    locg_id: int
