from datetime import datetime
from typing import Dict

import aiohttp
from bs4 import BeautifulSoup, Tag, NavigableString

from objects.comic import Comic
from objects.configuration import Brand


async def dc_from_soup():
    base_link = "https://www.dc.com"

    async with aiohttp.ClientSession() as cs:
        async with cs.get(base_link + "/comics") as r:
            page = await r.text()
    soup = BeautifulSoup(page, 'html.parser')

    comics = {}

    links = soup.find('ul', class_="react-multi-carousel-track content-tray-slider")
    for item in links.contents:
        branch = item.findChild(class_="card-button usePointer").get("href")
        link = base_link + branch

        page = None
        for i in range(10):
            try:
                async with aiohttp.ClientSession() as cs:
                    async with cs.get(link) as r:
                        page = await r.text()
                break
            except aiohttp.ClientPayloadError:
                pass
        if page is None:
            continue

        soup = BeautifulSoup(page, 'html.parser')

        txt = soup.find_all(class_="sc-g8nqnn-0")

        if not txt:
            continue
        c_type = ''.join(txt[0].find('p', class_='text-left').contents).strip()
        if c_type != "COMIC BOOK":
            continue
        title = ''.join(txt[0].find('h1', class_='text-left').contents).strip()

        desc = None
        if len(txt) > 1:
            if txt[1].find('p'):
                desc_list = get_desc(txt[1])
                desc = '\n'.join(i.strip() for i in ''.join(desc_list).split('\n') if i.strip())

        details_list = [i.contents for i in soup.find_all('div', class_="sc-b3fnpg-3")]
        details = {}
        for d in details_list:
            for dd in d:
                d_id = dd['id'][len('page151-band11690-Subitem2847'):]
                x = None
                if '-' in d_id:
                    d_id, x = d_id.split('-')
                    x = None if d_id not in ['24', '12'] else x
                if d_id not in details:
                    details[d_id] = []
                details[d_id] += [i.contents[0].contents[0] if x else i.contents[0] for i in dd.contents if
                                  type(i) == Tag]

        creators = {}
        if '24' in details:
            creators["Writer"] = [str(i) for i in details['24']]
        if '12' in details:
            creators["Artist"] = [str(i) for i in details['12']]

        get_from_det = lambda x: str(details[x][0]) if x in details else None
        price = get_from_det('33')
        price = 0 if price == "FREE" else (float(price) if price else None)
        date = get_from_det('36')
        date = datetime.strptime(date.replace('st', 'th').replace('nd,', 'th,').replace('rd', 'th').replace('Auguth', 'August'), '%A, %B %dth, %Y') if date else None
        page_count = get_from_det('48')

        img = soup.find('img', id="page151-band11672-Card11673-img")
        image = img['src'].split('?')[0]
        image = image if image else None

        copyright = str(soup.find('div', class_="small legal d-inline-block").contents[0].contents[0])

        c = Comic(Brand.DC, ''.join(i for i in title if i.isalnum()),
                     title, desc,
                     creators, image, link,
                     page_count, price, copyright, date)

        comics[c.id] = c
    return comics


async def dc_crawl() -> Dict[int, Comic]:
    comics = await dc_from_soup()
    return comics


def get_desc(t: Tag):
    strings = []
    for i in t.contents:
        if type(i) == Tag:
            if i.name in ['p', 'em']:
                strings += get_desc(i)
        elif type(i) == NavigableString:
            s = str(i)
            if t.name == 'em':
                s = (" " if s.startswith(" ") else "") + \
                    f"*{s.strip()}*" + \
                    (" " if s.endswith(" ") else "")
            strings.append(s)
    return strings
