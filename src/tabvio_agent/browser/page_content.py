import json
import re
from dataclasses import dataclass

from playwright.async_api import Frame, Page

from tabvio_agent.browser.browser_utils import evaluate_with_timeout, get_js_script
from tabvio_agent.browser.models import Bounds, Viewport

WHITESPACE = re.compile(r"\s+")
NGRAM_SIZE = 5
HEAD_SHARE = 0.45
MIN_SHORTENED_LENGTH = 40
SCROLL_HINT_MIN_PAGES = 0.05
TIMED_OUT_FRAME_TEXT = "[Frame unavailable: script evaluation timed out]"


@dataclass
class TextBlock:
    top: float
    bottom: float
    text: str

    def middle(self) -> float:
        return (self.top + self.bottom) / 2


def normalize(text: str) -> str:
    return WHITESPACE.sub(" ", text or "").strip()


def join_block_texts(blocks: list[TextBlock]) -> str:
    texts = []
    for block in blocks:
        texts.append(block.text)
    return normalize(" ".join(texts))


def join_indexes(indexes: list[int]) -> str:
    index_strings = []
    for index in indexes:
        index_strings.append(str(index))
    return ",".join(index_strings)


def group_identical_frames(
    frame_blocks: dict[int, list[TextBlock]]
) -> list[tuple[list[int], list[TextBlock]]]:
    """Collapse frames sharing identical text, ordered by first frame index."""
    indexes_by_text: dict[str, list[int]] = {}
    blocks_by_text: dict[str, list[TextBlock]] = {}
    for index in sorted(frame_blocks):
        blocks = frame_blocks[index]
        text = join_block_texts(blocks)
        if text not in indexes_by_text:
            indexes_by_text[text] = []
            blocks_by_text[text] = blocks
        indexes_by_text[text].append(index)

    groups = []
    for text, indexes in indexes_by_text.items():
        groups.append((indexes, blocks_by_text[text]))
    return groups


def fair_share(sizes: list[int], budget: int) -> list[int]:
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
        for index in unsettled:
            if sizes[index] <= share:
                fits_in_share.add(index)

        if not fits_in_share:
            for index in unsettled:
                allocations[index] = share
            break

        for index in fits_in_share:
            allocations[index] = sizes[index]
            remaining -= sizes[index]
        unsettled -= fits_in_share

    return allocations


def drop_repeated_ngrams(text: str, seen_ngrams: set[tuple[str, ...]], n: int = NGRAM_SIZE) -> str:
    """Remove any word n-gram already seen earlier in this frame.

    All n words of a repeat are marked, so trailing fragments of a duplicated
    phrase disappear too instead of leaving debris behind.
    """
    words = text.split()
    if len(words) < n:
        return text

    lowered_words = []
    for word in words:
        lowered_words.append(word.lower())

    is_repeat = [False] * len(words)
    for start in range(len(words) - n + 1):
        ngram = tuple(lowered_words[start:start + n])
        if ngram in seen_ngrams:
            for position in range(start, start + n):
                is_repeat[position] = True
        else:
            seen_ngrams.add(ngram)

    kept_words = []
    for word, repeated in zip(words, is_repeat):
        if not repeated:
            kept_words.append(word)
    return " ".join(kept_words)


def keep_head_and_tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text

    head_length = int(limit * HEAD_SHARE)
    tail_length = limit - head_length
    head = text[:head_length].rstrip()
    tail = text[-tail_length:].lstrip()
    omitted_count = len(text) - limit
    return f"{head} …[{omitted_count} chars omitted]… {tail}"


def remove_repeated_phrases(blocks: list[TextBlock]) -> list[TextBlock]:
    seen_ngrams = set()
    deduplicated_blocks = []
    for block in blocks:
        text = drop_repeated_ngrams(block.text, seen_ngrams)
        if text:
            deduplicated_blocks.append(TextBlock(block.top, block.bottom, text))
    return deduplicated_blocks


