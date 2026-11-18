import html
from dataclasses import dataclass

from tabvio_agent.browser.constants import MAX_ELEMENTS


@dataclass
class Bounds:
    x: float
    y: float
    width: float
    height: float
    top: float
    right: float
    bottom: float
    left: float


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    def fits_inside(self, width: int, height: int) -> bool:
        return self.x >= 0 and self.y >= 0 and self.x + self.width <= width and self.y + self.height <= height


@dataclass
class Viewport:
    width: int
    height: int

    def check_if_on_screen(self, bounds: Bounds) -> bool:
        is_above = bounds.bottom <= 0
        is_below = bounds.top >= self.height
        return not (is_above or is_below)


@dataclass
class Element:
    index: int
    signature: str
    tag: str
    text: str
    attrs: str
    cx: float
    cy: float
    element_bounds: Bounds


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

    def __str__(self) -> str:
        parts = [self.id]
        if self.selected:
            parts.append("[selected]")
        parts.append(f"title={self.title!r}")
        parts.append(f"url={self.url!r}")
        return " ".join(parts)


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


@dataclass
class Observation:
    url: str
    title: str
    pages_below: float
    elements: list[Element]
    page_content: str
    tabs: list[Tab]
    iframes: list[Iframe]
    elements_outside_viewport_count: int = 0
    payment_notice: str = ""

    def __str__(self) -> str:
        # Hide the query string and escape page details
        path, is_query, _ = self.url.partition("?")
        url = html.escape(f"{path}?..." if is_query else self.url)
        title = html.escape(self.title)

        # Format each element with whitespace-collapsed text
        formatted_elements = []
        for element in self.elements[:MAX_ELEMENTS]:
            text_lines = []
            for text_line in element.text.splitlines():
                text_no_spaces = " ".join(text_line.split())
                if text_no_spaces:
                    text_lines.append(text_no_spaces)

            text = html.escape("\n".join(text_lines), quote=False)
            attrs = html.escape(element.attrs.strip(), quote=False)
            opening_tag = f"{element.tag.strip()} {attrs}".strip()
            formatted_element = f"[{element.index}] <{opening_tag}>"
            if text:
                formatted_element += f" {text}"
            formatted_elements.append(formatted_element)

        # Note any elements cut off by the limit
        omitted_count = len(self.elements) - MAX_ELEMENTS
        if omitted_count > 0:
            formatted_elements.append(f"... {omitted_count} additional elements omitted")

        elements = "\n".join(formatted_elements) if formatted_elements else "(none)"
        if self.elements_outside_viewport_count:
            elements += (
                f"\n({self.elements_outside_viewport_count} more elements outside the screen, "
                "use page-navigator to reach them)"
            )

        tab_lines = []
        for tab in self.tabs:
            tab_lines.append(str(tab))
        tabs = "\n".join(tab_lines)

        iframe_lines = []
        for iframe in self.iframes:
            iframe_lines.append(str(iframe))
        iframes = "\n".join(iframe_lines)

        sections = [
            f'<page url="{url}" title="{title}" pages_below="{self.pages_below:.1f}">\n\n'
            f"Interactive elements:\n{elements}\n\n</page>",
            f"<viewport-text>{self.page_content}</viewport-text>",
            f"<available-tabs>\n{tabs}\n</available-tabs>",
        ]
        has_only_main_frame = len(self.iframes) <= 1
        if not has_only_main_frame:
            sections.append(f"<available-iframes>\n{iframes}\n</available-iframes>")
        if self.payment_notice:
            sections.append(self.payment_notice)
        return "\n".join(sections)
