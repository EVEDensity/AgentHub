"""Bounded local source snapshots and reproducible Git binary patches.

This is a consistency boundary, not an OS access-control boundary. Storage and
candidate roots must be outside the source workspace; callers own their lifetime.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


class SnapshotError(ValueError):
    """A snapshot cannot be captured or reconstructed exactly."""


@dataclass(frozen=True)
class SnapshotLimits:
    max_files: int = 2000
    max_file_bytes: int = 2 * 1024 * 1024
    max_total_bytes: int = 20 * 1024 * 1024
    exclude_globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    digest: str
    size: int
    mode: int


@dataclass
class FrozenSnapshot:
    root: Path
    files: tuple[SnapshotFile, ...]
    manifest_bytes: bytes
    digest: str
    limits: SnapshotLimits
    _temporary: tempfile.TemporaryDirectory
    source_type: Literal['git-commit', 'git-unborn', 'non-git'] = 'non-git'
    baseline_commit: str | None = None

    @property
    def tree_digest(self) -> str:
        """Byte/mode identity independent of source repository provenance."""
        return digest_bytes(_manifest(self.files))

    def close(self) -> None:
        self._temporary.cleanup()

    def __enter__(self) -> FrozenSnapshot:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


@dataclass(frozen=True)
class CandidatePatch:
    patch_bytes: bytes
    input_snapshot_digest: str
    candidate_snapshot_digest: str


_OMITTED = frozenset({
    '.git', '.hg', '.svn', '.agenthub', '.tmp', '.venv', 'venv', 'node_modules',
    '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache', '.next',
    'target', 'dist', 'build', 'attempt-snapshots', '.ssh', '.aws', '.azure',
})
_SECRET_GLOBS = ('.env*', '*.pem', '*.key', '*.p12', '*.pfx', '*.token',
                 'id_rsa*', 'id_ed25519*', '*credentials*.json', '*secrets*')


def digest_bytes(content: bytes) -> str:
    return 'sha256:' + hashlib.sha256(content).hexdigest()


def _manifest(files: tuple[SnapshotFile, ...], source_type: str | None = None,
              baseline_commit: str | None = None) -> bytes:
    document = {'format': 'agenthub.source-snapshot.v1', 'files': [
        {'path': item.path, 'digest': item.digest, 'size': item.size, 'mode': item.mode}
        for item in files
    ]}
    if source_type is not None:
        if source_type not in ('git-commit', 'git-unborn', 'non-git'):
            raise SnapshotError('snapshot source provenance is invalid')
        if (source_type == 'git-commit') != (baseline_commit is not None):
            raise SnapshotError('snapshot baseline commit does not match its source type')
        document.update(format='agenthub.source-snapshot.v2', sourceType=source_type, baselineCommit=baseline_commit)
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _git_provenance_command(source: Path, *arguments: str) -> subprocess.CompletedProcess:
    executable = shutil.which('git')
    if not executable:
        raise SnapshotError('Git source provenance requires an available Git executable')
    environment = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS='0',
                       GIT_NO_REPLACE_OBJECTS='1', LC_ALL='C')
    try:
        return subprocess.run([executable, *arguments], cwd=source, capture_output=True,
                              timeout=10, check=False, env=environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotError('Git source provenance is unavailable') from exc


def _has_git_metadata(source: Path) -> bool:
    marker = any(os.path.lexists(parent / '.git') for parent in (source, *source.parents))
    bare_layout = (source / 'HEAD').is_file() and (source / 'objects').is_dir() and (source / 'refs').is_dir()
    return marker or bare_layout


def _source_provenance(source: Path) -> tuple[str, str | None]:
    # Inspect known Git markers before invoking Git. Malformed repositories must
    # fail, whereas an ordinary directory is explicitly not a Git source.
    if not _has_git_metadata(source):
        return 'non-git', None
    probe = _git_provenance_command(source, 'rev-parse', '--is-inside-work-tree')
    if probe.returncode or probe.stdout.strip() != b'true':
        raise SnapshotError('Git source metadata is invalid or not a working tree')
    head = _git_provenance_command(source, 'rev-parse', '--verify', 'HEAD^{commit}')
    if head.returncode == 0:
        commit = head.stdout.strip().decode('ascii')
        if len(commit) not in (40, 64) or any(character not in '0123456789abcdef' for character in commit):
            raise SnapshotError('Git HEAD did not resolve to a commit object ID')
        return 'git-commit', commit
    branch = _git_provenance_command(source, 'symbolic-ref', '--quiet', 'HEAD')
    if branch.returncode == 0 and branch.stdout.strip().startswith(b'refs/heads/'):
        reference = branch.stdout.strip().decode('utf-8')
        existing = _git_provenance_command(source, 'show-ref', '--verify', '--quiet', reference)
        if existing.returncode == 1:
            return 'git-unborn', None
    raise SnapshotError('Git HEAD is unavailable or corrupt')


def _ambiguous_part(part: str) -> bool:
    devices = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    return (part in ('.', '..') or ':' in part or part.endswith((' ', '.'))
            or part.split('.', 1)[0].casefold() in devices or any(ord(character) < 32 for character in part))


def _safe_relative(relative: str) -> None:
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or len(relative) > 512 or '\\' in relative:
        raise SnapshotError('snapshot path is not a bounded relative POSIX path')
    if any(_ambiguous_part(part) for part in path.parts):
        raise SnapshotError('snapshot path escapes or is platform ambiguous')
    if path.as_posix() != relative:
        raise SnapshotError('snapshot path is not canonical')


def _is_link(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, 'st_file_attributes', 0) & 0x400)


def _omitted(path: PurePosixPath, limits: SnapshotLimits) -> bool:
    return any(part.lower() in _OMITTED for part in path.parts) or any(
        PurePosixPath(str(path).casefold()).match(pattern.casefold()) for pattern in (*_SECRET_GLOBS, *limits.exclude_globs)
    )


def _root(path: Path) -> Path:
    absolute = path.absolute()
    if not absolute.is_dir() or any(_is_link(part.lstat()) for part in (absolute, *absolute.parents)):
        raise SnapshotError('snapshot root must be a real directory without links')
    return absolute.resolve()


def _outside(source: Path, destination: Path) -> None:
    if destination == source or source in destination.parents or destination in source.parents:
        raise SnapshotError('snapshot, candidate and source directories must not overlap')


def _read_file(path: Path, maximum: int) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    if _is_link(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise SnapshotError('snapshot contains a link or special file')
    if before.st_size > maximum:
        raise SnapshotError('snapshot file exceeds byte limit')
    with path.open('rb') as stream:
        content = stream.read(maximum + 1)
        opened = os.fstat(stream.fileno())
    after = path.lstat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if len(content) > maximum or identity(before) != identity(after) or identity(before) != identity(opened):
        raise SnapshotError('snapshot file changed while being read')
    return content, before


def _walk_files(root: Path, limits: SnapshotLimits, reject_omitted: bool):
    pending, seen = [root], set()
    while pending:
        directory = pending.pop()
        if _is_link(directory.lstat()):
            raise SnapshotError('snapshot contains a linked directory')
        with os.scandir(directory) as children:
            entries = sorted(children, key=lambda entry: entry.name)
        if len(entries) + len(seen) > limits.max_files * 2:
            raise SnapshotError('snapshot exceeds entry limit')
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if _omitted(PurePosixPath(relative), limits):
                if reject_omitted:
                    raise SnapshotError('patch added an excluded secret or runtime path')
                continue
            _safe_relative(relative)
            if relative.casefold() in seen or len(seen) >= limits.max_files:
                raise SnapshotError('snapshot paths collide or exceed count limit')
            seen.add(relative.casefold())
            metadata = path.lstat()
            if _is_link(metadata):
                raise SnapshotError('snapshot contains a link')
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(path)
            else:
                yield path, relative


def _inventory(root: Path, limits: SnapshotLimits, copy_to: Path | None = None, *, reject_omitted: bool = False) -> tuple[SnapshotFile, ...]:
    files, total = [], 0
    for path, relative in _walk_files(root, limits, reject_omitted):
        content, metadata = _read_file(path, limits.max_file_bytes)
        total += len(content)
        if total > limits.max_total_bytes:
            raise SnapshotError('snapshot exceeds total byte limit')
        mode = 0o755 if metadata.st_mode & 0o111 else 0o644
        files.append(SnapshotFile(relative, digest_bytes(content), len(content), mode))
        if copy_to is not None:
            target = copy_to / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(mode)
    return tuple(sorted(files, key=lambda item: item.path))


def verify_snapshot(snapshot: FrozenSnapshot) -> None:
    if _inventory(_root(snapshot.root), snapshot.limits) != snapshot.files:
        raise SnapshotError('frozen snapshot bytes or modes changed')
    if (_manifest(snapshot.files, snapshot.source_type, snapshot.baseline_commit) != snapshot.manifest_bytes
            or digest_bytes(snapshot.manifest_bytes) != snapshot.digest):
        raise SnapshotError('frozen snapshot manifest changed')


def freeze_snapshot(workspace: Path, storage_root: Path, limits: SnapshotLimits = SnapshotLimits()) -> FrozenSnapshot:
    source, storage = _root(workspace), _root(storage_root)
    _outside(source, storage)
    temporary = tempfile.TemporaryDirectory(prefix='source-', dir=storage)
    try:
        tree = Path(temporary.name) / 'tree'
        tree.mkdir()
        provenance = _source_provenance(source)
        files = _inventory(source, limits, tree)
        if _inventory(source, limits) != files:
            raise SnapshotError('source changed during snapshot capture')
        if _source_provenance(source) != provenance:
            raise SnapshotError('Git baseline changed during snapshot capture')
        source_type, baseline_commit = provenance
        manifest = _manifest(files, source_type, baseline_commit)
        return FrozenSnapshot(tree, files, manifest, digest_bytes(manifest), limits, temporary,
                              source_type=source_type, baseline_commit=baseline_commit)
    except BaseException:
        temporary.cleanup()
        raise


def materialize_candidate(snapshot: FrozenSnapshot, destination: Path) -> Path:
    verify_snapshot(snapshot)
    destination = _root(destination.absolute().parent) / destination.name
    _outside(snapshot.root, destination)
    if destination.exists() or destination.is_symlink():
        raise SnapshotError('candidate destination must be new and outside the snapshot')
    destination.mkdir()
    _inventory(snapshot.root, snapshot.limits, destination)
    return destination


def _git(arguments: list[str], cwd: Path, content: bytes | None = None) -> bytes:
    executable = shutil.which('git')
    if not executable:
        raise SnapshotError('trusted local patching requires Git')
    environment = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP') if key in os.environ}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS='0', GIT_CEILING_DIRECTORIES=str(cwd))
    result = subprocess.run([executable, '-c', 'core.autocrlf=false', '-c', 'core.quotePath=false', *arguments],
                            cwd=cwd, input=content, capture_output=True, timeout=15, env=environment)
    expected = (0, 1) if arguments[0] == 'diff' else (0,)
    if result.returncode not in expected:
        raise SnapshotError('Git could not reproduce candidate patch')
    return result.stdout


def capture_candidate(snapshot: FrozenSnapshot, candidate_root: Path) -> CandidatePatch:
    verify_snapshot(snapshot)
    candidate = _root(candidate_root)
    _outside(snapshot.root, candidate)
    with tempfile.TemporaryDirectory(prefix='patch-', dir=snapshot.root.parent.parent) as directory:
        stage = Path(directory)
        materialize_candidate(snapshot, stage / 'a')
        (stage / 'b').mkdir()
        files = _inventory(candidate, snapshot.limits, stage / 'b')
        if _inventory(candidate, snapshot.limits) != files:
            raise SnapshotError('candidate changed during capture')
        patch = _git(['diff', '--no-index', '--binary', '--no-renames', '--no-ext-diff', '--no-textconv', '--', 'a', 'b'], stage)
        if len(patch) > 4 * snapshot.limits.max_total_bytes + 1024 * 1024:
            raise SnapshotError('candidate patch exceeds byte limit')
        return CandidatePatch(patch, snapshot.digest, digest_bytes(_manifest(files)))


def reconstruct_candidate(snapshot: FrozenSnapshot, candidate: CandidatePatch, destination: Path) -> Path:
    if candidate.input_snapshot_digest != snapshot.digest:
        raise SnapshotError('candidate patch refers to another input snapshot')
    if len(candidate.patch_bytes) > 4 * snapshot.limits.max_total_bytes + 1024 * 1024:
        raise SnapshotError('candidate patch exceeds byte limit')
    target = materialize_candidate(snapshot, destination)
    if candidate.patch_bytes:
        _git(['apply', '--no-index', '--binary', '--whitespace=nowarn', '-p2', '-'], target, candidate.patch_bytes)
    files = _inventory(target, snapshot.limits, reject_omitted=True)
    if digest_bytes(_manifest(files)) != candidate.candidate_snapshot_digest:
        raise SnapshotError('candidate patch does not reconstruct the frozen candidate')
    return target


def tree_digest(root: Path, limits: SnapshotLimits = SnapshotLimits()) -> str:
    return digest_bytes(_manifest(_inventory(_root(root), limits)))
