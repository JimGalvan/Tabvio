import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from tabvio.auth import routes as auth_routes
from tabvio.auth.sessions import (
    AuthKitSessionMiddleware,
    CurrentUser,
    read_session_state,
    user_repository,
    verify_auth_configuration,
)
from tabvio.config import (
    DATABASE_PATH,
    STATIC_DIRECTORY,
    read_credential_encryption_key,
    read_follow_up_window_seconds,
    read_headless_setting,
    read_max_concurrent_runs,
)
from tabvio.credentials.cipher import (
    LocalAesGcmCredentialCipher,
    UnavailableCredentialCipher,
)
from tabvio.credentials.exceptions import (
    CredentialConfigurationError,
    CredentialConflictError,
    CredentialInvalidError,
    CredentialNotFoundError,
)
from tabvio.credentials.repository import CredentialRepository
from tabvio.credentials.service import CredentialService
from tabvio.runs import constants
from tabvio.runs.exceptions import (
    RunCapacityReachedError,
    RunNotFoundError,
    RunNotReadyForFollowUpError,
    RunNotWaitingForInputError,
    SensitiveInputNotPendingError,
)
from tabvio.runs.models import RunEvent, RunRecord
from tabvio.runs.repository import RunRepository
from tabvio.runs.service import RunManager
from tabvio.server.schemas import (
    CreateCredentialRequest,
    CreateRunRequest,
    CredentialListResponse,
    CredentialMetadata,
    FollowUpRequest,
    RunListResponse,
    RunResponse,
    RunSummary,
    SensitiveInputRequest,
    UpdateCredentialRequest,
    UserInputRequest,
)

MJPEG_BOUNDARY = "tabvio-frame"
SCREEN_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "X-Accel-Buffering": "no",
}


