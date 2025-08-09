from dataclasses import dataclass
from typing import Optional, List
from datetime import date


@dataclass
class ComicData:
    id: int
    title: str
    publisher: str
    date: date
    price: float
    coverImage: str
    url: str
    pulls: int
    community: int
    titlePath: str
    variantId: Optional[str] = None
    parentId: Optional[str] = None
    variantName: Optional[str] = None


@dataclass
class Creator:
    name: str
    role: str
    url: str
    type: str


@dataclass
class Character:
    name: str
    url: str
    realName: Optional[str] = None
    type: Optional[str] = None


@dataclass
class Variant:
    id: int
    title: str
    coverImage: str
    url: str
    category: str


@dataclass
class Story:
    title: str
    type: str
    pages: Optional[int] = None
    creators: List[Creator] = None
    characters: List[Character] = None


@dataclass
class ComicRequest:
    comicId: int
    title: str
    variantId: Optional[str] = None


@dataclass
class ComicDetails:
    id: int
    title: str
    issueNumber: str
    publisher: str
    description: str
    coverDate: str
    releaseDate: date
    pages: int
    price: float
    format: str
    upc: Optional[str]
    isbn: Optional[str]
    distributorSku: str
    finalOrderCutoff: str
    coverImage: str
    url: str
    rating: float
    ratingCount: int
    ratingText: str
    pulls: int
    collected: int
    read: int
    wanted: int
    seriesUrl: str
    creators: List[Creator]
    characters: List[Character]
    variants: List[Variant]
    stories: List[Story]
    previousIssueUrl: Optional[str] = None
    nextIssueUrl: Optional[str] = None
