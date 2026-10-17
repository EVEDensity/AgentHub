"""Exact owner cleanup and repeated cancellation; Linux processes are opt-host."""

import asyncio
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.services.browser_read import process_cleanup as cleanup
from app.services.browser_read.process import LinuxBrowserProcessBackend


def proc_fixture(root, pid, owner, *, state="S"):
    directory = root / str(pid)
    directory.mkdir()
    fields = [state, "1", str(pid)] + ["0"] * 17
    fields[19] = "12345"
    (directory / "stat").write_text(
        f"{pid} (fixture chrome) " + " ".join(fields), encoding="ascii"
    )
    (directory / "environ").write_bytes(
        f"SAFE=fixture\x00AGENTHUB_BROWSER_OWNER={owner}\x00".encode()
    )


class BrowserCleanupTests(unittest.IsolatedAsyncioTestCase):
    def test_proc_scan_selects_exact_marker_and_ignores_zombies(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owner = Path(directory), str(uuid.uuid4())
            proc_fixture(root, 101, owner)
            proc_fixture(root, 102, owner + "-different")
            proc_fixture(root, 103, owner, state="Z")
            with patch.object(
                cleanup.os, "geteuid", return_value=root.stat().st_uid, create=True
            ):
                processes = cleanup._processes(owner, root)
            self.assertEqual(processes, (cleanup.OwnedProcess(101, 101, "12345"),))

    def test_same_uid_permission_failure_is_unknown_not_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owner = Path(directory), str(uuid.uuid4())
            proc_fixture(root, 101, owner)
            with (
                patch.object(
                    cleanup.os, "geteuid", return_value=root.stat().st_uid, create=True
                ),
                patch.object(cleanup, "_matches", side_effect=PermissionError),
                self.assertRaises(PermissionError),
            ):
                cleanup._processes(owner, root)

    def test_unicode_process_name_does_not_change_identity_or_break_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root, owner = Path(directory), str(uuid.uuid4())
            proc_fixture(root, 101, owner)
            path = root / "101" / "stat"
            path.write_bytes(
                path.read_bytes().replace(b"fixture chrome", "普通浏览器".encode())
            )
            with patch.object(
                cleanup.os, "geteuid", return_value=root.stat().st_uid, create=True
            ):
                self.assertEqual(
                    cleanup._processes(owner, root),
                    (cleanup.OwnedProcess(101, 101, "12345"),),
                )

    def test_pidfd_signal_requires_matching_current_start_time_and_marker(self):
        for matches in (False, True):
            with (
                self.subTest(matches=matches),
                patch.object(
                    cleanup,
                    "owned_processes",
                    return_value=(cleanup.OwnedProcess(101, 101, "start"),),
                ),
                patch.object(
                    cleanup.os, "pidfd_open", return_value=91, create=True
                ) as opened,
                patch.object(cleanup.signal, "pidfd_send_signal", create=True) as sent,
                patch.object(cleanup.signal, "SIGKILL", 9, create=True),
                patch.object(cleanup.os, "close") as closed,
                patch.object(cleanup, "_same_process", return_value=matches),
            ):
                cleanup._signal_owned(str(uuid.uuid4()))
                opened.assert_called_once_with(101)
                closed.assert_called_once_with(91)
                self.assertEqual(sent.call_count, int(matches))

    async def test_permission_failure_never_confirms_process_cleanup(self):
        with patch.object(cleanup, "_signal_owned", side_effect=PermissionError):
            self.assertFalse(
                await cleanup.cleanup_owned_processes(str(uuid.uuid4()), 0.1)
            )

    async def test_repeated_cancel_cannot_cancel_tracked_cleanup(self):
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def owned(_owner, _timeout):
            entered.set()
            await release.wait()
            finished.set()
            return True

        with patch.object(cleanup, "cleanup_owned_processes", owned):
            task = asyncio.create_task(cleanup.confirmed_cleanup(str(uuid.uuid4()), 1))
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())


@unittest.skipUnless(
    sys.platform == "linux", "Real pidfd/process cleanup requires Linux /proc"
)
class LinuxBrowserProcessTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, source, root, *, timeout=2, maximum=4096):
        owner = str(uuid.uuid4())
        result = await LinuxBrowserProcessBackend().run(
            (sys.executable, "-I", "-c", source),
            cwd=root,
            environment={"PATH": os.defpath, "AGENTHUB_BROWSER_OWNER": owner},
            timeout_seconds=timeout,
            max_output_bytes=maximum,
            monitored_root=root,
            max_workdir_bytes=1_048_576,
        )
        self.assertEqual(cleanup.owned_processes(owner), ())
        return result

    async def test_actual_stdout_nonzero_and_output_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = await self.execute(
                "print('real child output'); raise SystemExit(7)", root
            )
            self.assertEqual(result.exit_code, 7)
            self.assertIn(b"real child output", result.output)
            limited = await self.execute("print('x'*1000000)", root, maximum=128)
            self.assertTrue(limited.limit_exceeded)
            self.assertLessEqual(len(limited.output), 128)

    async def test_detached_descendant_is_cleaned_without_started_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = "import time;from pathlib import Path;Path('detached-ready').write_text('yes');time.sleep(10)"
            source = (
                "import subprocess,sys,time;from pathlib import Path\n"
                f"subprocess.Popen([sys.executable,'-I','-c',{child!r}],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                "deadline=time.monotonic()+2\n"
                "while not Path('detached-ready').exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                "assert Path('detached-ready').exists()\nprint('spawned')\n"
            )
            result = await self.execute(source, root, timeout=3)
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.limit_exceeded)
            self.assertTrue((root / "detached-ready").exists())
            self.assertFalse((root / "started.json").exists())

    async def test_timeout_and_cancellation_leave_no_live_marked_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = await self.execute(
                "import time;time.sleep(10)", root, timeout=0.15
            )
            self.assertTrue(result.timed_out)
            owner = str(uuid.uuid4())
            task = asyncio.create_task(
                LinuxBrowserProcessBackend().run(
                    (
                        sys.executable,
                        "-I",
                        "-c",
                        "import time;print('ready',flush=True);time.sleep(10)",
                    ),
                    cwd=root,
                    environment={"PATH": os.defpath, "AGENTHUB_BROWSER_OWNER": owner},
                    timeout_seconds=5,
                    max_output_bytes=4096,
                    monitored_root=root,
                    max_workdir_bytes=1_048_576,
                )
            )
            for _index in range(100):
                if cleanup.owned_processes(owner):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(cleanup.owned_processes(owner))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(cleanup.owned_processes(owner), ())
