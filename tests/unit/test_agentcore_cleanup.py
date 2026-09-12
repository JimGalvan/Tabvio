import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tabvio.browser.agentcore import AgentCoreBrowser
from tabvio.browser.session import BrowserSession


def test_remote_session_stops_when_playwright_close_fails():
    remote = SimpleNamespace(close=AsyncMock())
    browser = BrowserSession(remote_browser=remote)
    browser._browser = SimpleNamespace(close=AsyncMock(side_effect=ConnectionError("disconnected")))
    with pytest.raises(ConnectionError):
        asyncio.run(browser.close())
    remote.close.assert_awaited_once()


def test_stop_verifies_the_browser_session_is_terminated():
    client = SimpleNamespace(session_id="session", stop=Mock(), data_plane_client=Mock())
    client.data_plane_client.list_browser_sessions.return_value = {
        "items": [{"sessionId": "session", "status": "TERMINATED"}]
    }
    browser = AgentCoreBrowser("us-west-2")
    browser._client = client
    asyncio.run(browser.close())
    client.stop.assert_called_once()
    client.data_plane_client.list_browser_sessions.assert_called_once_with(browserIdentifier="aws.browser.v1")
    assert browser.session_id is None
