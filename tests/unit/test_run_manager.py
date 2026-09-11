import unittest

from tabvio.runs.runtime import LangChainAgentRuntime


class Message:
    def __init__(self, message_type: str, content: str):
        self.type = message_type
        self.content = content


class RunManagerMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._runtime = LangChainAgentRuntime.__new__(LangChainAgentRuntime)

    def test_assistant_message_is_extracted(self) -> None:
        message = Message("ai", "Task complete")

        result = self._runtime._message_text((message, {}))

        self.assertEqual(result, "Task complete")

    def test_tool_message_is_not_exposed_as_assistant_output(self) -> None:
        message = Message("tool", "<page>private observation</page>")

        result = self._runtime._message_text((message, {}))

        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
