import json
from typing import Dict, List

import aiohttp
from bs4 import BeautifulSoup

from objects.comic import comic_obj_from_marvel, Comic
from objects.configuration import Brand


async def marvel_from_API(marvel):
    raw = await marvel.get_comics(format='comic', dateDescriptor='thisWeek', limit=100)

    m_copyright = raw.dict['attributionText']

    comics = [comic_obj_from_marvel(c) for c in raw.data.results]
    for c in comics:
        c.brand = Brand.MARVEL
        c.copyright = m_copyright
    return {c.id: c for c in comics}

async def marvel_page_from_soup_to_json(url):
    async with aiohttp.ClientSession() as cs:
        async with cs.get(url) as r:
            page = await r.text()
    soup = BeautifulSoup(page, 'html.parser')

    script_tag = soup.find('script', text=lambda t: t and '__marvel-fitt__' in t)
    if script_tag is None:
        return {}

    script_content = script_tag.string
    json_data = script_content.split('window[\'__marvel-fitt__\']=', 1)[1].rsplit(';', 1)[0]
    return json.loads(json_data)

async def marvel_from_soup(issues: List[int] = None) -> Dict[int, str]:
    descs: Dict[int, str] = {}

    data = await marvel_page_from_soup_to_json("https://marvel.com/comics/calendar/")
    content_dict = data['page']['content']['allComicsReleases']['content']
    urls = [c['url'] for c in content_dict]

    for url in urls:
        if issues is not None:
            issue_id = int(url.strip('https://www.marvel.com/comics/issue/').split('/')[0])
            if issue_id not in issues:
                continue

        data = await marvel_page_from_soup_to_json(url)
        if data is None:
            continue

        issue_details = data['page']['content']['issueDetails']
        issue_id = issue_details['id']
        issue_desc = issue_details['desc']
        descs[issue_id] = issue_desc

    return descs


async def marvel_from_soup_old():
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
    descs = await marvel_from_soup([c.id for c in comics.values() if c.description is None])
    print(len(comics), len(descs))

    for k, c in comics.items():
        if k in descs:
            if c.description is None:
                comics[k].description = descs[k]

    return comics
