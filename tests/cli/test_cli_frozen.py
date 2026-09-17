"""Unit tests for the frozen-binary CLI path (north-star M3 / I-2).

The npm distribution ships a PyInstaller onefile binary. It boots its
mission-control subprocess by re-invoking itself with the hidden
``_serve`` subcommand instead of ``python -m uvicorn`` (no interpreter
exists on the target host).
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from app.cli import runtime
from app.cli.main import build_parser


class ServerCommandTests(unittest.TestCase):
    def test_dev_mode_uses_uvicorn_module(self) -> None:
        with mock.patch.object(runtime, "is_frozen", return_value=False):
            command = runtime.server_command(28123)
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1:4], ["-m", "uvicorn", "main:app"])
        self.assertIn("28123", command)

    def test_frozen_mode_reinvokes_self_with_serve(self) -> None:
        with mock.patch.object(runtime, "is_frozen", return_value=True):
            command = runtime.server_command(28123)
        self.assertEqual(
            command,
            [sys.executable, "_serve", "--port", "28123"],
        )

    def test_is_frozen_reads_sys_frozen(self) -> None:
        # Not frozen under pytest; assert the sentinel is what matters.
        self.assertEqual(runtime.is_frozen(), bool(getattr(sys, "frozen", False)))


class ServeSubcommandTests(unittest.TestCase):
    def test_serve_subcommand_parses(self) -> None:
        args = build_parser().parse_args(["_serve", "--port", "28123"])
        self.assertEqual(args.command, "_serve")
        self.assertEqual(args.port, 28123)
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.log_level, "warning")

    def test_serve_requires_port(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["_serve"])

    def test_serve_has_no_public_help_text(self) -> None:
        # argparse lists subcommand names in the usage line, but the
        # internal command must not document itself (help == SUPPRESS).
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["--help"])
        self.assertIn("_serve              ==SUPPRESS==", buffer.getvalue())


class ServerCwdTests(unittest.TestCase):
    def test_dev_mode_uses_repo_root(self) -> None:
        with mock.patch.object(runtime, "is_frozen", return_value=False):
            self.assertEqual(runtime.server_cwd(), str(runtime.REPO_ROOT))

    def test_frozen_mode_runs_anywhere(self) -> None:
        with mock.patch.object(runtime, "is_frozen", return_value=True):
            self.assertIsNone(runtime.server_cwd())


if __name__ == "__main__":
    unittest.main()
