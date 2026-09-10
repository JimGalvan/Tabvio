from collections import Counter
from dataclasses import dataclass
from typing import Any


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
class Observation:
    page_state: str
    page_snapshot: Counter[Any]


@dataclass(frozen=True)
class PaymentSignal:
    type: str
    value: str


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

    def __str__(self) -> str:
        name = " ".join(self.name.split())
        url = self.url

        if len(name) > 40:
            name = name[:40] + "…"

        if len(url) > 100:
            url = url[:100] + "…"

        parts = [self.id]

        if self.selected:
            parts.append("[selected]")
        if self.main:
            parts.append("[main]")
        if name:
            parts.append(f"name={name!r}")

        parts.append(f"url={url!r}")

        return " ".join(parts)
