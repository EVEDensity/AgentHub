from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.services.a2a_peer_credentials import OriginBoundA2ABearerProvider
from services.python.runner_service.a2a_peers import load_a2a_runner_peers

_PEER_ORIGIN = "https://peer.example.test"
_SECOND_ORIGIN = "https://second.example.test:8443"
_PUBLIC_KEY = "11" * 32
_ROTATED_PUBLIC_KEY = "22" * 32


def write_manifest(
    directory: Path,
    peers: list[dict[str, Any]],
    *,
    schema_version: str = "agenthub.runner.a2a-peers.v1",
) -> Path:
    path = directory / "a2a-peers.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": schema_version,
                "peers": peers,
            }
        ),
        encoding="utf-8",
    )
    return path


def peer_definition(**updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "agentOrigin": _PEER_ORIGIN,
        "trustedPublicKeys": [_PUBLIC_KEY, _ROTATED_PUBLIC_KEY],
    }
    values.update(updates)
    return values


class OriginBoundA2ABearerProviderTests(unittest.TestCase):
    def test_returns_only_exact_origin_credentials(self) -> None:
        provider = OriginBoundA2ABearerProvider(
            {_PEER_ORIGIN.upper(): "receiver-issued-token"}
        )

        self.assertEqual(provider.count, 1)
        self.assertEqual(
            provider.bearer_for(_PEER_ORIGIN),
            "receiver-issued-token",
        )
        self.assertIsNone(provider.bearer_for("https://unknown.example.test"))
        with self.assertRaises(ValueError):
            provider.bearer_for(f"{_PEER_ORIGIN}/not-an-origin")
        self.assertNotIn("receiver-issued-token", repr(provider))

    def test_rejects_duplicate_canonical_origins_and_invalid_tokens(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate Agent origin"):
            OriginBoundA2ABearerProvider(
                {
                    _PEER_ORIGIN: "first-token",
                    "https://PEER.EXAMPLE.TEST": "second-token",
                }
            )
        for token in ("", "contains a space", "non-ascii-token-\u5bc6"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                OriginBoundA2ABearerProvider({_PEER_ORIGIN: token})


class A2ARunnerPeerLoaderTests(unittest.TestCase):
    def test_loads_strict_rotating_pins_and_separate_receiver_token(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            token_file = directory / "peer.token"
            token_file.write_text("receiver-issued-token\n", encoding="utf-8")
            manifest_file = write_manifest(
                directory,
                [
                    peer_definition(bearerTokenFile=str(token_file)),
                    peer_definition(
                        agentOrigin=_SECOND_ORIGIN,
                        trustedPublicKeys=["33" * 32],
                    ),
                ],
            )

            loaded = load_a2a_runner_peers(manifest_file)

        self.assertTrue(loaded.trust_policy.require_pinned_keys)
        self.assertFalse(loaded.trust_policy.allow_unsigned_cards)
        self.assertEqual(
            loaded.trust_policy.pins_for(_PEER_ORIGIN),
            frozenset({_PUBLIC_KEY, _ROTATED_PUBLIC_KEY}),
        )
        self.assertEqual(loaded.credential_provider.count, 1)
        self.assertEqual(
            loaded.credential_provider.bearer_for(_PEER_ORIGIN),
            "receiver-issued-token",
        )
        self.assertIsNone(loaded.credential_provider.bearer_for(_SECOND_ORIGIN))
        self.assertNotIn("receiver-issued-token", repr(loaded))

    def test_manifest_rejects_plaintext_credentials_unknown_fields_and_versions(
        self,
    ) -> None:
        cases = (
            (
                "plaintext",
                [peer_definition(bearerToken="provider-secret-token")],
                "agenthub.runner.a2a-peers.v1",
                "provider-secret-token",
            ),
            (
                "unknown",
                [peer_definition(providerConfig={"token": "provider-secret"})],
                "agenthub.runner.a2a-peers.v1",
                "provider-secret",
            ),
            (
                "version",
                [peer_definition()],
                "agenthub.runner.a2a-peers.v2",
                "v2",
            ),
        )
        for name, peers, version, secret in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                manifest = write_manifest(
                    Path(raw),
                    peers,
                    schema_version=version,
                )
                with self.assertRaisesRegex(
                    ValueError, "manifest is invalid"
                ) as raised:
                    load_a2a_runner_peers(manifest)
                self.assertNotIn(secret, str(raised.exception))
                self.assertIsNone(raised.exception.__cause__)

    def test_manifest_rejects_duplicate_origins_keys_and_shared_token_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            token_file = directory / "shared.token"
            token_file.write_text("receiver-token", encoding="utf-8")
            cases = (
                (
                    "origins",
                    [
                        peer_definition(),
                        peer_definition(agentOrigin="https://PEER.EXAMPLE.TEST"),
                    ],
                ),
                (
                    "keys",
                    [peer_definition(trustedPublicKeys=[_PUBLIC_KEY, _PUBLIC_KEY])],
                ),
                (
                    "token-files",
                    [
                        peer_definition(bearerTokenFile=str(token_file)),
                        peer_definition(
                            agentOrigin=_SECOND_ORIGIN,
                            trustedPublicKeys=["33" * 32],
                            bearerTokenFile=str(token_file),
                        ),
                    ],
                ),
            )
            for name, peers in cases:
                with self.subTest(name=name):
                    manifest = write_manifest(directory, peers)
                    with self.assertRaisesRegex(ValueError, "manifest is invalid"):
                        load_a2a_runner_peers(manifest)

    def test_manifest_requires_absolute_token_file_and_valid_public_key(self) -> None:
        cases = (
            peer_definition(bearerTokenFile="relative/token"),
            peer_definition(trustedPublicKeys=["provider-secret-key"]),
            peer_definition(trustedPublicKeys=[]),
        )
        for peer in cases:
            with self.subTest(peer=peer), tempfile.TemporaryDirectory() as raw:
                manifest = write_manifest(Path(raw), [peer])
                with self.assertRaisesRegex(
                    ValueError, "manifest is invalid"
                ) as raised:
                    load_a2a_runner_peers(manifest)
                self.assertNotIn("provider-secret-key", str(raised.exception))

    def test_rejects_duplicate_json_fields_before_loading_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            manifest = directory / "a2a-peers.json"
            manifest.write_text(
                '{"schemaVersion":"agenthub.runner.a2a-peers.v1",'
                '"schemaVersion":"agenthub.runner.a2a-peers.v1","peers":[]}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "duplicate JSON fields"):
                load_a2a_runner_peers(manifest)

    def test_invalid_token_files_fail_without_exposing_path_or_content(self) -> None:
        values = (
            "first\nsecond",
            "contains a space",
            "non-ascii-token-\u5bc6",
            "x" * (16 * 1_024 + 1),
        )
        for index, value in enumerate(values):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                token_file = directory / f"secret-{index}.token"
                token_file.write_text(value, encoding="utf-8")
                manifest = write_manifest(
                    directory,
                    [peer_definition(bearerTokenFile=str(token_file))],
                )

                with self.assertRaises(ValueError) as raised:
                    load_a2a_runner_peers(manifest)
                message = str(raised.exception)
                self.assertNotIn(str(token_file), message)
                self.assertNotIn(value[:64], message)


if __name__ == "__main__":
    unittest.main()
