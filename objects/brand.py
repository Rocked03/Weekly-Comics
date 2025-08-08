from enum import Enum
from typing import Dict

from discord.app_commands import Choice

from types.brand import Brand


class BrandEnum(Enum):
    MARVEL = 'Marvel'
    DC = 'DC'


class Brands:
    Marvel = Marvel()
    DC = DC()

    def __init__(self):
        self.brands: Dict[str, Brand] = {
            BrandEnum.MARVEL.value: self.Marvel,
            BrandEnum.DC.value: self.DC
        }

    def __getitem__(self, item: str) -> Brand:
        return self.brands.get(item, None)

    def __values__(self):
        return self.brands.values()

    def __contains__(self, item: str) -> bool:
        return item in self.brands.keys()

    def __iter__(self):
        return iter(self.brands.values())


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


BrandAutocomplete = [
    brand.autocomplete_choice for brand in Brands()
]
