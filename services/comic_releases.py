from typing import Optional, Any, Dict
from comic_types.logc import ComicData, ComicDetails, ComicRequest
from datetime import datetime

import aiohttp

from config import API_URL


async def fetch_comic_releases(
        date: Optional[str] = None,
        issue: bool = True,
        annual: bool = True,
        digital: bool = True,
        variant: bool = False,
        trade: bool = True,
        hardcover: bool = True,
        publisher: Optional[int] = None
) -> list[ComicData]:
    """
    Fetches the latest comic releases from League of Comic Geeks API.
    """
    params: Dict[str, Any] = {
        "issue": issue,
        "annual": annual,
        "digital": digital,
        "variant": variant,
        "trade": trade,
        "hardcover": hardcover,
    }
    if date:
        params["date"] = date
    if publisher:
        params["publisher"] = publisher

    url = f"{API_URL}/comic/releases"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            comics = []
            for item in data:
                item['date'] = datetime.fromisoformat(item['date'].replace('Z', '')).date()
                comics.append(ComicData(**item))
            return comics


async def fetch_comic_details(comics: list[ComicRequest]) -> list[ComicDetails]:
    """
    Fetches detailed comic information for multiple comics from League of Comic Geeks API.
    comics: List of ComicRequest objects.
    Returns a list of ComicDetails objects.
    """
    url = f"{API_URL}/comic/details"
    payload = [comic.__dict__ for comic in comics]
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            results = []
            for item in data:
                if 'error' not in item:
                    if 'releaseDate' in item and item['releaseDate']:
                        item['releaseDate'] = datetime.fromisoformat(item['releaseDate'].replace('Z', '')).date()
                    results.append(ComicDetails(**item))
            return results


async def fetch_comic_releases_detailed(
    date: Optional[str] = None,
    issue: bool = True,
    annual: bool = True,
    digital: bool = True,
    variant: bool = False,
    trade: bool = True,
    hardcover: bool = True,
    publisher: Optional[int] = None
) -> list[ComicDetails]:
    """
    Fetches comic releases, then fetches detailed info for all releases and returns the ComicDetails list.
    """
    releases = await fetch_comic_releases(
        date=date,
        issue=issue,
        annual=annual,
        digital=digital,
        variant=variant,
        trade=trade,
        hardcover=hardcover,
        publisher=publisher
    )
    return await fetch_comic_details([ComicRequest(
            comicId=comic.id,
            title=comic.titlePath,
            variantId=comic.variantId
        ) for comic in releases])
