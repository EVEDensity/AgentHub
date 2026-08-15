from __future__ import annotations

import copy
import json
import unittest
from collections.abc import Callable
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.a2a_peer_route_service import (
    A2AAgentCardRouteResolver,
    A2AAgentCardTrustPolicy,
    A2APeerRouteResolutionError,
    _agent_card_signing_payload,
)

_ORIGIN = "https://receiver.example.test"
_SEED = bytes(range(32))


def agent_card() -> dict[str, Any]:
    return {
        "protocolVersion": "1.0",
        "name": "Receiver",
        "description": "A bounded test receiver.",
        "url": f"{_ORIGIN}/agent",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
            "codeExecution": True,
        },
        "skills": [
            {
                "id": "code_generation",
                "name": "Code generation",
                "tags": ["repository.write", "Review"],
                "inputSchema": {
                    "type": "object",
                    "minimum": 0.0000001,
                    "threshold": 0.000001,
                },
            }
        ],
        "endpoints": {"taskApi": f"{_ORIGIN}/platform/a2a/inbox"},
        "authSchemes": [{"type": "Bearer"}],
    }


def sign_card(
    card: dict[str, Any],
    *,
    seed: bytes = _SEED,
) -> tuple[dict[str, Any], str]:
    signed = copy.deepcopy(card)
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_key_hex = public_key.hex()
    signed["security"] = {
        "public_key": public_key_hex,
        "key_algorithm": "ed25519",
        "key_id": "receiver-card",
        "key_version": "2026-08-15",
    }
    signed["signature"] = private_key.sign(_agent_card_signing_payload(signed)).hex()
    return signed, public_key_hex


def json_response(card: dict[str, Any]) -> bytes:
    return json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode()


def resolver_for(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    policy: A2AAgentCardTrustPolicy | None = None,
    max_response_bytes: int = 1 << 20,
) -> tuple[A2AAgentCardRouteResolver, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        A2AAgentCardRouteResolver(
            trust_policy=policy,
            http_client=client,
            max_response_bytes=max_response_bytes,
        ),
        client,
    )


