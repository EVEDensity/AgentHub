"""Operator profile, runtime pin and canonical origin contract."""

import copy
import tempfile
import unittest
from pathlib import Path

from app.services.browser_read.profile import (
    BrowserReadError,
    BrowserReadProfile,
    canonical_url,
)


def profile_fixture(root, **updates):
    worker = root / "worker"
    worker.mkdir(exist_ok=True)
    for name in ("worker.mjs", "policy.mjs", "package-lock.json"):
        (worker / name).write_bytes(b"operator-owned fixture\n")
    node = root / "node"
    node.write_bytes(b"node fixture bytes")
    values = {
        "node_path": node,
        "worker_dir": worker,
        "origins": ("https://example.com",),
    }
    return BrowserReadProfile(**(values | updates))


class BrowserProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_identity_freezes_runtime_origins_limits_and_scope(self):
        first = profile_fixture(self.root)
        self.assertEqual(
            first.validate_url("https://EXAMPLE.com:443"), "https://example.com/"
        )
        self.assertEqual(
            first.validate_url("https://example.com/a?q=x%20y"),
            "https://example.com/a?q=x%20y",
        )
        scope = first.scope()
        scope["origins"].clear()
        self.assertEqual(first.origins, ("https://example.com",))
        second = profile_fixture(self.root, max_reads=2)
        self.assertNotEqual(first.digest, second.digest)
        first.verify_runtime()
        (first.worker_dir / "policy.mjs").write_bytes(b"modified")
        with self.assertRaises(BrowserReadError):
            first.verify_runtime()

    def test_ambiguous_credential_and_out_of_origin_urls_are_refused(self):
        profile = profile_fixture(self.root)
        invalid = (
            "https://other.com/",
            "http://example.com/",
            "file:///etc/passwd",
            "https://u:p@example.com/",
            "https://example.com/#x",
            "https://example.com/a/../b",
            "https://example.com/a/%2e/b",
            "https://example.com/space here",
            "https://example.com/'",
            "https://example.com/%zz",
            "https://example.com/%FF",
            "https://example.com/%C3%28",
            "https://example.com/%C0%AE",
            "https://127.1/",
            "https://example.com/你好",
            "https://example.com/\\x",
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(BrowserReadError):
                profile.validate_url(value)

    def test_http_requires_explicit_literal_loopback_origin(self):
        profile = profile_fixture(
            self.root, allow_http_loopback=True, origins=("http://127.0.0.1:8123",)
        )
        self.assertEqual(
            profile.validate_url("http://127.0.0.1:8123/a"), "http://127.0.0.1:8123/a"
        )
        for url in (
            "http://localhost:8123/",
            "http://example.com/",
            "http://127.0.0.2:8123/",
        ):
            with self.subTest(url=url), self.assertRaises(BrowserReadError):
                profile.validate_url(url)
        with self.assertRaises(BrowserReadError):
            profile_fixture(
                self.root, allow_http_loopback=True, origins=("http://127.0.0.2:8123",)
            )
        ipv6 = profile_fixture(
            self.root, allow_http_loopback=True, origins=("http://[::1]:8123",)
        )
        self.assertEqual(
            ipv6.validate_url("http://[::1]:8123/path"), "http://[::1]:8123/path"
        )

    def test_origin_and_limit_configuration_fail_closed(self):
        invalid = (
            {"origins": ("https://example.com/",)},
            {"origins": ("https://EXAMPLE.com",)},
            {"origins": ("https://example.com:443",)},
            {"origins": ()},
            {"origins": ("https://example.com", "https://example.com")},
            {"max_reads": True},
            {"max_text_chars": 255},
            {"timeout_ms": 30001},
            {"cleanup_timeout_seconds": float("nan")},
            {"allow_http_loopback": 1},
        )
        for updates in invalid:
            with self.subTest(updates=updates), self.assertRaises(BrowserReadError):
                profile_fixture(self.root, **copy.deepcopy(updates))

    def test_normal_canonical_url_preserves_encoded_queries(self):
        self.assertEqual(
            canonical_url("https://Example.com:443/path?q=%22x%22"),
            "https://example.com/path?q=%22x%22",
        )
        self.assertEqual(
            canonical_url("https://example.com/%E4%BD%A0?q=%FF"),
            "https://example.com/%E4%BD%A0?q=%FF",
        )
