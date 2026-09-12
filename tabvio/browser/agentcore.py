import asyncio
import logging
import time

from bedrock_agentcore.tools.browser_client import BrowserClient

from tabvio.browser.constants import VIEWPORT_HEIGHT, VIEWPORT_WIDTH

logger = logging.getLogger(__name__)


class AgentCoreBrowser:
    def __init__(
            self,
            region: str,
            identifier: str = "aws.browser.v1",
            session_timeout_seconds: int = 3600,
    ):
        self._region = region
        self._identifier = identifier
        self._session_timeout_seconds = session_timeout_seconds
        self._client: BrowserClient | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def session_id(self) -> str | None:
        if self._client is None:
            return None
        return self._client.session_id

    async def connect_endpoint(self) -> tuple[str, dict[str, str]]:
        async with self._lifecycle_lock:
            start = asyncio.create_task(asyncio.to_thread(self._start))
            try:
                return await asyncio.shield(start)
            except asyncio.CancelledError:
                await start
                raise

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._client is None:
                return
            await asyncio.to_thread(self._stop_and_verify, self._client)
            self._client = None

    def _stop_and_verify(self, client):
        session_id = client.session_id
        if session_id is None:
            return
        for attempt in range(3):
            try:
                client.stop()
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1)
        for _ in range(10):
            response = client.data_plane_client.list_browser_sessions(browserIdentifier=self._identifier)
            sessions = response.get("items", [])
            while response.get("nextToken"):
                response = client.data_plane_client.list_browser_sessions(
                    browserIdentifier=self._identifier, nextToken=response["nextToken"]
                )
                sessions.extend(response.get("items", []))
            session = next((item for item in sessions if item["sessionId"] == session_id), None)
            if session is None or session["status"] == "TERMINATED":
                return
            time.sleep(1)
        raise RuntimeError("The AgentCore browser session did not terminate")

    def _start(self) -> tuple[str, dict[str, str]]:
        self._client = BrowserClient(region=self._region)
        self._client.start(
            identifier=self._identifier,
            session_timeout_seconds=self._session_timeout_seconds,
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        )
        logger.info("Started AgentCore browser session %s", self._client.session_id)
        return self._client.generate_ws_headers()

    def live_view_url(self, expires: int = 300) -> str | None:
        if self._client is None:
            return None
        return self._client.generate_live_view_url(expires=expires)
