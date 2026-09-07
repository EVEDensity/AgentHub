from __future__ import annotations

import json

from scripts import generate_release_manifest
from scripts import generate_release_notes


def _record(scope: str, status: str = "PASS", **extra: object) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "runId": f"run-{scope}",
        "commit": "a" * 40,
        "environment": {"platform": "test", "python": "3.12", "ci": False},
        "evidenceLevel": "production",
        "scope": scope,
        "status": status,
        "observedAt": "2026-09-07T00:00:00+00:00",
        **extra,
    }


def test_manifest_remains_implemented_when_any_scope_is_missing(tmp_path):
    root = tmp_path / "evidence"
    (root / "provider").mkdir(parents=True)
    (root / "provider" / "provider.json").write_text(json.dumps(_record("provider")), encoding="utf-8")
    output = tmp_path / "release-manifest.json"
    report = tmp_path / "PRODUCTION_VERIFICATION.md"

    assert generate_release_manifest.main.__name__ == "main"
    import sys
    old = sys.argv
    sys.argv = ["generate_release_manifest", "--evidence-root", str(root), "--output", str(output), "--report", str(report)]
    try:
        assert generate_release_manifest.main() == 1
    finally:
        sys.argv = old
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["status"] == "implemented"
    assert "postgres" in manifest["missingOrNonPassingScopes"]
    assert "implemented" in report.read_text(encoding="utf-8")


def test_manifest_production_verified_requires_all_pass_scopes(tmp_path):
    root = tmp_path / "evidence"
    for scope in generate_release_manifest.REQUIRED_SCOPES - {"provider"}:
        directory = root / scope
        directory.mkdir(parents=True)
        extra = {"thresholdFailures": []} if scope == "benchmark" else {}
        (directory / f"{scope}.json").write_text(json.dumps(_record(scope, **extra)), encoding="utf-8")
    provider_directory = root / "provider"
    provider_directory.mkdir(parents=True)
    (provider_directory / "protocol.json").write_text(
        json.dumps(_record("provider-protocol")), encoding="utf-8"
    )
    (provider_directory / "mission.json").write_text(
        json.dumps(_record("mission-closed-loop")), encoding="utf-8"
    )
    output = tmp_path / "release-manifest.json"
    report = tmp_path / "PRODUCTION_VERIFICATION.md"
    import sys
    old = sys.argv
    sys.argv = [
        "generate_release_manifest",
        "--evidence-root", str(root),
        "--output", str(output),
        "--report", str(report),
        "--expected-commit", "a" * 40,
    ]
    try:
        assert generate_release_manifest.main() == 0
    finally:
        sys.argv = old
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "production-verified"


def test_manifest_rejects_foreign_commit_records(tmp_path):
    root = tmp_path / "evidence"
    for scope in generate_release_manifest.REQUIRED_SCOPES - {"provider"}:
        directory = root / scope
        directory.mkdir(parents=True)
        extra = {"thresholdFailures": []} if scope == "benchmark" else {}
        record = _record(scope, **extra)
        record["commit"] = "b" * 40
        (directory / f"{scope}.json").write_text(json.dumps(record), encoding="utf-8")
    provider_directory = root / "provider"
    provider_directory.mkdir(parents=True)
    for kind in ("provider-protocol", "mission-closed-loop"):
        record = _record(kind)
        record["commit"] = "b" * 40
        (provider_directory / f"{kind}.json").write_text(json.dumps(record), encoding="utf-8")
    output = tmp_path / "release-manifest.json"
    report = tmp_path / "PRODUCTION_VERIFICATION.md"
    import sys
    old = sys.argv
    sys.argv = [
        "generate_release_manifest", "--evidence-root", str(root),
        "--output", str(output), "--report", str(report),
        "--expected-commit", "a" * 40,
    ]
    try:
        assert generate_release_manifest.main() == 1
    finally:
        sys.argv = old
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["status"] == "implemented"
    assert manifest["foreignCommitRecords"] == ["b" * 40]


