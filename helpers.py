import html
import re

MAX_PAGE_TEXT_CHARS = 12_000
MAX_ELEMENTS = 100

class Helpers:
    @staticmethod
    def truncate(text: str, max_chars: int) -> tuple[str, bool]:
        if len(text) <= max_chars:
            return text, False

        return text[:max_chars].rstrip(), True

    @staticmethod
    def normalize_page_text(text: str) -> str:
        normalized_lines = []

        for line in text.splitlines():
            line = re.sub(r"\s+", " ", line).strip()

            if line:
                normalized_lines.append(line)

        return "\n".join(normalized_lines)

    @staticmethod
    def format_page_to_llm_output(data: dict) -> str:
        url = data.get("url", "")

        if "?" in url:
            url = url.split("?", 1)[0] + "?..."

        title = data.get("title", "")
        pages_below = float(data.get("pagesBelow", 0))

        url = html.escape(url, quote=True)
        title = html.escape(title, quote=True)

        elements = data.get("elements", [])
        visible_elements = elements[:MAX_ELEMENTS]

        lines = [
            (
                f'<page url="{url}" '
                f'title="{title}" '
                f'pages_below="{pages_below:.1f}">'
            ),
            "",
            "Interactive elements:",
        ]

        if visible_elements:
            for index, element in enumerate(visible_elements):
                tag = element.get("tag", "").strip()
                attrs = element.get("attrs", "").strip()
                text = Helpers.normalize_page_text(element.get("text", ""))

                tag_str = f"{tag} {attrs}".strip()

                if text:
                    lines.append(f"[{index}] <{tag_str}> {text}")
                else:
                    lines.append(f"[{index}] <{tag_str}>")

            if len(elements) > MAX_ELEMENTS:
                lines.append(
                    f"... {len(elements) - MAX_ELEMENTS} additional elements omitted"
                )
        else:
            lines.append("(none)")

        page_text = Helpers.normalize_page_text(data.get("pageText", ""))
        page_text, truncated = Helpers.truncate(page_text, MAX_PAGE_TEXT_CHARS)

        lines.extend(
            [
                "",
                "Page text:",
                page_text or "(none)",
            ]
        )

        if truncated:
            lines.append("\n[Page text truncated]")

        lines.extend(
            [
                "",
                "</page>",
            ]
        )
        return "\n".join(lines)
