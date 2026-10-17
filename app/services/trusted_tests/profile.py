"""Operator-frozen external acceptance tests for the trusted-local experiment."""
from __future__ import annotations

import json
import os
import sys
from importlib.metadata import version
from dataclasses import dataclass
from pathlib import Path

from .snapshot import FrozenSnapshot, SnapshotError, SnapshotLimits, digest_bytes, freeze_snapshot, verify_snapshot


@dataclass(frozen=True)
class FrozenTestProfile:
    suite: FrozenSnapshot
    argv: tuple[str, ...]
    execution_mode: str
    timeout_seconds: float
    max_output_bytes: int
    max_junit_bytes: int
    max_workdir_bytes: int
    digest: str
    interpreter_digest: str
    pytest_version: str

    def close(self) -> None:
        self.suite.close()

    def __enter__(self) -> FrozenTestProfile:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def _profile_content(profile: FrozenTestProfile) -> bytes:
    return json.dumps({
        'format': 'agenthub.operator-pytest-profile.v1', 'suiteDigest': profile.suite.digest,
        'argv': profile.argv, 'executionMode': profile.execution_mode,
        'timeoutSeconds': profile.timeout_seconds, 'maxOutputBytes': profile.max_output_bytes,
        'maxJunitBytes': profile.max_junit_bytes, 'maxWorkdirBytes': profile.max_workdir_bytes,
        'driver': 'isolated-python-pytest.v1', 'interpreterDigest': profile.interpreter_digest,
        'pytestVersion': profile.pytest_version,
    }, sort_keys=True, separators=(',', ':')).encode()


def _interpreter_digest(executable: str) -> str:
    with Path(executable).open('rb') as stream:
        content = stream.read(64 * 1024 * 1024 + 1)
    if len(content) > 64 * 1024 * 1024:
        raise SnapshotError('operator interpreter exceeds byte limit')
    return digest_bytes(content)


def _validate_argv(argv: tuple[str, ...]) -> None:
    if len(argv) < 3 or not Path(argv[0]).is_absolute() or argv[1:3] != ('-m', 'pytest'):
        raise SnapshotError('operator profile must name an absolute Python executable and -m pytest')
    if not Path(argv[0]).is_file() or len(argv) > 12:
        raise SnapshotError('operator profile executable or argument bound is invalid')
    if Path(argv[0]).resolve() != Path(sys.executable).resolve():
        raise SnapshotError('trusted-local profiles require the current Python interpreter')
    allowed = {'-q', '-v', '-vv', '-x', '--strict-markers', '--disable-warnings'}
    if any(item not in allowed for item in argv[3:]):
        raise SnapshotError('operator pytest profile contains unsupported arguments')


def validate_profile(profile: FrozenTestProfile) -> None:
    verify_snapshot(profile.suite)
    if profile.execution_mode != 'trusted-local':
        raise SnapshotError('only the explicit trusted-local experiment is supported')
    _validate_argv(profile.argv)
    if not 0 < profile.timeout_seconds <= 600:
        raise SnapshotError('operator test timeout must be in (0, 600]')
    if not 1024 <= profile.max_output_bytes <= 4 * 1024 * 1024:
        raise SnapshotError('operator log limit is invalid')
    if not 1024 <= profile.max_junit_bytes <= 1024 * 1024:
        raise SnapshotError('operator JUnit limit is invalid')
    if not 1024 <= profile.max_workdir_bytes <= 128 * 1024 * 1024:
        raise SnapshotError('operator work directory limit is invalid')
    if _interpreter_digest(profile.argv[0]) != profile.interpreter_digest or version('pytest') != profile.pytest_version:
        raise SnapshotError('operator test runtime changed')
    if digest_bytes(_profile_content(profile)) != profile.digest:
        raise SnapshotError('operator test profile changed')


def freeze_test_profile(acceptance_root: Path, argv: tuple[str, ...], storage_root: Path, *,
                        execution_mode: str, timeout_seconds: float = 30,
                        max_output_bytes: int = 256 * 1024, max_junit_bytes: int = 1024 * 1024,
                        max_workdir_bytes: int = 32 * 1024 * 1024) -> FrozenTestProfile:
    """Only an operator config supplies this function; no objective is parsed."""
    _validate_argv(argv)
    # Equivalent executable links must still launch this virtual environment,
    # whose pytest distribution version is bound below.
    argv = (sys.executable, *argv[1:])
    suite = freeze_snapshot(acceptance_root, storage_root, SnapshotLimits(max_files=500, max_total_bytes=5 * 1024 * 1024))
    try:
        prototype = FrozenTestProfile(suite, tuple(argv), execution_mode, float(timeout_seconds),
                                      max_output_bytes, max_junit_bytes, max_workdir_bytes, '',
                                      _interpreter_digest(argv[0]), version('pytest'))
        from dataclasses import replace
        profile = replace(prototype, digest=digest_bytes(_profile_content(prototype)))
        validate_profile(profile)
        if not any(item.path.endswith('.py') for item in suite.files):
            raise SnapshotError('operator acceptance suite has no Python files')
        return profile
    except BaseException:
        suite.close()
        raise


def test_environment(temporary_root: Path) -> dict[str, str]:
    """No arbitrary inherited variables, credentials, PYTHONPATH or pytest options."""
    environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT') if key in os.environ}
    environment.update(TEMP=str(temporary_root), TMP=str(temporary_root), TMPDIR=str(temporary_root),
                       HOME=str(temporary_root), USERPROFILE=str(temporary_root),
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1',
                       PYTHONNOUSERSITE='1', PYTHONHASHSEED='0', LANG='C.UTF-8')
    return environment
