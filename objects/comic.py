from discord import Message, Embed

from objects.brand import Brands
from types.brand import Brand
from types.logc import ComicDetails


class Comic(ComicDetails):
    def __str__(self) -> str:
        return f"{self.title}"

    def writer(self) -> str:
        return ', '.join(creator.name for creator in self.creators if creator.role == "Writer")

    def price_format(self) -> str:
        return f"${self.price:.2f} USD" if self.price is not None else None

    def pages(self) -> str:
        return f"{self.pages} pages" if self.pages else None

    def more(self) -> str:
        return self.url

    def brand_obj(self) -> Brand:
        return Brands[self.publisher]

    def process_creators(self) -> dict[str, list[str]]:
        creators = [i for i in self.creators if i.type == "comic"]

        grouped_creators = {}
        for creator in creators:
            for role in creator.role.split(', '):
                role = role.strip()
                if role not in grouped_creators:
                    grouped_creators[role] = []
                grouped_creators[role].append(creator.name)

        return grouped_creators

    def format_creators(self):
        creators = self.process_creators()
        keys = sorted(sorted(creators.keys()), key=sorting_key)
        text = []
        overflow = []
        for n, k in enumerate(keys):
            if n < 2 or (n == len(keys) - 1 and not overflow):
                text.append(f"-# **{k}**\n{', '.join(creators[k])}")
            else:
                for name in creators[k]:
                    overflow.append(f"{name} ({k})")
        if overflow:
            text.append(f"-# **More**\n{', '.join(overflow)}")
        return '\n'.join(text)

    def to_embed(self, full_img=True):
        embed = Embed(
            title=self.title,
            description=self.description,
            color=self.brand_obj.color)

        if self.creators:
            embed.add_field(name="Creators", value=self.format_creators())
        embed.add_field(name="Info",
                        value=f"{' · '.join(i for i in [self.format, self.price_format(), self.pages()] if i)}\n"
                              f"-# More details on [League of Comic Geeks]({self.url})")

        embed.set_footer(text=f"{self.title}")

        if full_img:
            embed.set_image(url=self.coverImage)
        else:
            embed.set_thumbnail(url=self.coverImage)

        return embed

    def to_instance(self, message: Message):
        return ComicMessage(self, message)


class ComicMessage(Comic):
    def __init__(self, comic: Comic, message: Message):
        super().__init__(**comic.__dict__)
        self.message = message

    def more(self):
        return self.message.jump_url


def sorting_key(person):
    priority = ["Writer", "Artist", "Penciller", "Inker", "Colorist", "Letterer", "Editor"]
    try:
        return priority.index(person)
    except ValueError:
        return len(priority)
