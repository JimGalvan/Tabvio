MJPEG_BOUNDARY = "tabvio-frame"
CONTROL_CLOSE_UNAUTHENTICATED = 1008
CONTROL_CLOSE_RUN_NOT_FOUND = 4404
CONTROL_CLOSE_RUN_NOT_WAITING = 4409

CONTROL_MESSAGE_NOT_JSON = "That message was not JSON"
CONTROL_EVENT_NOT_UNDERSTOOD = "That control event was not understood"

NO_CACHE = "no-cache"
NO_CACHE_HEADERS = {"cache-control": NO_CACHE}
SSE_STREAM_HEADERS = {
    "Cache-Control": NO_CACHE,
    "X-Accel-Buffering": "no",
}
SCREEN_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "X-Accel-Buffering": "no",
}
