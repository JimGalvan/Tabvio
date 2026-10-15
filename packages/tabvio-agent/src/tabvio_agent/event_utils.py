from dataclasses import fields
from datetime import datetime
from typing import Any, get_args
from uuid import UUID

from tabvio_agent.events import AgentEvent
from tabvio_agent.models import Todo


class EventUtils:
    @staticmethod
    def from_payload(event_type: str, payload: dict[str, Any]) -> AgentEvent:
        values = dict(payload)
        if "request_id" in values:
            values["request_id"] = UUID(str(values["request_id"]))
        if "expires_at" in values and isinstance(values["expires_at"], str):
            values["expires_at"] = datetime.fromisoformat(values["expires_at"])
        if "payment_signals" in values:
            values["payment_signals"] = tuple(values["payment_signals"])
        if "todos" in values:
            todos = []
            for todo in values["todos"]:
                todos.append(Todo(**todo))
            values["todos"] = tuple(todos)
        for event_class in get_args(AgentEvent):
            for event_field in fields(event_class):
                if event_field.name == "event_type" and event_field.default == event_type:
                    return event_class(**values)
        raise ValueError(f"Unknown agent event type: {event_type}")
