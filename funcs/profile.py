from io import BytesIO
from typing import List

import aiohttp
from PIL import Image, ImageDraw


class Profile:
    def __init__(self, images: List[Image.Image], size: int, margin: int,
                 skew: int, elem_width: int, *, bg=(255, 255, 255, 255), round_corners=0):
        self.images = images

        self.size = size
        self.margin = margin
        self.skew = skew
        self.elem_width = elem_width

        self.bg = bg
        self.round_corners = round_corners


def resize(im: Image.Image, width: int):
    w, h = im.size
    ratio = width / min(im.size)
    return im.resize((int(w * ratio), int(h * ratio)))


def add_corners(im, rad):
    # https://stackoverflow.com/a/11291419
    circle = Image.new('L', (rad * 2, rad * 2), 0)
    draw = ImageDraw.Draw(circle)
    draw.ellipse((0, 0, rad * 2 - 1, rad * 2 - 1), fill=255)
    alpha = Image.new('L', im.size, 255)
    w, h = im.size
    alpha.paste(circle.crop((0, 0, rad, rad)), (0, 0))
    alpha.paste(circle.crop((0, rad, rad, rad * 2)), (0, h - rad))
    alpha.paste(circle.crop((rad, 0, rad * 2, rad)), (w - rad, 0))
    alpha.paste(circle.crop((rad, rad, rad * 2, rad * 2)), (w - rad, h - rad))
    im.putalpha(alpha)
    return im


def imager(pf: Profile):
    im = Image.new('RGBA', (pf.size, pf.size), pf.bg)

    images = [resize(i, pf.elem_width) for i in pf.images[:4]]
    images = [add_corners(i, pf.round_corners) for i in images]

    x = int((pf.size - pf.margin) / 2)
    y = int((pf.size - pf.margin) / 2 - pf.skew)
    im.alpha_composite(images[0], (x - images[0].width, y - images[0].height))
    y = int((pf.size + pf.margin) / 2 - pf.skew)
    im.alpha_composite(images[1], (x - images[1].width, y))

    x = int((pf.size + pf.margin) / 2)
    y = int((pf.size - pf.margin) / 2 + pf.skew)
    im.alpha_composite(images[2], (x, y - images[2].height))
    y = int((pf.size + pf.margin) / 2 + pf.skew)
    im.alpha_composite(images[3], (x, y))

    return im


def imager_to_bytes(pf: Profile):
    im = imager(pf)
    output_buffer = BytesIO()
    im.save(output_buffer, "png")
    output_buffer.seek(0)
    return output_buffer


async def load_image(url: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            image_bytes = await response.read()
    return Image.open(BytesIO(image_bytes)).convert('RGBA')

# import requests
# from io import BytesIO
#
# p = Profile([Image.open(BytesIO(requests.get(i).content)).convert('RGBA') for i in [
#     "http://i.annihil.us/u/prod/marvel/i/mg/3/f0/643469f1d536e/clean.jpg",
#     "http://i.annihil.us/u/prod/marvel/i/mg/9/f0/643469ee0ec6f/clean.jpg",
#     "https://static.dc.com/2023-04/BBGD_Cv6_00611_DIGITAL.jpg",
#     "https://static.dc.com/2023-04/DCeased_WotUG_Cv8_00811_DIGITAL.jpg"
# ]],
#             1200, 70, 300, 1000,
#             bg=(255, 255, 255, 240),
#             round_corners=20)
#
# i = imager(p)
#
# i.show()
# i.save('icon.png')
