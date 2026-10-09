"""OS-owned execution locks vanish on process death, without deleting evidence."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


class RecoveryExecutionBusy(RuntimeError):
    """Another local process or worker still owns this exact attempt."""


class RecoveryExecutionLock:
    def __init__(self, directory: Path, scope: str) -> None:
        path = directory / (hashlib.sha256(scope.encode()).hexdigest() + ".lock")
        self.handle = path.open("a+b")
        self.handle.seek(0)
        if path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RecoveryExecutionBusy("Runner attempt is active in another local worker") from exc

    def close(self) -> None:
        if self.handle.closed:
            return
        if os.name == "nt":
            import msvcrt
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()

    def __del__(self) -> None:
        handle = getattr(self, "handle", None)
        if handle is not None and not handle.closed:
            self.close()
