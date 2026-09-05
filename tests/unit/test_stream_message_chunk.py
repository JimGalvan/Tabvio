import unittest

from langchain_core.messages import AIMessageChunk

from tabvio.runs.service import RunManager


class StreamMessageChunkTests(unittest.TestCase):
    def test_langchain_ai_message_chunk_is_extracted(self) -> None:
        manager = RunManager.__new__(RunManager)
        message = AIMessageChunk(content="Working")

        result = manager._extract_stream_message((message, {}))

        self.assertEqual(result, "Working")


if __name__ == "__main__":
    unittest.main()
