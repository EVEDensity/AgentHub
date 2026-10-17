"""Actual pytest process log through fenced registration, CAS and HTTP reads."""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from tests.integration.artifact_log_http_fixture import (
    BOUNDED_TAIL, MISSION_ID, WORK_UNIT_ID, WORKSPACE_ID, build_fixture_app,
)

HUMAN = {"Authorization": "Bearer artifact-log-human"}
FOREIGN = {"Authorization": "Bearer artifact-log-foreign"}
BASE = f"/api/v1/missions/{MISSION_ID}"


@pytest.fixture
def log_http():
    with TestClient(build_fixture_app()) as http:
        yield http


def preview(http, name, *, headers=HUMAN, mission_id=MISSION_ID):
    return http.get(f"/api/v1/missions/{mission_id}/artifacts/artifact-log-{name}/preview", headers=headers)


def state(http):
    paths = ("", "/work-units", "/artifacts", "/evidence", "/events")
    documents = []
    for path in paths:
        response = http.get(BASE + path, headers=HUMAN)
        assert response.status_code == 200, response.text
        documents.append(response.json())
    return documents


def assert_registered_integrity(response, metadata):
    body = response.json()
    assert (body["schemaVersion"], body["integrity"], body["encoding"]) == (1, "verified", "utf-8")
    artifact = body["artifact"]
    assert (artifact["kind"], artifact["mediaType"], artifact["missionId"], artifact["workUnitId"], artifact["attempt"]) == (
        "log", "text/plain", MISSION_ID, WORK_UNIT_ID, 1,
    )
    content = body["text"].encode("utf-8")
    assert len(content) == artifact["sizeBytes"]
    assert "sha256:" + hashlib.sha256(content).hexdigest() == artifact["digest"] == metadata["digest"]
    assert "contentAddress" not in response.text
    assert "verdict" not in body
    assert (response.headers["cache-control"], response.headers["x-content-type-options"]) == ("no-store", "nosniff")


def assert_actual_process_output(body):
    diagnostic = json.loads(body["text"].splitlines()[0])
    assert (diagnostic["treeChanged"], diagnostic["timedOut"], diagnostic["limitExceeded"]) == (False, False, False)
    assert "1 failed" in body["text"]
    assert "Intentional fixture failure" in body["text"]
    for marker in ("真实 stdout 中文", "真实 stderr 中文", "\x1b[32m", "<script>", "<img ", "\x1b]52;", "\x1b]8;", "\x1b]0;", "progress 10%\rprogress 100%"):
        assert marker in body["text"]


def assert_no_acceptance(http):
    unit = http.get(BASE + "/work-units", headers=HUMAN).json()["workUnits"][0]
    assert (unit["status"], unit["attempt"]) == ("VERIFYING", 1)
    assert "lease" not in unit
    assert http.get(BASE, headers=HUMAN).json()["status"] == "RUNNING"
    assert http.get(BASE + "/evidence", headers=HUMAN).json() == {"evidence": []}
    events = http.get(BASE + "/events", headers=HUMAN).json()["events"]
    assert any(event["event_type"] == "mission.lifecycle.started" for event in events)


def test_real_pytest_combined_log_reaches_verified_http_without_accepting_work(log_http):
    health = log_http.get("/fixture/health").json()
    assert health["ready"] and health["fixturePid"] > 0
    assert (health["workspaceId"], health["workUnitId"]) == (WORKSPACE_ID, WORK_UNIT_ID)
    assert health["artifacts"]["real"]["source"] == "production-trusted-test-producer"
    assert health["reads"] == {}
    response = preview(log_http, "real")
    assert response.status_code == 200, response.text
    assert_registered_integrity(response, health["artifacts"]["real"])
    assert_actual_process_output(response.json())
    assert_no_acceptance(log_http)


def test_boundary_cases_are_explicit_and_use_real_cas_failures(log_http):
    health = log_http.get("/fixture/health").json()
    for name in ("empty", "large", "denied", "tampered", "missing", "invalid", "bounded"):
        assert health["artifacts"][name]["source"] == "artificial-boundary-fixture"
    empty = preview(log_http, "empty")
    assert empty.status_code == 200 and empty.json()["text"] == "" and empty.json()["artifact"]["sizeBytes"] == 0
    for name, code in (("large", 413), ("denied", 403), ("tampered", 409), ("missing", 503), ("invalid", 415)):
        response = preview(log_http, name)
        assert response.status_code == code, response.text
        assert response.headers["cache-control"] == "no-store"
        assert "local:sha256" not in response.text
    reads = log_http.get("/fixture/health").json()["reads"]
    assert "artifact-log-large" not in reads and "artifact-log-denied" not in reads
    assert reads == {f"artifact-log-{name}": 1 for name in ("empty", "tampered", "missing", "invalid")}


def test_foreign_and_unknown_identities_do_not_read_log_bytes(log_http):
    assert preview(log_http, "real", headers=FOREIGN).status_code == 403
    assert preview(log_http, "real", headers={}).status_code == 401
    assert preview(log_http, "real", headers={"Authorization": "Bearer unknown"}).status_code == 401
    assert preview(log_http, "real", mission_id="missing-mission").status_code == 404
    assert log_http.get(BASE + "/artifacts/missing-artifact/preview", headers=HUMAN).status_code == 404
    assert log_http.get("/fixture/health").json()["reads"] == {}


def test_listing_and_preview_do_not_change_registered_state_or_create_evidence(log_http):
    before = state(log_http)
    assert log_http.get("/fixture/health").json()["reads"] == {}
    for name in ("real", "empty", "tampered", "missing"):
        preview(log_http, name)
    assert state(log_http) == before
    assert len(before[2]["artifacts"]) == 8
    assert before[3] == {"evidence": []}


def test_artificial_bounded_log_preserves_unprojected_lines_and_long_unicode_row(log_http):
    response = preview(log_http, "bounded")
    assert response.status_code == 200, response.text
    metadata = log_http.get("/fixture/health").json()["artifacts"]["bounded"]
    assert metadata["source"] == "artificial-boundary-fixture"
    assert_registered_integrity(response, metadata)
    text = response.json()["text"]
    lines = text.split("\n")
    assert len(lines) == 2002
    assert lines[0] == "fixture-line-0000" and lines[2] == "fixture-line-0002"
    assert lines[2000] == "界" * 2500 and lines[2001] == BOUNDED_TAIL
    assert not text.endswith("\n")
    assert len(text.encode("utf-8")) <= 262144
    assert_no_acceptance(log_http)


def test_shutdown_cleans_only_owned_database_process_inputs_and_cas():
    with TestClient(build_fixture_app()) as http:
        root = http.app.state.root
        assert (root / "missions.sqlite3").is_file() and (root / "cas" / "sha256").is_dir()
        assert preview(http, "real").status_code == 200
    assert not root.exists()
