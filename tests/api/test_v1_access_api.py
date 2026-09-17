from __future__ import annotations

import unittest
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import router as v1_router
from app.services.auth_service import get_current_user


def build_app(user: dict[str, Any] | None) -> FastAPI:
    app = FastAPI()
    app.include_router(v1_router)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return app


class V1AccessWhoamiTests(unittest.TestCase):
    def test_whoami_reports_developer_capabilities(self) -> None:
        client = TestClient(
            build_app({"id": "user-1", "name": "Ada", "role": "developer"})
        )

        response = client.get("/api/v1/access/whoami")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "userId": "user-1",
                "name": "Ada",
                "role": "developer",
                "workspaceId": "user-1",
                "isAdmin": False,
                "canVerify": False,
            },
        )

    def test_whoami_reports_verifier_capability(self) -> None:
        client = TestClient(
            build_app({"id": "verifier-1", "name": "Vera", "role": "verifier"})
        )

        response = client.get("/api/v1/access/whoami")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["isAdmin"])
        self.assertTrue(body["canVerify"])

    def test_whoami_reports_admin_capabilities(self) -> None:
        client = TestClient(
            build_app({"id": "admin-1", "name": "Al", "role": "admin"})
        )

        response = client.get("/api/v1/access/whoami")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["isAdmin"])
        self.assertTrue(body["canVerify"])

    def test_whoami_requires_authentication(self) -> None:
        client = TestClient(build_app(None))

        response = client.get("/api/v1/access/whoami")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
