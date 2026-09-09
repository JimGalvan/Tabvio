import json
import re
from typing import Dict, List, Tuple

from playwright.async_api import Page

from tabvio.browser.browser_utils import BrowserUtils

WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    return WHITESPACE.sub(" ", text or "").strip()


def group_identical_frames(frames: Dict[int, str]) -> List[Tuple[List[int], str]]:
    """Collapse frames sharing identical text, ordered by first frame index."""
    frames_by_text: Dict[str, List[int]] = {}
    for index in sorted(frames):
        text = normalize(frames[index])
        if text not in frames_by_text:
            frames_by_text[text] = []
        frames_by_text[text].append(index)

    groups = []
    for text, indexes in frames_by_text.items():
        groups.append((indexes, text))

    groups.sort(key=lambda group: group[0][0])
    return groups


def fair_share(sizes: List[int], budget: int) -> List[int]:
    """Max-min fair allocation.

    Repeatedly hand out an equal share; any frame smaller than its share is
    settled at its true size and its leftover is redistributed to the frames
    still over-share. Means one 2000-char frame can't starve a 20-char one.
    """
    allocations = [0] * len(sizes)
    remaining = budget
    unsettled = set(range(len(sizes)))

    while unsettled and remaining > 0:
        share = remaining // len(unsettled)
        if share == 0:
            break

        fits_in_share = set()
        for i in unsettled:
            if sizes[i] <= share:
                fits_in_share.add(i)

        if not fits_in_share:
            for i in unsettled:
                allocations[i] = share
            break

        for i in fits_in_share:
            allocations[i] = sizes[i]
            remaining -= sizes[i]
        unsettled -= fits_in_share

    return allocations


def drop_repeated_ngrams(text: str, n: int = 5) -> str:
    """Remove any word n-gram already seen earlier in this frame.

    All n words of a repeat are marked, so trailing fragments of a duplicated
    phrase disappear too instead of leaving debris behind.
    """
    words = text.split()
    if len(words) <= n:
        return text

    seen = set()
    is_repeat = [False] * len(words)
    for i in range(len(words) - n + 1):
        ngram = tuple(word.lower() for word in words[i:i + n])
        if ngram in seen:
            for j in range(i, i + n):
                is_repeat[j] = True
        else:
            seen.add(ngram)

    kept = []
    for word, repeated in zip(words, is_repeat):
        if not repeated:
            kept.append(word)
    return " ".join(kept)


def keep_head_and_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text

    head_length = int(limit * 0.45)
    tail_length = limit - head_length
    head = text[:head_length].rstrip()
    tail = text[-tail_length:].lstrip()
    return f"{head} …[{len(text) - limit} chars omitted]… {tail}"


def join_indexes(indexes) -> str:
    return ",".join(str(index) for index in indexes)


async def get_frame_texts(page: Page) -> Dict[int, str]:
    frames: Dict[int, str] = {}
    scan_script = BrowserUtils.get_js_script('scan-page.js')
    for index, iframe in enumerate(page.frames):
        page_text = json.loads(await iframe.evaluate(scan_script))['pageText']
        frames[index] = page_text
    return frames


class PageContent:
    @staticmethod
    async def get_page_content(page: Page, char_budget: int = 500) -> str:
        frames: Dict[int, str] = await get_frame_texts(page)
        filled_groups = []
        empty_indexes = []

        for indexes, text in group_identical_frames(frames):
            if text:
                filled_groups.append((indexes, text))
            else:
                empty_indexes.extend(indexes)

        sizes = []
        for _, text in filled_groups:
            sizes.append(len(text))
        limits = fair_share(sizes, char_budget)

        lines = []
        for (indexes, text), limit in zip(filled_groups, limits):
            if len(text) > limit:
                text = keep_head_and_tail(drop_repeated_ngrams(text), max(limit, 40))
            duplicate_tag = f" (×{len(indexes)} identical)" if len(indexes) > 1 else ""
            lines.append(f"iframe[{join_indexes(indexes)}]{duplicate_tag} {text}")

        if empty_indexes:
            lines.append(f"iframe[{join_indexes(empty_indexes)}] (empty)")

        return "\n".join(lines)
