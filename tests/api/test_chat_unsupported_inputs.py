"""Unsupported chat input must fail validation before durable admission."""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from tests.api.test_chat_mission import build_chat_app


class ChatUnsupportedInputsTests(unittest.TestCase):
    def test_attachments_references_and_permissions_are_not_silently_ignored(self):
        for field, value in (
            ("attachments", [{"name": "input.txt", "content": "do work"}]),
            ("references", [{"type": "file", "path": "src/main.py"}]),
            ("execPermission", "full-access"),
            ("exec_permission", "read-only"),
        ):
            with self.subTest(field=field):
                app, fakes = build_chat_app()
                response = TestClient(app).post(
                    "/api/v1/chat/mission", json={"message": "inspect", field: value},
                )
                self.assertEqual(response.status_code, 422, response.text)
                errors = response.json()["detail"]
                self.assertTrue(any(error["loc"][-1] == field for error in errors))
                self.assertFalse(fakes["repo"].missions)
                self.assertFalse(fakes["repo"].contracts)
                self.assertFalse(fakes["repo"].work_units)
                self.assertFalse(fakes["sessions"].created)
                self.assertFalse(fakes["session_events"].events)
                self.assertFalse(fakes["pending"].pendings)
