"""Async file I/O helpers — wrap blocking filesystem calls with asyncio.to_thread().

Usage::

    from app.utils.async_file import aread_text, awrite_text, aexists, aunlink, aglob

    content = await aread_text(some_path)
    await awrite_text(some_path, content)
    if await aexists(some_path):
        await aunlink(some_path)
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# Path stat / existence
# ═══════════════════════════════════════════════════════════════════════


async def aexists(path: Path | str) -> bool:
    return await asyncio.to_thread(lambda: Path(path).exists())


async def aisfile(path: Path | str) -> bool:
    return await asyncio.to_thread(lambda: Path(path).is_file())


async def aisdir(path: Path | str) -> bool:
    return await asyncio.to_thread(lambda: Path(path).is_dir())


async def astat_size(path: Path | str) -> int:
    return await asyncio.to_thread(lambda: Path(path).stat().st_size)


async def astat_mtime(path: Path | str) -> float:
    return await asyncio.to_thread(lambda: Path(path).stat().st_mtime)


# ═══════════════════════════════════════════════════════════════════════
# Read / write
# ═══════════════════════════════════════════════════════════════════════


async def aread_text(path: Path | str, encoding: str = "utf-8") -> str:
    return await asyncio.to_thread(lambda: Path(path).read_text(encoding=encoding))


async def awrite_text(path: Path | str, content: str, encoding: str = "utf-8") -> None:
    await asyncio.to_thread(lambda: Path(path).write_text(content, encoding=encoding))


async def aread_bytes(path: Path | str) -> bytes:
    return await asyncio.to_thread(lambda: Path(path).read_bytes())


async def awrite_bytes(path: Path | str, data: bytes) -> None:
    await asyncio.to_thread(lambda: Path(path).write_bytes(data))


# ═══════════════════════════════════════════════════════════════════════
# JSON
# ═══════════════════════════════════════════════════════════════════════


async def aread_json(path: Path | str) -> Any:
    """Read and parse a JSON file."""
    text = await aread_text(path)
    return json.loads(text)


async def awrite_json(path: Path | str, data: Any, *, indent: int = 2) -> None:
    """Serialize and write JSON to a file."""
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    await awrite_text(path, text)


# ═══════════════════════════════════════════════════════════════════════
# Directory / glob
# ═══════════════════════════════════════════════════════════════════════


async def aglob(pattern: Path | str, max_results: int = 500) -> list[Path]:
    """Return list of Paths matching the glob pattern."""
    return await asyncio.to_thread(
        lambda: list(Path(pattern).parent.glob(Path(pattern).name))[:max_results]
        if "*" in str(pattern)
        else list(Path(pattern).glob("*"))[:max_results]
    )


async def aglob_simple(base_dir: Path | str, pattern: str) -> list[Path]:
    """Glob a pattern relative to base_dir and return matching Paths."""
    return await asyncio.to_thread(lambda: list(Path(base_dir).glob(pattern)))


async def aiterdir(path: Path | str) -> list[Path]:
    """Return sorted list of directory entries."""
    return await asyncio.to_thread(lambda: sorted(Path(path).iterdir()))


async def amkdir(path: Path | str, parents: bool = True, exist_ok: bool = True) -> None:
    await asyncio.to_thread(lambda: Path(path).mkdir(parents=parents, exist_ok=exist_ok))


# ═══════════════════════════════════════════════════════════════════════
# File operations
# ═══════════════════════════════════════════════════════════════════════


async def aunlink(path: Path | str, missing_ok: bool = True) -> None:
    try:
        file_path = Path(path)
        await asyncio.to_thread(lambda: file_path.unlink(missing_ok=missing_ok))
    except FileNotFoundError:
        if not missing_ok:
            raise


async def acopy(src: Path | str, dst: Path | str) -> None:
    await asyncio.to_thread(lambda: shutil.copy2(str(src), str(dst)))


async def armtree(path: Path | str, ignore_missing: bool = True) -> None:
    try:
        await asyncio.to_thread(
            lambda: shutil.rmtree(str(path), ignore_errors=ignore_missing)
        )
    except FileNotFoundError:
        if not ignore_missing:
            raise


async def aopen_read(path: Path | str, encoding: str = "utf-8") -> str:
    """Read entire file contents via open()."""
    def _read():
        with open(str(path), "r", encoding=encoding) as f:
            return f.read()
    return await asyncio.to_thread(_read)


async def aopen_write(path: Path | str, content: str | bytes, encoding: str = "utf-8") -> None:
    """Write entire file contents via open()."""
    def _write():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "w" if isinstance(content, str) else "wb", encoding=encoding if isinstance(content, str) else None) as f:
            f.write(content)  # type: ignore[arg-type]
    await asyncio.to_thread(_write)
