"""Browser profile links count toward budget without following their targets."""

import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.browser_read.process import inspect_workdir


class BrowserWorkdirTests(unittest.TestCase):
    def _walk_failure(self, root, error):
        def walk(_root, *, followlinks, onerror):
            self.assertFalse(followlinks)
            onerror(error)
            return []

        with patch("app.services.browser_read.process.os.walk", side_effect=walk):
            return inspect_workdir(root, 1024)

    def test_disappeared_child_directory_enoent_during_walk_is_not_a_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            error = FileNotFoundError(
                errno.ENOENT,
                "private child name",
                str(root / "chromium-profile" / "cache"),
            )
            budget = self._walk_failure(root, error)
            self.assertIsNone(budget.reason)
            self.assertEqual((budget.error_number, budget.error_type), (None, None))

    def test_root_missing_permission_and_io_errors_still_fail_with_safe_diagnostics(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failures = (
                FileNotFoundError(errno.ENOENT, "private root", str(root)),
                FileNotFoundError(errno.ENOENT, "private missing filename"),
                FileNotFoundError(
                    errno.ENOENT, "private outside", str(root.parent / "outside")
                ),
                PermissionError(errno.EACCES, "private child", str(root / "cache")),
                OSError(errno.EIO, "private child", str(root / "cache")),
            )
            for error in failures:
                with self.subTest(error_type=type(error).__name__):
                    budget = self._walk_failure(root, error)
                    self.assertEqual(budget.reason, "workdir_unreadable")
                    self.assertEqual(budget.error_number, error.errno)
                    self.assertEqual(budget.error_type, type(error).__name__)
                    self.assertNotIn("private", repr(budget))
            missing = inspect_workdir(root / "missing-root", 1024)
            self.assertEqual(
                (missing.reason, missing.error_number),
                ("workdir_unreadable", errno.ENOENT),
            )

    def test_byte_and_file_count_limits_remain_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").write_bytes(b"x" * 1025)
            self.assertEqual(inspect_workdir(root, 1024).reason, "workdir_bytes")
            self.assertIsNone(inspect_workdir(root, 1025).reason)
            with patch(
                "app.services.browser_read.process.os.walk",
                return_value=[(root, [], ["data"] * 5001)],
            ):
                self.assertEqual(
                    inspect_workdir(root, 10_000_000).reason, "workdir_entries"
                )

    def test_symlink_directory_and_dangling_singleton_link_are_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, outside = base / "owned", base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "large").write_bytes(b"x" * 100_000)
            try:
                (root / "profile-cache").symlink_to(outside, target_is_directory=True)
                (root / "SingletonLock").symlink_to(base / "nonexistent-host-12345")
            except OSError:
                self.skipTest("This filesystem does not permit creating symlinks")
            budget = inspect_workdir(root, 4096)
            self.assertIsNone(budget.reason)
            self.assertEqual(budget.entries, 2)
            self.assertEqual(
                budget.bytes, sum(path.lstat().st_size for path in root.iterdir())
            )

    def test_unreadable_directory_is_failure_and_diagnostic_does_not_include_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "app.services.browser_read.process.os.walk",
                side_effect=PermissionError("private path"),
            ):
                budget = inspect_workdir(root, 1024)
            self.assertEqual(budget.reason, "workdir_unreadable")
            self.assertNotIn("private path", repr(budget))