def test_manifest_requires_latest_pass_for_each_provider_evidence_kind(tmp_path):
    root = tmp_path / "evidence"
    for scope in generate_release_manifest.REQUIRED_SCOPES - {"provider"}:
        directory = root / scope
        directory.mkdir(parents=True)
        extra = {"thresholdFailures": []} if scope == "benchmark" else {}
        (directory / f"{scope}.json").write_text(json.dumps(_record(scope, **extra)), encoding="utf-8")
    provider_directory = root / "provider"
    provider_directory.mkdir(parents=True)
    (provider_directory / "protocol-pass.json").write_text(
        json.dumps(_record("provider-protocol", observedAt="2026-09-07T00:00:00+00:00")), encoding="utf-8"
    )
    (provider_directory / "protocol-fail.json").write_text(
        json.dumps(_record("provider-protocol", status="FAIL", observedAt="2026-09-07T00:01:00+00:00")), encoding="utf-8"
    )
    (provider_directory / "mission.json").write_text(json.dumps(_record("mission-closed-loop")), encoding="utf-8")
    output = tmp_path / "release-manifest.json"
    report = tmp_path / "PRODUCTION_VERIFICATION.md"
    import sys
    old = sys.argv
    sys.argv = [
        "generate_release_manifest", "--evidence-root", str(root),
        "--output", str(output), "--report", str(report),
        "--expected-commit", "a" * 40,
    ]
    try:
        assert generate_release_manifest.main() == 1
    finally:
        sys.argv = old
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "implemented"


def test_release_notes_require_production_verified_manifest(tmp_path):
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "RELEASE_NOTES.md"
    manifest.write_text(json.dumps({"status": "implemented"}), encoding="utf-8")
    import sys
    old = sys.argv
    sys.argv = [
        "generate_release_notes", "--manifest", str(manifest),
        "--output", str(output), "--tag", "v1.0.0",
    ]
    try:
        try:
            generate_release_notes.main()
        except SystemExit as exc:
            assert "not production-verified" in str(exc)
        else:
            raise AssertionError("implemented manifest must be rejected")
    finally:
        sys.argv = old


def test_release_notes_render_only_redacted_manifest_metadata(tmp_path):
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "RELEASE_NOTES.md"
    required = sorted(generate_release_manifest.REQUIRED_SCOPES)
    payload = {
        "status": "production-verified",
        "commit": "a" * 40,
        "generatedAt": "2026-09-07T00:00:00+00:00",
        "evidenceCount": 8,
        "requiredScopes": required,
        "scopeStatus": {scope: "PASS" for scope in required},
        "missingOrNonPassingScopes": [],
        "foreignCommitRecords": [],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    import sys
    old = sys.argv
    sys.argv = [
        "generate_release_notes", "--manifest", str(manifest),
        "--output", str(output), "--tag", "v1.0.0",
    ]
    try:
        assert generate_release_notes.main() == 0
    finally:
        sys.argv = old
    rendered = output.read_text(encoding="utf-8")
    assert "# AgentHub CLI v1.0.0" in rendered
    assert "production-verified" not in rendered
    assert "a" * 40 in rendered


def test_manifest_maps_provider_protocol_and_mission_scopes_to_provider(tmp_path):
    root = tmp_path / "evidence"
    (root / "provider").mkdir(parents=True)
    (root / "provider" / "stream.json").write_text(json.dumps(_record("provider-protocol")), encoding="utf-8")
    (root / "provider" / "mission.json").write_text(json.dumps(_record("mission-closed-loop")), encoding="utf-8")
    output = tmp_path / "release-manifest.json"
    report = tmp_path / "report.md"
    import sys
    old = sys.argv
    sys.argv = ["generate_release_manifest", "--evidence-root", str(root), "--output", str(output), "--report", str(report)]
    try:
        assert generate_release_manifest.main() == 1
    finally:
        sys.argv = old
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["scopeStatus"]["provider"] == "PASS"
