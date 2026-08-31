"""Stack bootstrap installer (north-star M3 / §4.0 product baseline).

Implements the downloader half of the desktop bootstrap contract:

1. fetch a stack manifest from the release source;
2. download every listed file into ``<data>/stacks/<version>-<commit>/``
   with sha256 verification and resume support (already-verified files
   are skipped; partial downloads land in ``*.part`` and are renamed
   only after the digest matches);
3. write the stack manifest copy where the desktop shell reads it
   (``local-services/stack-manifest.json``, matching
   ``desktop/src-tauri/src/services.rs`` version_dir_name/manifest
   conventions);
4. atomically switch ``stacks/.pinned`` to the new stack only after
   every file verified — a failed install never disturbs the pinned
   stack, and older stacks remain for rollback.

The network layer is injected (``fetch_fn``) so the whole pipeline is
testable offline. ``fetch_fn(url) -> bytes`` must raise on failure.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILE_NAME = "stack-manifest.json"
PIN_FILE_NAME = ".pinned"
STACK_SERVICES_DIR = "local-services"

FetchFn = Callable[[str], bytes]


class StackInstallerError(RuntimeError):
    """Raised when a stack cannot be installed or verified."""


@dataclass(frozen=True)
class StackFileEntry:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class StackManifest:
    schema_version: int
    version: str
    commit: str
    generated_at: str
    files: tuple[StackFileEntry, ...]

    @property
    def directory_name(self) -> str:
        return version_dir_name(self.version, self.commit)


def version_dir_name(version: str, commit: str) -> str:
    """Match the desktop shell's stack directory naming exactly.

    Mirrors ``version_dir_name_of`` in ``services.rs``: alphanumeric
    plus ``. _ -`` pass through; everything else becomes ``_``.
    """
    raw = f"{version}-{commit}" if commit else version
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw)


def parse_manifest(payload: bytes) -> StackManifest:
    """Parse and validate a stack manifest document.

    Raises ``StackInstallerError`` on schema mismatch or malformed
    entries — never returns a half-validated manifest.
    """
    import json

    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise StackInstallerError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StackInstallerError("manifest must be a JSON object")
    schema_version = data.get("schemaVersion")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        raise StackInstallerError(
            f"unsupported manifest schemaVersion: {schema_version!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION})"
        )
    version = str(data.get("version") or "")
    if not version:
        raise StackInstallerError("manifest has no version")
    entries: list[StackFileEntry] = []
    raw_files = data.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise StackInstallerError("manifest lists no files")
    for item in raw_files:
        if not isinstance(item, dict):
            raise StackInstallerError("manifest file entry must be an object")
        path = str(item.get("path") or "")
        digest = str(item.get("sha256") or "")
        size = item.get("size")
        if not path or not digest:
            raise StackInstallerError("manifest file entry missing path/sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise StackInstallerError(f"manifest entry has invalid size: {path}")
        # Paths are relative and must never escape the stack directory.
        normalized = Path(path).as_posix()
        if normalized.startswith("/") or ".." in Path(path).parts:
            raise StackInstallerError(f"manifest entry escapes stack dir: {path}")
        entries.append(
            StackFileEntry(path=normalized, sha256=digest, size=size)
        )
    return StackManifest(
        schema_version=schema_version,
        version=version,
        commit=str(data.get("commit") or ""),
        generated_at=str(data.get("generatedAt") or ""),
        files=tuple(entries),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(path: Path, entry: StackFileEntry) -> bool:
    if not path.is_file() or path.stat().st_size != entry.size:
        return False
    return _sha256_file(path) == entry.sha256


def stacks_root(data_dir: Path) -> Path:
    return data_dir / "stacks"


def list_installed_stacks(data_dir: Path) -> list[StackManifest]:
    """Return manifests of installed stacks, newest generation first.

    Mirrors the desktop shell's discovery: read
    ``stacks/<version>/local-services/stack-manifest.json``.
    """
    import json

    results: list[StackManifest] = []
    root = stacks_root(data_dir)
    if not root.is_dir():
        return results
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        manifest_path = entry / STACK_SERVICES_DIR / MANIFEST_FILE_NAME
        if not manifest_path.is_file():
            continue
        try:
            results.append(parse_manifest(manifest_path.read_bytes()))
        except StackInstallerError:
            continue  # unreadable stacks are skipped, never fatal
    results.sort(key=lambda m: m.generated_at, reverse=True)
    return results


def read_pinned(data_dir: Path) -> str | None:
    raw = (stacks_root(data_dir) / PIN_FILE_NAME).read_text(
        encoding="utf-8"
    ) if (stacks_root(data_dir) / PIN_FILE_NAME).is_file() else ""
    name = raw.strip()
    return name or None


def pin_stack(data_dir: Path, manifest: StackManifest) -> None:
    """Atomically pin a stack directory name.

    The pin lands through a temp file + rename so a crash mid-write
    can never corrupt the current pin.
    """
    root = stacks_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    pin_path = root / PIN_FILE_NAME
    temp_path = root / f"{PIN_FILE_NAME}.tmp"
    temp_path.write_text(manifest.directory_name, encoding="utf-8")
    temp_path.replace(pin_path)


def install_stack(
    *,
    manifest_url: str,
    data_dir: Path,
    fetch_fn: FetchFn,
    base_url: str = "",
    on_progress: Callable[[str, int, int], None] | None = None,
) -> StackManifest:
    """Download and verify one stack, then pin it.

    ``base_url`` prefixes every manifest file path when the release
    source serves files from a different root than the manifest.
    ``on_progress(path, index, total)`` reports per-file progress.

    Failure at any point raises ``StackInstallerError`` and leaves the
    existing pin untouched; partially downloaded files remain as
    ``*.part`` so a retry resumes where it stopped.
    """
    try:
        manifest = parse_manifest(fetch_fn(manifest_url))
    except StackInstallerError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport errors
        raise StackInstallerError(f"cannot fetch manifest: {exc}") from exc

    stack_dir = stacks_root(data_dir) / manifest.directory_name
    services_dir = stack_dir / STACK_SERVICES_DIR
    services_dir.mkdir(parents=True, exist_ok=True)

    total = len(manifest.files)
    for index, entry in enumerate(manifest.files, 1):
        target = stack_dir / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if _verify_file(target, entry):
            if on_progress is not None:
                on_progress(entry.path, index, total)
            continue  # resume: already verified
        url = f"{base_url.rstrip('/')}/{entry.path.lstrip('/')}" if base_url else entry.path
        part_path = target.with_name(target.name + ".part")
        try:
            payload = fetch_fn(url)
        except Exception as exc:  # noqa: BLE001 - transport errors
            raise StackInstallerError(
                f"download failed for {entry.path}: {exc}"
            ) from exc
        digest = hashlib.sha256(payload).hexdigest()
        if digest != entry.sha256 or len(payload) != entry.size:
            raise StackInstallerError(
                f"integrity check failed for {entry.path}: "
                f"expected sha256 {entry.sha256}, got {digest}"
            )
        part_path.write_bytes(payload)
        part_path.replace(target)
        if on_progress is not None:
            on_progress(entry.path, index, total)

    # Manifest copy goes where the desktop shell discovers stacks.
    import json

    manifest_copy = stack_dir / STACK_SERVICES_DIR / MANIFEST_FILE_NAME
    manifest_copy.write_text(
        json.dumps(
            {
                "schemaVersion": manifest.schema_version,
                "version": manifest.version,
                "commit": manifest.commit,
                "generatedAt": manifest.generated_at,
                "files": [
                    {
                        "path": entry.path,
                        "sha256": entry.sha256,
                        "size": entry.size,
                    }
                    for entry in manifest.files
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    pin_stack(data_dir, manifest)
    return manifest


def default_fetch_fn(url: str) -> bytes:
    """HTTP GET returning the response body; raises on any failure."""
    import httpx

    response = httpx.get(url, timeout=120, follow_redirects=True)
    response.raise_for_status()
    return response.content
