import datetime as dt
from dataclasses import fields, is_dataclass
from typing import Type, TypeVar, List

from discord import Interaction

from comic_types.locg import ComicDetails
from config import ADMIN_USER_IDS
from objects.brand import Brands

T = TypeVar('T')


def from_dict(data_class: Type[T], data: dict) -> T:
    if not is_dataclass(data_class):
        raise ValueError(f"{data_class} is not a dataclass")

    field_types = {f.name: f.type for f in fields(data_class)}
    init_args = {}

    for field_name, field_type in field_types.items():
        if field_name in data:
            value = data[field_name]
            if is_dataclass(field_type):
                init_args[field_name] = from_dict(field_type, value)
            elif hasattr(field_type, '__origin__') and field_type.__origin__ == list:
                inner_type = field_type.__args__[0]
                init_args[field_name] = [from_dict(inner_type, item) if is_dataclass(inner_type) else item for item in
                                         value]
            else:
                init_args[field_name] = value

    return data_class(**init_args)


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
