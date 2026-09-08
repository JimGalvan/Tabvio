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
