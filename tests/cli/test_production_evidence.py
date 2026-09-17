from __future__ import annotations

import json

import pytest

from scripts.production_evidence import new_evidence, write_evidence
from scripts import prepare_release_evidence


def test_evidence_contains_run_commit_and_safe_environment_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTHUB_PRODUCTION_EVIDENCE_DIR", raising=False)
    record = new_evidence(
        scope="provider-protocol",
        evidence_level="real-provider",
        status="SKIP",
        errorType="missing_credentials",
    )

    path = write_evidence(record, scope="provider", mirror_path=tmp_path / "mirror.json")
    assert path is None
    payload = json.loads((tmp_path / "mirror.json").read_text(encoding="utf-8"))
    assert payload["runId"].startswith("run-")
    assert payload["commit"]
    assert payload["environment"]["python"]
    assert "api_key" not in json.dumps(payload).lower()


def test_evidence_mirror_preserves_canonical_record(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTHUB_PRODUCTION_EVIDENCE_DIR", raising=False)
    mirror = tmp_path / "mirror.json"
    record = new_evidence(scope="tty", evidence_level="real-tty", status="SKIP")

    assert write_evidence(record, scope="tty", mirror_path=mirror) is None
    assert json.loads(mirror.read_text(encoding="utf-8"))["runId"] == record["runId"]


def test_evidence_rejects_noncanonical_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTHUB_PRODUCTION_EVIDENCE_DIR", str(tmp_path / "outside"))
    record = new_evidence(scope="provider", evidence_level="real-provider", status="SKIP")
    with pytest.raises(ValueError, match="inside artifacts/production"):
        write_evidence(record, scope="provider")


def test_prepare_release_evidence_only_removes_release_subdirectory(monkeypatch, tmp_path):
    monkeypatch.setattr(prepare_release_evidence, "EVIDENCE_ROOT", tmp_path)
    target = tmp_path / "release-v1.0.0"
    target.mkdir(parents=True)
    (target / "old.json").write_text("{}", encoding="utf-8")
    (tmp_path / "other.json").write_text("keep", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["prepare_release_evidence", "release-v1.0.0"])
    assert prepare_release_evidence.main() == 0
    assert target.is_dir() and not (target / "old.json").exists()
    assert (tmp_path / "other.json").exists()


def test_evidence_rejects_sensitive_fields():
    with pytest.raises(ValueError, match="forbidden evidence field"):
        new_evidence(
            scope="provider-protocol",
            evidence_level="real-provider",
            prompt="do not persist",
        )


def test_evidence_rejects_authorization_material():
    with pytest.raises(ValueError, match="authorization"):
        new_evidence(
            scope="provider-protocol",
            evidence_level="real-provider",
            detail="Bearer secret-value",
        )
