from discord import Message, Embed

from objects.brand import Brands
from comic_types.brand import Brand
from comic_types.locg import ComicDetails


class Comic(ComicDetails):
    def __str__(self) -> str:
        return f"{self.title}"

    @property
    def writer(self) -> str:
        return ', '.join(creator.name for creator in self.creators if "Writer" in creator.role)

    @property
    def price_format(self) -> str:
        return f"${self.price:.2f} USD" if self.price is not None else None

    @property
    def pages_format(self) -> str:
        return f"{self.pages} pages" if self.pages else None

    @property
    def more(self) -> str:
        return self.url

    @property
    def brand_obj(self) -> Brand:
        return Brands().from_locg_name(self.publisher)

    def process_creators(self) -> dict[str, list[str]]:
        creators = [i for i in self.creators if i.type == "creator"]

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
        keys = sorted(creators.keys(), key=sorting_key)
        text = []
        overflow = []
        for n, k in enumerate(keys):
            if n < 2 or (n == len(keys) - 1 and not overflow):
                text.append(f"-# ▸**__{k}__**\n{' · '.join(creators[k])}")
            else:
                for name in creators[k]:
                    overflow.append(f"{name} ({k})")
        if overflow:
            text.append(f"-# ▸**__More__**\n{' · '.join(overflow)}")

        result = []
        total_length = 0
        for item in text:
            item_length = len(item) + (1 if result else 0)  # +1 for '\n' if not first item
            if total_length + item_length > 1024:
                break
            result.append(item)
            total_length += item_length

        return '\n'.join(result)

    def to_embed(self, full_img=True):
        embed = Embed(
            title=self.title,
            description=self.description,
            color=self.brand_obj.color)

        if self.creators:
            embed.add_field(name="Creators", value=self.format_creators())
        embed.add_field(name="Info",
                        value=f"{' · '.join(i for i in [self.format, self.price_format, self.pages_format] if i)}\n"
                              f"Releases on {self.releaseDate.strftime('%d &M, %Y')}\n"
                              f"-# More details on [League of Comic Geeks]({self.url})")

        embed.set_footer(text=f"{self.format} · {self.title}")

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

    @property
    def more(self):
        return self.message.jump_url


def sorting_key(person):
    priority = ["Writer", "Artist", "Penciller", "Inker", "Colorist", "Letterer", "Editor"]
    try:
        return priority.index(person)
    except ValueError:
        return len(priority)