class A2AAgentCardRouteResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_valid_signed_pinned_route_and_bearer_policy(self) -> None:
        signed, public_key = sign_card(agent_card())
        other_key = "00" * 32
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=json_response(signed))

        policy = A2AAgentCardTrustPolicy(
            require_pinned_keys=True,
            trusted_public_keys={_ORIGIN: [other_key, public_key]},
        )
        resolver, client = resolver_for(handler, policy=policy)
        try:
            route = await resolver.resolve(
                f"{_ORIGIN}/configured/path?ignored=true",
                required_capabilities=("CODE_GENERATION", "repository.write"),
            )
        finally:
            await client.aclose()

        self.assertEqual(route.agent_origin, _ORIGIN)
        self.assertEqual(route.task_api_url, f"{_ORIGIN}/platform/a2a/inbox")
        self.assertTrue(route.requires_bearer)
        self.assertEqual(requests[0].url.path, "/.well-known/agent-card.json")
        self.assertNotIn("Authorization", requests[0].headers)

    async def test_rejects_missing_required_capability(self) -> None:
        signed, _ = sign_card(agent_card())
        resolver, client = resolver_for(
            lambda _: httpx.Response(200, content=json_response(signed))
        )
        try:
            with self.assertRaisesRegex(
                A2APeerRouteResolutionError,
                "does not provide every required capability",
            ):
                await resolver.resolve(
                    _ORIGIN,
                    required_capabilities=("release.sign",),
                )
        finally:
            await client.aclose()

    async def test_rejects_unsigned_card_by_default(self) -> None:
        resolver, client = resolver_for(
            lambda _: httpx.Response(200, content=json_response(agent_card()))
        )
        try:
            with self.assertRaisesRegex(
                A2APeerRouteResolutionError,
                "unsigned Agent Card rejected",
            ):
                await resolver.resolve(_ORIGIN, required_capabilities=())
        finally:
            await client.aclose()

    async def test_explicit_unsigned_compatibility_remains_bounded(self) -> None:
        card = agent_card()
        card["authSchemes"] = []
        resolver, client = resolver_for(
            lambda _: httpx.Response(200, content=json_response(card)),
            policy=A2AAgentCardTrustPolicy(allow_unsigned_cards=True),
        )
        try:
            route = await resolver.resolve(_ORIGIN, required_capabilities=())
        finally:
            await client.aclose()
        self.assertFalse(route.requires_bearer)

    async def test_rejects_pin_mismatch_without_exposing_key_material(self) -> None:
        signed, public_key = sign_card(agent_card())
        resolver, client = resolver_for(
            lambda _: httpx.Response(200, content=json_response(signed)),
            policy=A2AAgentCardTrustPolicy(trusted_public_keys={_ORIGIN: ["ff" * 32]}),
        )
        try:
            with self.assertRaises(A2APeerRouteResolutionError) as raised:
                await resolver.resolve(_ORIGIN, required_capabilities=())
        finally:
            await client.aclose()
        self.assertIn("not trusted", str(raised.exception))
        self.assertNotIn(public_key, str(raised.exception))
        self.assertNotIn(signed["signature"], str(raised.exception))

    async def test_rejects_malformed_key_and_signature_without_echoing_values(
        self,
    ) -> None:
        for field, secret_value in (
            ("public_key", "provider-secret-public-key"),
            ("signature", "provider-secret-signature"),
        ):
            with self.subTest(field=field):
                signed, _ = sign_card(agent_card())
                if field == "signature":
                    signed[field] = secret_value
                else:
                    signed["security"][field] = secret_value
                resolver, client = resolver_for(
                    lambda _, value=signed: httpx.Response(
                        200, content=json_response(value)
                    )
                )
                try:
                    with self.assertRaises(A2APeerRouteResolutionError) as raised:
                        await resolver.resolve(_ORIGIN, required_capabilities=())
                finally:
                    await client.aclose()
                self.assertNotIn(secret_value, str(raised.exception))

    async def test_rejects_well_formed_signature_after_card_tampering(self) -> None:
        signed, _ = sign_card(agent_card())
        signed["description"] = "tampered after signing"
        resolver, client = resolver_for(
            lambda _: httpx.Response(200, content=json_response(signed))
        )
        try:
            with self.assertRaisesRegex(
                A2APeerRouteResolutionError,
                "signature verification failed",
            ):
                await resolver.resolve(_ORIGIN, required_capabilities=())
        finally:
            await client.aclose()

    async def test_rejects_protocol_or_card_origin_drift(self) -> None:
        for field, value, message in (
            ("protocolVersion", "2.0", "unsupported protocol"),
            ("url", "https://attacker.example.test/agent", "Card URL crossed"),
        ):
            with self.subTest(field=field):
                card = agent_card()
                card[field] = value
                signed, _ = sign_card(card)
                resolver, client = resolver_for(
                    lambda _, candidate=signed: httpx.Response(
                        200,
                        content=json_response(candidate),
                    )
                )
                try:
                    with self.assertRaisesRegex(
                        A2APeerRouteResolutionError,
                        message,
                    ):
                        await resolver.resolve(_ORIGIN, required_capabilities=())
                finally:
                    await client.aclose()

    async def test_rejects_cross_origin_task_endpoint(self) -> None:
        card = agent_card()
        card["endpoints"]["taskApi"] = "https://attacker.example.test/tasks"
        signed, _ = sign_card(card)
        resolver, client = resolver_for(
            lambda _: httpx.Response(200, content=json_response(signed))
        )
        try:
            with self.assertRaisesRegex(
                A2APeerRouteResolutionError,
                "task endpoint crossed",
            ):
                await resolver.resolve(_ORIGIN, required_capabilities=())
        finally:
            await client.aclose()

    async def test_rejects_cross_origin_card_redirect(self) -> None:
        resolver, client = resolver_for(
            lambda _: httpx.Response(
                307,
                headers={"Location": "https://attacker.example.test/agent-card.json"},
            )
        )
        try:
            with self.assertRaisesRegex(
                A2APeerRouteResolutionError,
                "redirect crossed",
            ):
                await resolver.resolve(_ORIGIN, required_capabilities=())
        finally:
            await client.aclose()

    async def test_accepts_same_origin_card_redirect(self) -> None:
        signed, _ = sign_card(agent_card())
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if len(paths) == 1:
                return httpx.Response(307, headers={"Location": "/cards/current"})
            return httpx.Response(200, content=json_response(signed))

        resolver, client = resolver_for(handler)
        try:
            await resolver.resolve(_ORIGIN, required_capabilities=())
        finally:
            await client.aclose()
        self.assertEqual(paths, ["/.well-known/agent-card.json", "/cards/current"])

    async def test_rejects_oversized_and_duplicate_field_cards(self) -> None:
        cases = (
            (
                "oversized",
                lambda _: httpx.Response(200, content=b"x" * 65),
                64,
                "size limit",
            ),
            (
                "duplicate",
                lambda _: httpx.Response(200, content=b'{"name":"a","name":"b"}'),
                1 << 20,
                "strict JSON",
            ),
        )
        for name, handler, limit, message in cases:
            with self.subTest(name=name):
                resolver, client = resolver_for(
                    handler,
                    max_response_bytes=limit,
                )
                try:
                    with self.assertRaisesRegex(
                        A2APeerRouteResolutionError,
                        message,
                    ):
                        await resolver.resolve(_ORIGIN, required_capabilities=())
                finally:
                    await client.aclose()

    async def test_rejects_unknown_card_fields(self) -> None:
        signed, _ = sign_card(agent_card())
        signed["credential"] = "must-not-be-accepted"
        resolver, client = resolver_for(
            lambda _: httpx.Response(200, content=json_response(signed))
        )
        try:
            with self.assertRaisesRegex(
                A2APeerRouteResolutionError,
                "route validation failed",
            ):
                await resolver.resolve(_ORIGIN, required_capabilities=())
        finally:
            await client.aclose()

    async def test_strict_schema_rejects_falsey_values_with_wrong_types(self) -> None:
        for field, value in (
            ("authSchemes", ""),
            ("tags", ""),
        ):
            with self.subTest(field=field):
                card = agent_card()
                card[field] = value
                resolver, client = resolver_for(
                    lambda _, candidate=card: httpx.Response(
                        200,
                        content=json_response(candidate),
                    ),
                    policy=A2AAgentCardTrustPolicy(allow_unsigned_cards=True),
                )
                try:
                    with self.assertRaisesRegex(
                        A2APeerRouteResolutionError,
                        "route validation failed",
                    ):
                        await resolver.resolve(_ORIGIN, required_capabilities=())
                finally:
                    await client.aclose()

    def test_trust_policy_validates_origins_keys_and_conflicting_modes(self) -> None:
        with self.assertRaises(ValueError):
            A2AAgentCardTrustPolicy(
                allow_unsigned_cards=True,
                require_pinned_keys=True,
            )
        with self.assertRaises(ValueError):
            A2AAgentCardTrustPolicy(
                trusted_public_keys={f"{_ORIGIN}/path": ["00" * 32]}
            )
        with self.assertRaises(ValueError):
            A2AAgentCardTrustPolicy(
                trusted_public_keys={_ORIGIN: ["provider-secret-key"]}
            )

    def test_signing_payload_matches_gateway_struct_and_float_encoding(self) -> None:
        signed, public_key = sign_card(agent_card())
        payload = _agent_card_signing_payload(signed).decode()

        self.assertNotIn('"signature"', payload)
        self.assertIn('"minimum":1e-7', payload)
        self.assertIn('"threshold":0.000001', payload)
        self.assertIn(f'"public_key":"{public_key}"', payload)
        self.assertLess(payload.index('"protocolVersion"'), payload.index('"name"'))
        self.assertLess(payload.index('"capabilities"'), payload.index('"skills"'))


if __name__ == "__main__":
    unittest.main()
