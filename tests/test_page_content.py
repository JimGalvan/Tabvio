import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tabvio_agent.browser.page_content import PageContent

VIEWPORT_HEIGHT = 768


def text_block(text: str, top: float = 10.0, bottom: float = 30.0) -> dict:
    return {
        "text": text,
        "boundingRectangle": {
            "x": 0.0,
            "y": top,
            "width": 100.0,
            "height": bottom - top,
            "top": top,
            "right": 100.0,
            "bottom": bottom,
            "left": 0.0,
        },
    }


def scan(blocks: list[dict], pages_below: float = 0.0) -> dict:
    return {
        "viewport": {"width": 1365, "height": VIEWPORT_HEIGHT},
        "textBlocks": blocks,
        "pagesBelow": pages_below,
    }


def frames(count: int) -> list[Mock]:
    return [Mock(is_detached=Mock(return_value=False)) for _ in range(count)]


class PageContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_frame_timeout_preserves_content_before_and_after_it(self):
        page_frames = frames(4)
        page = SimpleNamespace(frames=page_frames)
        evaluate = AsyncMock(side_effect=[
            TimeoutError("The frame has no execution context"),
            json.dumps(scan([
                text_block("Card number"),
                text_block("Expiration"),
                text_block("Security code"),
            ])),
            json.dumps(scan([])),
        ])
        with patch("tabvio_agent.browser.page_content.evaluate_with_timeout", evaluate):
            content = await PageContent.get_page_content(
                page, page_frames[0], scan([text_block("Checkout")]), char_budget=400,
            )

        self.assertIn("iframe[0] Checkout", content)
        self.assertIn("iframe[1] [Frame unavailable: script evaluation timed out]", content)
        self.assertIn("iframe[2] Card number Expiration Security code", content)
        self.assertIn("iframe[3] (empty)", content)

    async def test_observation_cancellation_is_not_treated_as_missing_content(self):
        page_frames = frames(2)
        page = SimpleNamespace(frames=page_frames)
        with patch(
            "tabvio_agent.browser.page_content.evaluate_with_timeout",
            AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await PageContent.get_page_content(
                    page, page_frames[0], scan([text_block("Checkout")]),
                )

    async def test_only_the_text_on_screen_is_reported(self):
        page_frames = frames(1)
        page = SimpleNamespace(frames=page_frames)
        scanned = scan([
            text_block("Above the screen", top=-80.0, bottom=-40.0),
            text_block("On the screen", top=100.0, bottom=140.0),
            text_block("Below the screen", top=900.0, bottom=940.0),
        ])

        content = await PageContent.get_page_content(page, page_frames[0], scanned)

        self.assertIn("On the screen", content)
        self.assertNotIn("Above the screen", content)
        self.assertNotIn("Below the screen", content)

    async def test_a_single_frame_is_reported_without_an_iframe_label(self):
        page_frames = frames(1)
        page = SimpleNamespace(frames=page_frames)

        content = await PageContent.get_page_content(
            page, page_frames[0], scan([text_block("Checkout")]),
        )

        self.assertEqual(content, "Checkout")

    async def test_pages_left_below_the_screen_are_offered_to_the_scroll_tool(self):
        page_frames = frames(1)
        page = SimpleNamespace(frames=page_frames)

        content = await PageContent.get_page_content(
            page, page_frames[0], scan([text_block("Checkout")], pages_below=1.44),
        )

        self.assertEqual(
            content,
            "[1.4 pages below the screen. use 'scroll' tool to scroll down]\nCheckout",
        )

    async def test_text_furthest_from_the_middle_of_the_screen_is_dropped_first(self):
        page_frames = frames(1)
        page = SimpleNamespace(frames=page_frames)
        scanned = scan([
            text_block("T" * 30, top=0.0, bottom=20.0),
            text_block("M" * 30, top=374.0, bottom=394.0),
            text_block("B" * 30, top=740.0, bottom=760.0),
        ])

        content = await PageContent.get_page_content(
            page, page_frames[0], scanned, char_budget=40,
        )

        self.assertEqual(
            content,
            f"…[30 chars omitted above]\n{'M' * 30}\n…[30 chars omitted below]",
        )
