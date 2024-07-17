from datetime import datetime
from typing import Dict, List

import discord as discord
import marvel.comic as mcom

from objects.configuration import brand_colours, Brand, brand_links


def alpha_surnames(names):
    return sorted(names, key=lambda x: x.split(' ')[-1])


class Comic:
    def __init__(self, brand: Brand = None, id=None, title=None, description=None, creators=None, image_url=None,
                 url=None, page_count=None, price=None, copyright=None, date=None, **kwargs):
        if creators is None:
            creators = {}

        self.brand: Brand = brand

        self.id = id
        self.title: str = title
        self.description: str = description
        self.creators: Dict[str, List[str]] = creators

        self.image_url: str = image_url
        self.url: str = url

        self.date: datetime = date
        self.page_count: int = page_count
        self.price: float = price

        self.copyright: str = copyright

    def __str__(self):
        return f"{self.title}"

    def writer(self):
        return ', '.join(alpha_surnames(self.creators['Writer'])) if 'Writer' in self.creators else None

    def price_format(self):
        return f"${self.price:.2f} USD" if self.price is not None else None

    def pages(self):
        return f"{self.page_count} pages" if self.page_count else None

    def more(self):
        return self.url

    def format_creators(self, *, cover=False, compact=False):
        keys = sorted(sorted(self.creators.keys()), key=sorting_key)
        bold_wrap = lambda role, name: f"**{name}**" if role else name
        return "\n".join(
            f"-# **{k}**\n{bold_wrap(k, ', '.join(alpha_surnames(self.creators[k])))}"
            for k in keys
            if (not compact or k in ["Writer", "Penciler", "Artist"]) and (cover or not k.endswith("(Cover)"))
        )

    def to_embed(self, full_img=True):
        embed = discord.Embed(
            title=self.title,
            description=self.description,
            color=brand_colours[self.brand])

        if self.creators:
            embed.add_field(name="Creators", value=self.format_creators())
        embed.add_field(name="Info",
                        value=f"{' · '.join(i for i in [self.price_format(), self.pages()] if i)}\n"
                              f"-# More details on [{brand_links[self.brand]}]({self.url})")

        embed.set_footer(text=f"{self.title} · {self.copyright}")

        if full_img:
            embed.set_image(url=self.image_url)
        else:
            embed.set_thumbnail(url=self.image_url)

        return embed

    def to_instance(self, message: discord.Message):
        return ComicMessage(self, message)


class ComicMessage(Comic):
    def __init__(self, comic: Comic, message: discord.Message):
        super().__init__(**comic.__dict__)
        self.message = message

    def more(self):
        return self.message.jump_url


def comic_obj_from_marvel(data: mcom.Comic):
    c = Comic()
    c.id = data.id
    c.title = data.title
    c.image_url = data.images[0].path + '/clean.jpg' \
        if data.images else "https://i.annihil.us/u/prod/marvel/i/mg/b/40/image_not_available/clean.jpg"

    c.page_count = data.pageCount
    c.url = next((i['url'] for i in data.urls if i['type'] == 'detail'), None)
    c.price = next((i.price for i in data.prices if i.type == 'printPrice'), None)
    c.date = next((i.date for i in data.dates if i.type == 'onsaleDate'), None)

    for cr in data.creators.items:
        role = cr.role.title()
        if role not in c.creators:
            c.creators[role] = [cr.name]
        else:
            c.creators[role].append(cr.name)

    return c


def sorting_key(person):
    priority = ["Writer", "Artist", "Penciler", "Inker", "Colorist", "Letterer", "Editor"]
    try:
        return priority.index(person)
    except ValueError:
        return len(priority)