def trim_vertically(blocks: list[TextBlock], viewport_height: float, limit: int) -> str:
    """Fit the text into limit by dropping the blocks farthest from the middle of the screen."""
    text = join_block_texts(blocks)
    if len(text) <= limit:
        return text

    blocks = remove_repeated_phrases(blocks)
    limit = max(limit, MIN_SHORTENED_LENGTH)
    center = viewport_height / 2

    distances = []
    for index, block in enumerate(blocks):
        distances.append((abs(block.middle() - center), index))
    distances.sort()

    kept_indexes = set()
    used_length = 0
    for _, index in distances:
        added_length = len(blocks[index].text) + 1
        if used_length + added_length > limit:
            break
        kept_indexes.add(index)
        used_length += added_length

    oversized_index = None
    if not kept_indexes:
        oversized_index = distances[0][1]

    omitted_above = 0
    omitted_below = 0
    kept_texts = []
    for index, block in enumerate(blocks):
        if index in kept_indexes:
            kept_texts.append(block.text)
        elif index == oversized_index:
            kept_texts.append(keep_head_and_tail(block.text, limit))
        elif block.middle() < center:
            omitted_above += len(block.text)
        else:
            omitted_below += len(block.text)

    lines = []
    if omitted_above:
        lines.append(f"…[{omitted_above} chars omitted above]")
    lines.append(" ".join(kept_texts))
    if omitted_below:
        lines.append(f"…[{omitted_below} chars omitted below]")
    return "\n".join(lines)


def describe_scroll(pages_below: float) -> str:
    if pages_below <= SCROLL_HINT_MIN_PAGES:
        return ""
    return (
        f"[{pages_below:.1f} pages below the screen. use 'scroll' tool to scroll down]\n"
    )


def frame_label(indexes: list[int]) -> str:
    label = f"iframe[{join_indexes(indexes)}]"
    if len(indexes) > 1:
        label += f" (×{len(indexes)} identical)"
    return label


def get_blocks_in_viewport(scan_result: dict) -> list[TextBlock]:
    viewport = Viewport(**scan_result["viewport"])
    blocks = []
    for block in scan_result["textBlocks"]:
        bounds = Bounds(**block["boundingRectangle"])
        is_on_screen = viewport.check_if_on_screen(bounds)
        if is_on_screen:
            blocks.append(TextBlock(top=bounds.top, bottom=bounds.bottom, text=block["text"]))
    return blocks


async def scan_frames(
    page: Page, scanned_frame: Frame, scanned_result: dict
) -> tuple[dict[int, list[TextBlock]], dict[int, float], dict[int, float]]:
    """Return each frame's on-screen text blocks, its screen height, and how many screens of it are below the fold."""
    frame_blocks: dict[int, list[TextBlock]] = {}
    height_by_frame: dict[int, float] = {}
    pages_below_by_frame: dict[int, float] = {}
    scan_script = get_js_script("scan-page.js")

    for index, frame in enumerate(page.frames):
        if frame.is_detached():
            continue

        if frame is scanned_frame:
            scan_result = scanned_result
        else:
            try:
                scan_result = json.loads(await evaluate_with_timeout(frame, scan_script))
            except TimeoutError:
                frame_blocks[index] = [TextBlock(top=0, bottom=0, text=TIMED_OUT_FRAME_TEXT)]
                height_by_frame[index] = 0
                continue

        frame_blocks[index] = get_blocks_in_viewport(scan_result)
        height_by_frame[index] = float(scan_result["viewport"]["height"])
        pages_below_by_frame[index] = float(scan_result.get("pagesBelow", 0))

    return frame_blocks, height_by_frame, pages_below_by_frame


class PageContent:
    @staticmethod
    async def get_page_content(
        page: Page, scanned_frame: Frame, scanned_result: dict, char_budget: int = 500
    ) -> str:
        frame_blocks, height_by_frame, pages_below_by_frame = await scan_frames(page, scanned_frame, scanned_result)

        filled_groups = []
        empty_indexes = []
        for indexes, blocks in group_identical_frames(frame_blocks):
            if join_block_texts(blocks):
                filled_groups.append((indexes, blocks))
            else:
                empty_indexes.extend(indexes)

        text_lengths = []
        for _, blocks in filled_groups:
            text_lengths.append(len(join_block_texts(blocks)))
        limits = fair_share(text_lengths, char_budget)

        has_only_main_frame = len(frame_blocks) == 1
        lines = []
        for (indexes, blocks), limit in zip(filled_groups, limits):
            first_frame_index = indexes[0]
            trimmed_text = trim_vertically(blocks, height_by_frame[first_frame_index], limit)
            scroll_hint = describe_scroll(pages_below_by_frame.get(first_frame_index, 0.0))
            if has_only_main_frame:
                lines.append(f"{scroll_hint}{trimmed_text}")
            else:
                lines.append(f"{frame_label(indexes)} {scroll_hint}{trimmed_text}")

        if empty_indexes:
            lines.append(f"iframe[{join_indexes(empty_indexes)}] (empty)")

        return "\n".join(lines)
