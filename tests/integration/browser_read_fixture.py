"""Local HTTP documents for real browser conformance; no public network calls."""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from app.services.browser_read import BrowserReadProfile, LocalBrowserReadBackend


def real_backend(origin: str, *, timeout_ms=15_000, max_text_chars=16_384, process_backend=None):
    node = shutil.which('node')
    if node is None:
        raise RuntimeError('Enabled browser conformance requires Node.js')
    cache = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
    if not cache:
        raise RuntimeError('Enabled browser conformance requires an explicit browser cache')
    profile = BrowserReadProfile(node_path=Path(node).resolve(),
        worker_dir=Path(__file__).resolve().parents[2] / 'services/node/browser-read',
        origins=(origin,), browser_cache_path=Path(cache).resolve(), allow_http_loopback=True,
        timeout_ms=timeout_ms, max_text_chars=max_text_chars)
    return LocalBrowserReadBackend(profile, process_backend=process_backend)


class BrowserDocumentFixture:
    def __init__(self):
        self.hits, self.foreign_hits, self.tasks = [], [], set()
        self.slow_started = asyncio.Event()

    async def start(self):
        self.foreign = await asyncio.start_server(self.foreign_request, '127.0.0.1', 0)
        self.server = await asyncio.start_server(self.request, '127.0.0.1', 0)
        self.origin = f'http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}'
        self.foreign_origin = f'http://127.0.0.1:{self.foreign.sockets[0].getsockname()[1]}'
        return self

    async def close(self):
        for server in (self.server, self.foreign):
            server.close()
            await server.wait_closed()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def foreign_request(self, reader, writer):
        await self.handle(reader, writer, foreign=True)

    async def request(self, reader, writer):
        await self.handle(reader, writer, foreign=False)

    async def handle(self, reader, writer, *, foreign):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            lines = raw.decode('latin-1').split('\r\n')
            method, target, _ = lines[0].split(' ', 2)
            headers = dict(line.split(': ', 1) for line in lines[1:] if ': ' in line)
            path = urlsplit(target).path
            (self.foreign_hits if foreign else self.hits).append((method, path, headers))
            if path == '/slow':
                self.slow_started.set()
                await asyncio.sleep(60)
            status, extra, body = self.document(path, headers)
            data = body.encode('utf-8')
            response = f'HTTP/1.1 {status}\r\nContent-Type: {extra.pop("Content-Type", "text/html; charset=utf-8")}\r\n'
            response += ''.join(f'{key}: {value}\r\n' for key, value in extra.items())
            response += f'Content-Length: {len(data)}\r\nConnection: close\r\n\r\n'
            writer.write(response.encode('ascii') + data)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.tasks.discard(task)

    def document(self, path, headers):
        if path == '/redirect':
            return '302 Found', {'Location': self.origin + '/document'}, ''
        if path == '/external-redirect':
            return '302 Found', {'Location': self.foreign_origin + '/redirect-target'}, ''
        if path == '/missing':
            return '404 Not Found', {}, '<body>Missing fixture</body>'
        if path == '/download':
            return '200 OK', {'Content-Disposition': 'attachment; filename=fixture.txt'}, 'fixture download'
        if path == '/nonhtml':
            return '200 OK', {'Content-Type': 'application/json'}, '{"fixture":true}'
        if path == '/large':
            return '200 OK', {}, '<body>' + '界😀' * 400 + '</body>'
        if path == '/empty':
            return '200 OK', {}, '<html><head><title></title></head><body></body></html>'
        if path == '/cookie':
            cookie = next((value for key, value in headers.items() if key.lower() == 'cookie'), 'NO_COOKIE')
            return '200 OK', {'Set-Cookie': 'fixture_session=owned; Path=/; SameSite=Lax'}, f'<body>{cookie}</body>'
        if path == '/refresh':
            return '200 OK', {}, '<meta http-equiv="refresh" content="0;url=/refresh-target"><body>Initial document</body>'
        return '200 OK', {}, (f'<html><title>Fixture title</title><body>Browser fixture 中文'
            f'<script>document.body.innerText="SCRIPT_RAN";fetch("{self.foreign_origin}/script")</script>'
            f'<img src="{self.foreign_origin}/pixel"><iframe src="/frame"></iframe></body></html>')
