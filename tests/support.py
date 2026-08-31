"""Helpers for driving the real application in tests.

The endpoint tests go through ``TestClient`` rather than calling route
functions directly, so the middleware, the dependency graph, and the exception
handlers are all part of what is under test. Session verification is the one
thing stubbed out, because it is the only part that needs WorkOS.
"""

from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from tabvio.auth import sessions
from tabvio.auth.constants import SESSION_COOKIE_NAME
from tabvio.auth.models import User
from tabvio.runs.models import RunRecord, RunStatus

SEALED_SESSION = "sealed-test-session"


def build_user(email: str = "owner@example.com") -> User:
    return User(workos_user_id=f"user_{uuid4().hex}", email=email)


def build_run(
    user_id,
    task: str = "Open example.com",
    status: RunStatus = RunStatus.SUCCEEDED,
) -> RunRecord:
    return RunRecord(
        task=task,
        max_runtime_seconds=300,
        user_id=user_id,
        status=status,
    )


def build_session_state(user: User, refreshed: bool = False) -> sessions.SessionState:
    return sessions.SessionState(
        workos_user_id=user.workos_user_id,
        email=user.email,
        sealed_session=SEALED_SESSION,
        refreshed=refreshed,
    )


@contextmanager
def signed_in_client(user: User | None = None):
    """A TestClient carrying a session cookie that resolves to ``user``."""
    user = user or build_user()
    sessions.reset_session_cache()

    from tabvio.server.routes import app

    # Deliberately not entered as a context manager: the lifespan verifies
    # WorkOS credentials, and these tests are meant to run on a bare checkout.
    with patch.object(
        sessions,
        "_load_session_state",
        return_value=(sessions.SessionVerdict.VALID, build_session_state(user)),
    ):
        with patch.object(sessions.user_repository, "get_or_create_user", return_value=user):
            client = TestClient(app)
            client.cookies.set(SESSION_COOKIE_NAME, SEALED_SESSION)
            yield client, user

    sessions.reset_session_cache()


@contextmanager
def anonymous_client():
    from tabvio.server.routes import app

    sessions.reset_session_cache()
    yield TestClient(app)
    sessions.reset_session_cache()
