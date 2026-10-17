"""Explicit real Linux/Chromium gate; enabled runs cannot skip dependencies."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

from app.services.browser_read import BrowserReadError
from tests.integration.browser_read_fixture import BrowserDocumentFixture, real_backend


def process_identity(pid):
    try:
        fields = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] not in {'Z', 'X'} else None
    except (FileNotFoundError, ProcessLookupError):
        return None


class RecordingProcess:
    def __init__(self, actual):
        self.actual, self.started, self.root = actual, asyncio.Event(), None

    async def run(self, *args, **kwargs):
        self.root = kwargs['cwd']
        self.started.set()
        return await self.actual.run(*args, **kwargs)


class BrowserReadLiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if os.environ.get('AGENTHUB_REAL_BROWSER') != '1':
            self.skipTest('Real Chromium conformance requires explicit AGENTHUB_REAL_BROWSER=1')
        self.web = await BrowserDocumentFixture().start()
        self.addAsyncCleanup(self.web.close)

    async def test_real_document_is_anonymous_static_and_has_no_subresource_requests(self):
        result = await real_backend(self.web.origin).read(self.web.origin + '/document', timeout_seconds=20)
        self.assertTrue(result.success, result.errorCode)
        self.assertEqual(result.title, 'Fixture title')
        self.assertIn('Browser fixture 中文', result.text)
        self.assertNotIn('SCRIPT_RAN', result.text)
        self.assertTrue(result.cleanupConfirmed)
        self.assertGreaterEqual(result.blockedResourceCount, 1)
        self.assertEqual(self.web.foreign_hits, [])
        self.assertEqual([(method, path) for method, path, _ in self.web.hits], [('GET', '/document')])

    async def test_redirects_http_errors_and_downloads_cannot_be_success(self):
        backend = real_backend(self.web.origin)
        cases = {'/redirect': 'redirect_rejected', '/external-redirect': 'redirect_rejected',
                 '/missing': 'http_error', '/download': 'download_rejected', '/nonhtml': 'document_type_unsupported'}
        for path, error in cases.items():
            with self.subTest(path=path):
                result = await backend.read(self.web.origin + path, timeout_seconds=20)
                self.assertFalse(result.success)
                self.assertEqual(result.errorCode, error)
                self.assertTrue(result.cleanupConfirmed)
        self.assertEqual(self.web.foreign_hits, [])
        self.assertNotIn('/document', [path for _, path, _ in self.web.hits])

    async def test_contexts_never_reuse_cookies_and_empty_documents_are_honest(self):
        backend = real_backend(self.web.origin)
        first = await backend.read(self.web.origin + '/cookie', timeout_seconds=20)
        second = await backend.read(self.web.origin + '/cookie', timeout_seconds=20)
        self.assertTrue(first.success and second.success)
        self.assertEqual((first.text, second.text), ('NO_COOKIE', 'NO_COOKIE'))
        self.assertNotEqual(first.requestId, second.requestId)
        empty = await backend.read(self.web.origin + '/empty', timeout_seconds=20)
        self.assertTrue(empty.success)
        self.assertEqual((empty.title, empty.text, empty.textBytes, empty.truncated), ('', '', 0, False))

    async def test_unicode_truncation_is_reported_and_meta_refresh_does_not_fetch_another_document(self):
        backend = real_backend(self.web.origin, max_text_chars=256)
        result = await backend.read(self.web.origin + '/large', timeout_seconds=20)
        self.assertTrue(result.success)
        self.assertEqual(len(result.text), 256)
        self.assertTrue(result.truncated)
        self.assertGreater(result.textBytes, len(result.text.encode()))
        refreshed = await backend.read(self.web.origin + '/refresh', timeout_seconds=20)
        self.assertNotIn('/refresh-target', [path for _, path, _ in self.web.hits])
        if refreshed.success:
            self.assertEqual((refreshed.finalUrl, refreshed.text), (self.web.origin + '/refresh', 'Initial document'))

    async def test_cancellation_after_chromium_start_removes_owned_processes_and_preserves_another_read(self):
        backend = real_backend(self.web.origin)
        recorder = RecordingProcess(backend._process)
        backend._process = recorder
        slow = asyncio.create_task(backend.read(self.web.origin + '/slow', timeout_seconds=25))
        await asyncio.wait_for(self.web.slow_started.wait(), 15)
        started = json.loads((recorder.root / 'started.json').read_text())
        identities = {pid: process_identity(pid) for pid in (started['workerPid'], started['browserPid'])}
        self.assertTrue(all(identities.values()), 'Cancellation must observe the real worker and browser alive')
        other = asyncio.create_task(real_backend(self.web.origin).read(self.web.origin + '/document', timeout_seconds=20))
        slow.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(slow, 15)
        other_result = await other
        self.assertTrue(other_result.success)
        for pid, identity in identities.items():
            self.assertNotEqual(process_identity(pid), identity, f'Owned process {pid} survived cancellation')

    async def test_total_deadline_cannot_be_reported_as_a_completed_read(self):
        backend = real_backend(self.web.origin, timeout_ms=10_000)
        recorder = RecordingProcess(backend._process)
        backend._process = recorder
        task = asyncio.create_task(backend.read(self.web.origin + '/slow', timeout_seconds=10))
        await asyncio.wait_for(self.web.slow_started.wait(), 8)
        started = json.loads((recorder.root / 'started.json').read_text())
        identities = {pid: process_identity(pid) for pid in (started['workerPid'], started['browserPid'])}
        self.assertTrue(all(identities.values()), 'Deadline must observe the real HTTP request and live processes')
        try:
            result = await asyncio.wait_for(task, 20)
        except (TimeoutError, BrowserReadError):
            result = None
        if result is not None:
            self.assertFalse(result.success)
            self.assertTrue(result.cleanupConfirmed)
        for pid, identity in identities.items():
            self.assertNotEqual(process_identity(pid), identity, f'Owned process {pid} survived its deadline')
