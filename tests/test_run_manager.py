import unittest

from run_manager import RunManager


class Message:
    def __init__(self, message_type: str, content: str):
        self.type = message_type
        self.content = content


class RunManagerMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._manager = RunManager.__new__(RunManager)

    def test_assistant_message_is_extracted(self) -> None:
        message = Message("ai", "Task complete")

        result = self._manager._extract_stream_message((message, {}))

        self.assertEqual(result, "Task complete")

    def test_tool_message_is_not_exposed_as_assistant_output(self) -> None:
        message = Message("tool", "<page>private observation</page>")

        result = self._manager._extract_stream_message((message, {}))

        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
