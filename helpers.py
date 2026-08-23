import textwrap

class Helpers:
    @staticmethod
    def format_page_to_llm_output(data, text_width=50):
        url = data.get('url', '')
        if '?' in url:
            url = url.split('?', 1)[0] + '?...'

        title = data.get('title', '')
        pages_below = float(data.get('pagesBelow', 0))

        lines = [f'<page url="{url}" title="{title}" pages_below="{pages_below}">', "Interactive elements:"]

        for i, element in enumerate(data.get('elements', [])):
            tag = element.get('tag', '')
            attrs = element.get('attrs', '')
            text = element.get('text', '')
            tag_str = f"{tag} {attrs}".strip()
            lines.append(f"[{i}]<{tag_str} /> {text}")

        lines.append("Page text:")
        lines.append(textwrap.fill(data.get('pageText', ''), width=text_width))
        lines.append("</page>")

        return "\n".join(lines)