import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tabvio.browser.page_content import PageContent


class PageContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_frame_timeout_preserves_content_before_and_after_it(self):
        page = SimpleNamespace(frames=[
            Mock(is_detached=Mock(return_value=False)) for _ in range(4)
        ])
        evaluate = AsyncMock(side_effect=[
            json.dumps({"pageText": "Checkout"}),
            TimeoutError("The frame has no execution context"),
            json.dumps({"pageText": "Card number Expiration Security code"}),
            json.dumps({"pageText": ""}),
        ])
        with patch("tabvio.browser.page_content.evaluate_with_timeout", evaluate):
            content = await PageContent.get_page_content(page, char_budget=400)

        self.assertIn("iframe[0] Checkout", content)
        self.assertIn("iframe[1] [Frame unavailable: script evaluation timed out]", content)
        self.assertIn("iframe[2] Card number Expiration Security code", content)
        self.assertIn("iframe[3] (empty)", content)

    async def test_observation_cancellation_is_not_treated_as_missing_content(self):
        page = SimpleNamespace(frames=[Mock(is_detached=Mock(return_value=False))])
        with patch(
            "tabvio.browser.page_content.evaluate_with_timeout",
            AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await PageContent.get_page_content(page)
