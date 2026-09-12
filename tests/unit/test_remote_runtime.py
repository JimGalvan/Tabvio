import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tabvio.remote.runtime import RemoteAgentRuntime


def test_credentials_cannot_escape_the_run_selection():
    selected, other, user = uuid4(), uuid4(), uuid4()
    service = Mock()
    runtime = RemoteAgentRuntime("arn", uuid4(), user, (selected,), service)

    async def check():
        with pytest.raises(ValueError):
            await runtime._handle("credentials.resolve", {"id": str(other), "hostname": "example.com"})
        with pytest.raises(ValueError):
            await runtime._handle("credentials.list", {"ids": [str(other)]})

    asyncio.run(check())
    service.resolve_for_domain.assert_not_called()
    service.require_selected.assert_not_called()


def test_credentials_are_resolved_for_the_authenticated_owner_and_hostname():
    selected, user = uuid4(), uuid4()
    service = Mock()
    service.resolve_for_domain.return_value = SimpleNamespace(model_dump=lambda **kwargs: {"login": "test"})
    runtime = RemoteAgentRuntime("arn", uuid4(), user, (selected,), service)
    result = asyncio.run(runtime._handle("credentials.resolve", {"id": str(selected), "hostname": "example.com"}))
    assert result == {"login": "test"}
    service.resolve_for_domain.assert_called_once_with(selected, user, "example.com")


def test_sensitive_input_state_arrives_before_the_ui_event():
    request_id = uuid4()
    runtime = RemoteAgentRuntime("arn", uuid4(), uuid4(), (), None)

    async def check():
        event = {"kind": "custom", "event_type": "sensitive_input.required", "payload": {}}
        await runtime._handle("event", {
            "pending": {"id": str(request_id), "element_index": 3, "prompt": "Enter code"},
            "event": event,
        })
        assert runtime.sensitive_inputs.require(request_id).element_index == 3
        assert await runtime._events.get() == event

    asyncio.run(check())