repository = RunRepository(DATABASE_PATH)
credential_repository = CredentialRepository(DATABASE_PATH)
credential_encryption_key = read_credential_encryption_key()
credential_cipher = (
    LocalAesGcmCredentialCipher(credential_encryption_key)
    if credential_encryption_key
    else UnavailableCredentialCipher()
)
credential_service = CredentialService(credential_repository, credential_cipher)
run_manager = RunManager(
    repository,
    credential_service=credential_service,
    headless=read_headless_setting(),
    max_concurrent_runs=read_max_concurrent_runs(constants.DEFAULT_MAX_CONCURRENT_RUNS),
    follow_up_window_seconds=read_follow_up_window_seconds(constants.DEFAULT_FOLLOW_UP_WINDOW_SECONDS),
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    verify_auth_configuration()
    repository.initialize()
    user_repository.initialize()
    credential_repository.initialize()
    yield
    await run_manager.shutdown()


app = FastAPI(
    title="Tabvio Agent API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(AuthKitSessionMiddleware)
app.include_router(auth_routes.router)


# The run layer already distinguishes these; mapping them once here keeps every
# route free of the same four try/except blocks. A run belonging to somebody
# else raises RunNotFoundError too, so it reports as missing rather than
# forbidden and an identifier cannot be used to confirm a run exists.
_RUN_ERROR_STATUSES = {
    RunNotFoundError: status.HTTP_404_NOT_FOUND,
    RunCapacityReachedError: status.HTTP_429_TOO_MANY_REQUESTS,
    RunNotWaitingForInputError: status.HTTP_409_CONFLICT,
    RunNotReadyForFollowUpError: status.HTTP_409_CONFLICT,
    SensitiveInputNotPendingError: status.HTTP_409_CONFLICT,
    CredentialNotFoundError: status.HTTP_404_NOT_FOUND,
    CredentialConflictError: status.HTTP_409_CONFLICT,
    CredentialInvalidError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CredentialConfigurationError: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _register_run_error_handlers(application: FastAPI) -> None:
    for error_type, status_code in _RUN_ERROR_STATUSES.items():

        async def handle(request: Request, exception: Exception, code: int = status_code) -> JSONResponse:
            return JSONResponse(status_code=code, content={"detail": str(exception)})

        application.add_exception_handler(error_type, handle)


_register_run_error_handlers(app)


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    request: Request, exception: RequestValidationError
) -> JSONResponse:
    """Return validation details without reflecting submitted values."""
    errors = []
    for error in exception.errors():
        errors.append(
            {
                key: value
                for key, value in error.items()
                if key not in {"input", "url"}
            }
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": errors},
    )


class RevalidatedStaticFiles(StaticFiles):
    """Serve static files that browsers must revalidate before reusing."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["cache-control"] = "no-cache"
        return response


app.mount("/static", RevalidatedStaticFiles(directory=STATIC_DIRECTORY), name="static")


def _serve_page(name: str) -> FileResponse:
    return FileResponse(
        STATIC_DIRECTORY / name,
        headers={"cache-control": "no-cache"},
    )


@app.get("/", include_in_schema=False)
async def get_landing_page() -> FileResponse:
    """Public: nothing here reads a run, so it needs no session."""
    return _serve_page("index.html")


@app.get("/privacy", include_in_schema=False)
async def get_privacy_page() -> FileResponse:
    return _serve_page("privacy.html")


@app.get("/terms", include_in_schema=False)
async def get_terms_page() -> FileResponse:
    return _serve_page("terms.html")


@app.get("/app", include_in_schema=False)
async def get_dashboard(request: Request) -> Response:
    if read_session_state(request) is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    return _serve_page("app.html")


@app.get("/api/health")
async def get_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/runs", response_model=RunListResponse)
async def list_runs(user: CurrentUser) -> RunListResponse:
    runs = await run_in_threadpool(run_manager.list_runs, user.id)
    return RunListResponse(
        runs=[RunSummary.model_validate(run.model_dump()) for run in runs]
    )


@app.get("/api/credentials", response_model=CredentialListResponse)
async def list_credentials(user: CurrentUser) -> CredentialListResponse:
    credentials = await run_in_threadpool(credential_service.list, user.id)
    return CredentialListResponse(credentials=credentials)


@app.post(
    "/api/credentials",
    response_model=CredentialMetadata,
    status_code=status.HTTP_201_CREATED,
)
async def create_credential(
    request: CreateCredentialRequest, user: CurrentUser
) -> CredentialMetadata:
    return await run_in_threadpool(credential_service.create, user.id, request)


@app.patch("/api/credentials/{credential_id}", response_model=CredentialMetadata)
async def update_credential(
    credential_id: UUID,
    request: UpdateCredentialRequest,
    user: CurrentUser,
) -> CredentialMetadata:
    return await run_in_threadpool(
        credential_service.update, credential_id, user.id, request
    )


@app.delete(
    "/api/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_credential(credential_id: UUID, user: CurrentUser) -> Response:
    await run_in_threadpool(credential_service.revoke, credential_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/api/runs",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(request: CreateRunRequest, user: CurrentUser) -> RunResponse:
    run = await run_manager.create_run(
        task=request.task,
        max_runtime_seconds=request.max_runtime_seconds,
        user_id=user.id,
        credential_ids=request.credential_ids,
    )
    return _build_run_response(run)


@app.get("/api/runs/{run_id}", response_model=RunResponse)
async def get_run(run_id: UUID, user: CurrentUser) -> RunResponse:
    run = await run_in_threadpool(run_manager.get_run, run_id, user.id)
    return _build_run_response(run)


@app.get("/api/runs/{run_id}/stream")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    user: CurrentUser,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    events = await run_in_threadpool(
        run_manager.stream_events,
        run_id,
        user.id,
        _parse_last_event_id(last_event_id),
    )

    async def event_stream() -> AsyncIterator[str]:
        async for event in events:
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


@app.get("/api/runs/{run_id}/screen.jpg")
async def get_run_screen(run_id: UUID, user: CurrentUser) -> Response:
    frame = await run_in_threadpool(run_manager.get_latest_frame, run_id, user.id)
    if frame is None:
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers=SCREEN_CACHE_HEADERS,
        )

    return Response(
        content=frame,
        media_type="image/jpeg",
        headers=SCREEN_CACHE_HEADERS,
    )


@app.get("/api/runs/{run_id}/screen.mjpeg")
async def stream_run_screen(run_id: UUID, user: CurrentUser) -> StreamingResponse:
    frames = await run_in_threadpool(run_manager.stream_frames, run_id, user.id)

    async def frame_stream() -> AsyncIterator[bytes]:
        async for frame in frames:
            frame_header = (
                f"--{MJPEG_BOUNDARY}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n"
            ).encode("ascii")
            yield frame_header + frame + b"\r\n"

    return StreamingResponse(
        frame_stream(),
        media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
        headers=SCREEN_CACHE_HEADERS,
    )


@app.post("/api/runs/{run_id}/input", response_model=RunResponse)
async def submit_run_input(
    run_id: UUID,
    request: UserInputRequest,
    user: CurrentUser,
) -> RunResponse:
    run = await run_manager.submit_input(run_id, user.id, request.answer)
    return _build_run_response(run)


@app.post("/api/runs/{run_id}/sensitive-input", response_model=RunResponse)
async def submit_sensitive_input(
    run_id: UUID,
    request: SensitiveInputRequest,
    user: CurrentUser,
) -> RunResponse:
    run = await run_manager.submit_sensitive_input(
        run_id,
        user.id,
        request.request_id,
        request.code.get_secret_value(),
    )
    return _build_run_response(run)


@app.post("/api/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(run_id: UUID, user: CurrentUser) -> RunResponse:
    run = await run_manager.cancel_run(run_id, user.id)
    return _build_run_response(run)


@app.post("/api/runs/{run_id}/follow-ups", response_model=RunResponse)
async def submit_follow_up(
    run_id: UUID,
    request: FollowUpRequest,
    user: CurrentUser,
) -> RunResponse:
    run = await run_manager.submit_follow_up(run_id, user.id, request.task)
    return _build_run_response(run)


@app.post("/api/runs/{run_id}/end", response_model=RunResponse)
async def end_run_session(run_id: UUID, user: CurrentUser) -> RunResponse:
    run = await run_manager.end_session(run_id, user.id)
    return _build_run_response(run)


def _build_run_response(run: RunRecord) -> RunResponse:
    return RunResponse(
        run=run,
        stream_url=f"/api/runs/{run.id}/stream",
        screen_url=f"/api/runs/{run.id}/screen.jpg",
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
        f"id: {event.sequence}\nevent: {event.event_type}\ndata: {serialized_data}\n\n"
    )
