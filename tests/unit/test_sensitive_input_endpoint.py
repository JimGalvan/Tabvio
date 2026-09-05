import unittest
from uuid import uuid4

from tests.support import signed_in_client


class SensitiveInputEndpointTests(unittest.TestCase):
    def test_validation_error_does_not_reflect_submitted_code(self) -> None:
        secret_code = "SECRET-CODE-" * 20
        with signed_in_client() as (client, _):
            response = client.post(
                f"/api/runs/{uuid4()}/sensitive-input",
                json={"request_id": str(uuid4()), "code": secret_code},
            )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret_code, response.text)
        self.assertNotIn("input", response.text)


if __name__ == "__main__":
    unittest.main()
