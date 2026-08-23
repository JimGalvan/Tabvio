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