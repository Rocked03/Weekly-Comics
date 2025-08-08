from discord import Message

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
