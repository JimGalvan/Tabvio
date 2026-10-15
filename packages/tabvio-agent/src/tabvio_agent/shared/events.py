from typing import Any

from langgraph.config import get_stream_writer


def publish_custom_event(event_type: str, payload: dict[str, Any]) -> None:
    writer = get_stream_writer()
    writer({"event_type": event_type, "payload": payload})
