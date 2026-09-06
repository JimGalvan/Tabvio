from dataclasses import dataclass


@dataclass
class Element:
    index: int
    signature: str
    tag: str
    text: str
    attrs: str
    cx: float
    cy: float


@dataclass
class Tab:
    id: str
    selected: bool
    title: str
    url: str


@dataclass
class Iframe:
    id: str
    selected: bool
    main: bool
    name: str
    url: str
