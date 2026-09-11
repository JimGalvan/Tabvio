import asyncio
import logging

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

    @property
    def session_id(self) -> str | None:
        if self._client is None:
            return None
        return self._client.session_id

    async def connect_endpoint(self) -> tuple[str, dict[str, str]]:
        # boto3 is blocking, and this runs inside the run's event loop.
        return await asyncio.to_thread(self._start)

    async def close(self) -> None:
        if self._client is None:
            return

        client = self._client
        self._client = None
        try:
            await asyncio.to_thread(client.stop)
        except Exception:
            # A session left running bills by the minute, so say so loudly.
            logger.warning(
                "Could not stop the AgentCore browser session", exc_info=True
            )

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
