import html
import re

MAX_PAGE_TEXT_CHARS = 3_000
MAX_ELEMENTS = 100


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False

    return text[:max_chars].rstrip(), True


def normalize_page_text(text: str) -> str:
    normalized_lines = []

    for line in text.splitlines():
        collapsed = re.sub(r"\s+", " ", line).strip()

        if collapsed:
            normalized_lines.append(collapsed)

    return "\n".join(normalized_lines)


def hide_query_string(url: str) -> str:
    if "?" not in url:
        return url

    path = url.split("?", 1)[0]
    return f"{path}?..."


def format_element(index: int, element: dict) -> str:
    tag = element.get("tag", "").strip()
    attrs = html.escape(element.get("attrs", "").strip(), quote=False)
    text = html.escape(normalize_page_text(element.get("text", "")), quote=False)

    opening_tag = f"{tag} {attrs}".strip()

    if text:
        return f"[{index}] <{opening_tag}> {text}"

    return f"[{index}] <{opening_tag}>"


def format_scan_for_llm(scan: dict) -> str:
    url = html.escape(hide_query_string(scan.get("url", "")), quote=True)
    title = html.escape(scan.get("title", ""), quote=True)
    pages_below = float(scan.get("pagesBelow", 0))

    elements = scan.get("elements", [])
    shown_elements = elements[:MAX_ELEMENTS]
    omitted_count = len(elements) - len(shown_elements)

    lines = [
        f'<page url="{url}" title="{title}" pages_below="{pages_below:.1f}">',
        "",
        "Interactive elements:",
    ]

    if not shown_elements:
        lines.append("(none)")
    else:
        for index, element in enumerate(shown_elements):
            lines.append(format_element(index, element))

        if omitted_count:
            lines.append(f"... {omitted_count} additional elements omitted")

    lines.append("")
    lines.append("</page>")

    return "\n".join(lines)
