import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tabvio_agent.browser.session import BrowserSession


class BrowserFrameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.main_frame = SimpleNamespace(
            name="", url="https://shop.example", is_detached=Mock(return_value=False),
        )
        self.child_frame = SimpleNamespace(
            name="frame-a", url="https://shop.example/form", is_detached=Mock(return_value=False),
        )
        self.page = SimpleNamespace(
            main_frame=self.main_frame, frames=[self.main_frame, self.child_frame],
            is_closed=Mock(return_value=False),
        )
        self.browser = BrowserSession()
        self.browser._reset_page_state(self.page)
        self.browser._collect_iframes()

    async def test_switching_to_a_tracked_frame_and_back_invalidates_element_indices(self):
        for frame_id, frame in (("frame:1", self.child_frame), ("frame:0", self.main_frame)):
            with self.subTest(frame_id=frame_id):
                self.browser._elements = [object()]

                result = await self.browser.switch_to_iframe(frame_id)

                self.assertEqual(result, f"Switched to frame {frame_id} with url {frame.url}")
                self.assertIs(self.browser._active_frame(), frame)
                self.assertIs(self.browser._page, self.page)
                self.assertEqual(self.browser._elements, [])
                selected_ids = []
                for available_frame in self.browser._collect_iframes():
                    if available_frame.selected:
                        selected_ids.append(available_frame.id)
                self.assertEqual(selected_ids, [frame_id])

    async def test_missing_and_detached_frames_preserve_selection_and_elements(self):
        self.child_frame.is_detached.return_value = True
        elements = [object()]
        self.browser._elements = elements
        for frame_id in ("frame:missing", "frame:1"):
            with self.subTest(frame_id=frame_id):
                result = await self.browser.switch_to_iframe(frame_id)

                self.assertEqual(result, f"Frame with id {frame_id} not found")
                self.assertIs(self.browser._active_frame(), self.main_frame)
                self.assertIs(self.browser._elements, elements)
