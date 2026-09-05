import unittest
from unittest.mock import patch
from uuid import uuid4

from tabvio.server import routes as app_module
from tests.support import anonymous_client, build_run, signed_in_client

# Every run endpoint, as a table, so a new one cannot quietly arrive without
# an ownership test.
RUN_ENDPOINTS = [
    ("GET", ""),
    ("GET", "/stream"),
    ("GET", "/screen.jpg"),
    ("GET", "/screen.mjpeg"),
    ("POST", "/cancel"),
    ("POST", "/end"),
    ("POST", "/input"),
    ("POST", "/sensitive-input"),
    ("POST", "/follow-ups"),
    ("POST", "/rerun"),
]

REQUEST_BODIES = {
    "/input": {"answer": "Yes"},
    "/sensitive-input": {"request_id": str(uuid4()), "code": "123456"},
    "/follow-ups": {"task": "Add an item"},
}


def call(client, method: str, run_id, suffix: str):
    url = f"/api/runs/{run_id}{suffix}"
    if method == "POST":
        return client.post(url, json=REQUEST_BODIES.get(suffix, {}))
    return client.get(url)


class AnonymousAccessTests(unittest.TestCase):
    def test_every_run_endpoint_requires_a_session(self) -> None:
        run_id = uuid4()
        with anonymous_client() as client:
            for method, suffix in RUN_ENDPOINTS:
                with self.subTest(endpoint=suffix or "/"):
                    self.assertEqual(call(client, method, run_id, suffix).status_code, 401)

    def test_listing_and_creating_runs_require_a_session(self) -> None:
        with anonymous_client() as client:
            self.assertEqual(client.get("/api/runs").status_code, 401)
            self.assertEqual(
                client.post("/api/runs", json={"task": "Open example.com"}).status_code,
                401,
            )

    def test_the_dashboard_redirects_to_sign_in(self) -> None:
        with anonymous_client() as client:
            response = client.get("/app", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_the_landing_page_stays_public(self) -> None:
        with anonymous_client() as client:
            self.assertEqual(client.get("/").status_code, 200)


class RunOwnershipTests(unittest.TestCase):
    def test_another_accounts_run_is_reported_missing_everywhere(self) -> None:
        """The manager is left real, so this exercises the ownership check itself."""
        with signed_in_client() as (client, _):
            run = build_run(uuid4())
            with patch.object(app_module.repository, "get_run", return_value=run):
                for method, suffix in RUN_ENDPOINTS:
                    with self.subTest(endpoint=suffix or "/"):
                        self.assertEqual(
                            call(client, method, run.id, suffix).status_code, 404
                        )

    def test_a_run_without_an_owner_is_reported_missing(self) -> None:
        """Runs recorded before accounts existed belong to nobody."""
        with signed_in_client() as (client, _):
            run = build_run(None)
            with patch.object(app_module.repository, "get_run", return_value=run):
                self.assertEqual(client.get(f"/api/runs/{run.id}").status_code, 404)

    def test_a_missing_run_is_reported_missing(self) -> None:
        with signed_in_client() as (client, _):
            with patch.object(app_module.repository, "get_run", return_value=None):
                self.assertEqual(client.get(f"/api/runs/{uuid4()}").status_code, 404)

    def test_the_owner_can_load_their_run(self) -> None:
        with signed_in_client() as (client, user):
            run = build_run(user.id)
            with patch.object(app_module.repository, "get_run", return_value=run):
                response = client.get(f"/api/runs/{run.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["id"], str(run.id))


if __name__ == "__main__":
    unittest.main()
