import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from run_manager import (
    RunAlreadyActiveError,
    RunManager,
    RunNotFoundError,
    RunNotWaitingForInputError,
)
from run_models import CreateRunRequest, RunEvent, RunResponse, UserInputRequest
from run_repository import RunRepository

BASE_DIRECTORY = Path(__file__).resolve().parent
STATIC_DIRECTORY = BASE_DIRECTORY / "static"
DATABASE_PATH = BASE_DIRECTORY / "data" / "tabvio.db"
MJPEG_BOUNDARY = "tabvio-frame"


def _read_headless_setting() -> bool:
    configured_value = os.getenv("TABVIO_HEADLESS", "true").strip().lower()
    return configured_value not in {"false", "0", "no"}


repository = RunRepository(DATABASE_PATH)
run_manager = RunManager(repository, headless=_read_headless_setting())


@asynccontextmanager
async def lifespan(application: FastAPI):
    repository.initialize()
    yield
    await run_manager.shutdown()


app = FastAPI(
    title="Tabvio Agent API",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")


@app.get("/", include_in_schema=False)
async def get_viewer() -> FileResponse:
    return FileResponse(STATIC_DIRECTORY / "index.html")


@app.get("/api/health")
async def get_health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/runs",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(request: CreateRunRequest) -> RunResponse:
    try:
        run = await run_manager.create_run(
            task=request.task,
            max_runtime_seconds=request.max_runtime_seconds,
        )
    except RunAlreadyActiveError as exception:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exception),
        ) from exception

    return _build_run_response(run)


@app.get("/api/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: UUID) -> RunResponse:
    try:
        run = run_manager.get_run(run_id)
    except RunNotFoundError as exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exception),
        ) from exception

    return _build_run_response(run)


@app.get("/api/runs/{run_id}/stream")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    try:
        run_manager.get_run(run_id)
    except RunNotFoundError as exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exception),
        ) from exception

    after_sequence = _parse_last_event_id(last_event_id)

    async def event_stream() -> AsyncIterator[str]:
        async for event in run_manager.stream_events(run_id, after_sequence):
            if await request.is_disconnected():
                return

            yield _format_sse_event(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/runs/{run_id}/screen.mjpeg")
async def stream_run_screen(run_id: UUID) -> StreamingResponse:
    try:
        run_manager.get_run(run_id)
    except RunNotFoundError as exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exception),
        ) from exception

    async def frame_stream() -> AsyncIterator[bytes]:
        async for frame in run_manager.stream_frames(run_id):
            frame_header = (
                f"--{MJPEG_BOUNDARY}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n"
            ).encode("ascii")
            yield frame_header + frame + b"\r\n"

    return StreamingResponse(
        frame_stream(),
        media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/runs/{run_id}/input", response_model=RunResponse)
async def submit_run_input(
    run_id: UUID,
    request: UserInputRequest,
) -> RunResponse:
    try:
        run = await run_manager.submit_input(run_id, request.answer)
    except RunNotFoundError as exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exception),
        ) from exception
    except RunNotWaitingForInputError as exception:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exception),
        ) from exception

    return _build_run_response(run)


@app.post("/api/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(run_id: UUID) -> RunResponse:
    try:
        run = await run_manager.cancel_run(run_id)
    except RunNotFoundError as exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exception),
        ) from exception

    return _build_run_response(run)


def _build_run_response(run) -> RunResponse:
    return RunResponse(
        run=run,
        stream_url=f"/api/runs/{run.id}/stream",
        screen_url=f"/api/runs/{run.id}/screen.mjpeg",
    )


def _parse_last_event_id(last_event_id: str | None) -> int:
    if last_event_id is None:
        return 0

    try:
        return max(int(last_event_id), 0)
    except ValueError:
        return 0


def _format_sse_event(event: RunEvent) -> str:
    event_data = event.model_dump(mode="json")
    serialized_data = json.dumps(event_data, ensure_ascii=False)
    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type}\n"
        f"data: {serialized_data}\n\n"
    )
