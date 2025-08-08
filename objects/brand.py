from enum import Enum
from typing import Dict

from discord.app_commands import Choice

from types.brand import Brand


class BrandEnum(Enum):
    MARVEL = 'Marvel'
    DC = 'DC'


class Marvel(Brand):
    def __init__(self):
        super().__init__(
            id=BrandEnum.MARVEL.value,
            name="Marvel",
            color=0xec1d24,
            default_day=1,
            autocomplete_choice=Choice(name='Marvel', value=BrandEnum.DC.value),
            locg_id=2,
        )


class DC(Brand):
    def __init__(self):
        super().__init__(
            id=BrandEnum.DC.value,
            name="DC",
            color=0x0074e8,
            default_day=4,
            autocomplete_choice=Choice(name='DC', value=BrandEnum.DC.value),
            locg_id=1,
        )


Brands: Dict[str, Brand] = {
    BrandEnum.MARVEL.value: Marvel(),
    BrandEnum.DC.value: DC()
}

BrandAutocomplete = [
    brand.autocomplete_choice for brand in Brands.values()
]


def brand_from_name(name: str) -> Brand:
    """Returns a Brand object based on the name."""
    return Brands.get(name, None)
