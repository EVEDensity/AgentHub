from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import router as v1_router
from app.services.auth_service import get_current_user

SKILL_MD = """---
name: Demo Skill
description: A demo skill used by the v1 skills API tests.
version: 1.0.0
---

# Demo Skill

Body used for classification.
"""


def build_app(user: dict[str, Any] | None) -> FastAPI:
    app = FastAPI()
    app.include_router(v1_router)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return app


class V1SkillsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        project_root = Path(self._tmp.name)
        skill_dir = project_root / ".claude" / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        self._previous_env = os.environ.get("AGENTHUB_PROJECT_DIR")
        os.environ["AGENTHUB_PROJECT_DIR"] = str(project_root)

    def tearDown(self) -> None:
        if self._previous_env is None:
            os.environ.pop("AGENTHUB_PROJECT_DIR", None)
        else:
            os.environ["AGENTHUB_PROJECT_DIR"] = self._previous_env
        self._tmp.cleanup()

    def test_skills_requires_authentication(self) -> None:
        client = TestClient(build_app(None))

        response = client.get("/api/v1/skills")

        self.assertEqual(response.status_code, 401)

    def test_skills_lists_project_skill_when_authenticated(self) -> None:
        client = TestClient(
            build_app({"id": "user-1", "name": "Ada", "role": "developer"})
        )

        response = client.get(
            "/api/v1/skills", params={"source": "project"}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        names = [skill["name"] for skill in body["skills"]]
        self.assertIn("demo-skill", names)
        demo = next(
            skill for skill in body["skills"] if skill["name"] == "demo-skill"
        )
        self.assertEqual(demo["display_name"], "Demo Skill")
        self.assertEqual(demo["source"], "project")

    def test_skill_detail_requires_authentication(self) -> None:
        client = TestClient(build_app(None))

        response = client.get("/api/v1/skills/demo-skill", params={"source": "project"})

        self.assertEqual(response.status_code, 401)

    def test_skill_detail_returns_body_and_metadata(self) -> None:
        client = TestClient(
            build_app({"id": "user-1", "name": "Ada", "role": "developer"})
        )

        response = client.get("/api/v1/skills/demo-skill", params={"source": "project"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "demo-skill")
        self.assertEqual(body["source"], "project")
        self.assertIn("Demo Skill", body["raw"])

    def test_skill_raw_requires_authentication(self) -> None:
        client = TestClient(build_app(None))

        response = client.get(
            "/api/v1/skills/demo-skill/raw", params={"source": "project"}
        )

        self.assertEqual(response.status_code, 401)

    def test_skill_raw_returns_markdown_content(self) -> None:
        client = TestClient(
            build_app({"id": "user-1", "name": "Ada", "role": "developer"})
        )

        response = client.get(
            "/api/v1/skills/demo-skill/raw", params={"source": "project"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Demo Skill", response.text)

    def test_unknown_skill_returns_404(self) -> None:
        client = TestClient(
            build_app({"id": "user-1", "name": "Ada", "role": "developer"})
        )

        response = client.get(
            "/api/v1/skills/missing-skill", params={"source": "project"}
        )

        self.assertEqual(response.status_code, 404)

    def test_legacy_mount_stays_available_without_authentication(self) -> None:
        """The legacy /api/skills compatibility alias must keep working (I-7a)."""
        from app.api import routes as api_routes

        paths = {getattr(route, "path", "") for route in api_routes.api_router.routes}
        self.assertIn("/api/skills", paths)
        self.assertIn("/api/skills/{skill_name}", paths)


if __name__ == "__main__":
    unittest.main()
