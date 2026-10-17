"""Default development topology must not expose operator or experimental APIs."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SERVICES = yaml.safe_load(
    (ROOT / "deploy/docker-compose.platform.yml").read_text(encoding="utf-8")
)["services"]


def test_iam_management_is_opt_in_internal_and_requires_mounted_identity():
    iam = SERVICES["iam-service"]
    assert iam["profiles"] == ["iam-admin"]
    assert not iam.get("ports")
    assert iam["expose"] == ["8088"]
    assert iam["environment"]["IAM_SERVICE_TOKEN_FILE"] == "/run/secrets/iam-service-token"
    assert "iam-service-token" in iam["secrets"]
    assert iam["read_only"] is True
    assert iam["cap_drop"] == ["ALL"]


def test_all_development_published_ports_bind_only_loopback():
    for name, service in SERVICES.items():
        for port in service.get("ports", []):
            assert isinstance(port, str) and port.startswith("127.0.0.1:"), (
                f"{name} publishes a non-loopback development port"
            )


def test_unverified_knowledge_and_legacy_executors_are_explicit_experiments():
    quarantined = {
        "realtime-orchestrator", "agent-runtime-control-plane",
        "offline-knowledge-service", "document-pipeline-service",
        "model-adapter-service", "summarization-service", "evaluation-batch-service",
        "retrieval-core", "qdrant", "opensearch", "minio",
    }
    for name in quarantined:
        assert SERVICES[name]["profiles"] == ["legacy-experimental"]
    for name, service in SERVICES.items():
        if service.get("profiles"):
            continue
        dependencies = set(service.get("depends_on", []))
        assert not dependencies & quarantined, f"{name} activates an experimental dependency"


def test_default_api_surface_does_not_require_iam_or_experimental_profiles():
    for name in ("gateway-service", "frontend", "session-service"):
        assert not SERVICES[name].get("profiles")
        assert "iam-service" not in SERVICES[name].get("depends_on", [])
