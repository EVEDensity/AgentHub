"""Trusted-local process limits use real child processes and cleanup."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from app.services.trusted_tests.process import TrustedLocalProcessBackend
from app.services.trusted_tests.profile import test_environment as child_environment


class TrustedProcessTests(unittest.IsolatedAsyncioTestCase):
    async def run_script(self, source, *, timeout=3, maximum=4096):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            return await TrustedLocalProcessBackend().run(
                (sys.executable, '-I', '-c', source), cwd=root, environment=child_environment(root),
                timeout_seconds=timeout, max_output_bytes=maximum, monitored_root=root,
                max_workdir_bytes=1024 * 1024,
            )

    async def test_output_excess_is_bounded_and_never_a_clean_result(self):
        result = await self.run_script('import sys;sys.stdout.write("x"*1000000)', maximum=1024)
        self.assertTrue(result.limit_exceeded)
        self.assertLessEqual(len(result.output), 1024)

    async def test_timeout_stops_the_process_group(self):
        result = await self.run_script('import time;time.sleep(10)', timeout=0.15)
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.exit_code, 0)

    async def test_async_task_cancellation_waits_for_process_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            task = asyncio.create_task(TrustedLocalProcessBackend().run(
                (sys.executable, '-I', '-c', 'import time;time.sleep(10)'),
                cwd=root, environment=child_environment(root), timeout_seconds=15,
                max_output_bytes=1024, monitored_root=root, max_workdir_bytes=1024 * 1024,
            ))
            await asyncio.sleep(0.15); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task

    async def test_files_exceeding_the_workdir_bound_interrupt_execution(self):
        result = await self.run_script('from pathlib import Path;import time;Path("large").write_bytes(b"x"*2000000);time.sleep(10)')
        self.assertTrue(result.limit_exceeded)
        self.assertNotEqual(result.exit_code, 0)

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group regression; Windows uses a kill-on-close Job Object')
    async def test_normal_exit_cleans_descendant_with_redirected_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            child = (
                'import time;from pathlib import Path;'
                'Path("child-ready").write_text("ready");time.sleep(0.8);'
                'Path("late-marker").write_text("leaked")'
            )
            source = (
                'import subprocess,sys,time;from pathlib import Path\n'
                f'subprocess.Popen([sys.executable,"-I","-c",{child!r}], '
                'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
                'deadline=time.monotonic()+2\n'
                'while not Path("child-ready").exists() and time.monotonic()<deadline: time.sleep(0.01)\n'
                'assert Path("child-ready").exists()\n'
                'print("child started")\n'
            )
            result = await TrustedLocalProcessBackend().run(
                (sys.executable, '-I', '-c', source), cwd=root, environment=child_environment(root),
                timeout_seconds=4, max_output_bytes=4096, monitored_root=root,
                max_workdir_bytes=1024 * 1024,
            )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn(b'child started', result.output)
            self.assertFalse(result.timed_out or result.limit_exceeded)
            # Keep the directory alive so a leaked descendant could write here.
            await asyncio.sleep(1.1)
            self.assertFalse((root / 'late-marker').exists())
