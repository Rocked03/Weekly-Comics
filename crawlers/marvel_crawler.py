from typing import Dict

import aiohttp
from bs4 import BeautifulSoup

from objects.comic import comic_obj_from_marvel, Comic
from objects.configuration import Brand


async def marvel_from_API(marvel):
    raw = await marvel.get_comics(format='comic', noVariants='true', dateDescriptor='thisWeek', limit=100)

    m_copyright = raw.dict['attributionText']

    comics = [comic_obj_from_marvel(c) for c in raw.data.results]
    for c in comics:
        c.brand = Brand.MARVEL
        c.copyright = m_copyright
    return {c.id: c for c in comics}


async def marvel_from_soup():
    async with aiohttp.ClientSession() as cs:
        async with cs.get("https://marvel.com/comics/calendar/") as r:
            page = await r.text()
    soup = BeautifulSoup(page, 'html.parser')

    descs = {}

    for link in soup.find_all('a', class_="meta-title"):
        plink = 'https:' + link.get('href').strip()
        id = int(plink.strip('https://www.marvel.com/comics/issue/').split('/')[0])

        page = None
        for i in range(10):
            try:
                async with aiohttp.ClientSession() as cs:
                    async with cs.get(plink) as r:
                        page = await r.text()
                break
            except aiohttp.ClientPayloadError:
                pass
        if page is None:
            continue

        soup = BeautifulSoup(page, 'html.parser')

        try:
            desc = next(i for i in soup.find_all('p') if 'data-blurb' in i.attrs).get_text().strip()
        except StopIteration:
            continue

        descs[id] = desc

    return descs


async def marvel_crawl(marvel) -> Dict[int, Comic]:
    comics = await marvel_from_API(marvel)
    descs = await marvel_from_soup()
    print(len(comics), len(descs))

    for k, c in comics.items():
        if k in descs:
            if c.description is None:
                comics[k].description = descs[k]

    return comics
