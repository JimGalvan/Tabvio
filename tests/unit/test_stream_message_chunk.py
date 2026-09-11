import unittest

from langchain_core.messages import AIMessageChunk

from tabvio.runs.runtime import LangChainAgentRuntime


class StreamMessageChunkTests(unittest.TestCase):
    def test_langchain_ai_message_chunk_is_extracted(self) -> None:
        runtime = LangChainAgentRuntime.__new__(LangChainAgentRuntime)
        message = AIMessageChunk(content="Working")

        result = runtime._message_text((message, {}))

        self.assertEqual(result, "Working")


if __name__ == "__main__":
    unittest.main()
