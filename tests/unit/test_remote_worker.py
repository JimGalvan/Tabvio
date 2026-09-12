from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from starlette.testclient import TestClient

from tabvio.remote import worker


def test_worker_rejects_unknown_browser_commands_and_closes_on_disconnect(monkeypatch):
    browser = SimpleNamespace(close=AsyncMock(), capture_screen_frame=AsyncMock(return_value=b"image"))
    runtime = SimpleNamespace(browser=browser)
    monkeypatch.setattr(worker, "build_local_agent_runtime", lambda *args: runtime)
    with TestClient(worker.app) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"id": "1", "method": "initialize", "params": {"thread_id": str(uuid4())}})
            assert socket.receive_json()["result"]["ready"]
            socket.send_json({"id": "2", "method": "capture_screen_frame", "params": {"args": [45]}})
            assert socket.receive_json()["result"] == "aW1hZ2U="
            socket.send_json({"id": "3", "method": "__getattribute__", "params": {}})
            assert "error" in socket.receive_json()
    browser.close.assert_awaited()


def test_worker_streams_and_resumes_the_same_agent(monkeypatch):
    class Runtime:
        def __init__(self):
            self.browser = SimpleNamespace(close=AsyncMock())
            self.sensitive_inputs = SimpleNamespace(pending=None)
            self.inputs = []

        def start_input(self, value):
            return value

        def resume_input(self, value):
            return {"answer": value}

        async def stream(self, value):
            self.inputs.append(value)
            yield {"kind": "message", "text": "working"}

        async def final_output(self):
            return "done"

    runtime = Runtime()
    monkeypatch.setattr(worker, "build_local_agent_runtime", lambda *args: runtime)
    with TestClient(worker.app) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"id": "1", "method": "initialize", "params": {"thread_id": str(uuid4())}})
            socket.receive_json()
            for resume in [False, True]:
                socket.send_json({"id": "2", "method": "stream", "params": {"input": "hello", "resume": resume}})
                event = socket.receive_json()
                assert event["params"]["event"]["text"] == "working"
                socket.send_json({"id": event["id"], "result": None})
                assert socket.receive_json()["result"]["output"] == "done"
    assert runtime.inputs == ["hello", {"answer": "hello"}]
