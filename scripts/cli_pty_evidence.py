"""Physical PTY evidence gate for width rendering and Ctrl-C shutdown.

This gate reports SKIP on Windows because the standard library has no portable
ConPTY API. On POSIX it allocates a real pseudo terminal, sends ETX, and
requires an orderly exit without a traceback.
"""
from __future__ import annotations

import json
import os
import platform
import select
import sys
import time
from pathlib import Path

try:
    import pty
except ImportError:  # Windows has no POSIX PTY module.
    pty = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.production_evidence import new_evidence, write_evidence


def main() -> int:
    output = os.environ.get("AGENTHUB_PTY_EVIDENCE_OUTPUT", "").strip()
    if os.name == "nt" or pty is None or not hasattr(pty, "fork"):
        return _emit(output, status="SKIP", errorType="posix_pty_required", platform=platform.system())
    results = []
    for width in (40, 80, 120):
        result = _probe(width)
        results.append(result)
        if result["status"] != "PASS":
            return _emit(output, status="FAIL", errorType=result["errorType"], widths=results)
    return _emit(output, status="PASS", widths=results, ctrlCHandled=True)


def _probe(width: int) -> dict[str, object]:
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["COLUMNS"] = str(width)
        os.execl(sys.executable, sys.executable, "-c", (
            "import signal,time,sys; "
            "signal.signal(signal.SIGINT, lambda *_: sys.exit(130)); "
            "print('agenthub tty ready', flush=True); time.sleep(30)"
        ))
    captured = bytearray()
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline and b"ready" not in captured:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    captured.extend(os.read(fd, 4096))
                except OSError:
                    break
        os.write(fd, b"\x03")
        _, status = os.waitpid(pid, 0)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    text = captured.decode("utf-8", errors="replace")
    exit_code = os.waitstatus_to_exitcode(status)
    error_type = "ctrl_c_not_clean" if exit_code != 130 else "traceback" if "Traceback" in text else ""
    return {"width": width, "status": "PASS" if b"ready" in captured and exit_code == 130 and "Traceback" not in text else "FAIL", "exitCode": exit_code, "errorType": error_type}


def _emit(output: str, **fields: object) -> int:
    record = new_evidence(scope="tty-pty", evidence_level="physical-pty", **fields)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    write_evidence(record, scope="tty", mirror_path=output or None)
    return 0 if record["status"] in {"PASS", "SKIP"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
