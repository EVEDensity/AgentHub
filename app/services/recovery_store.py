"""Private SQLite images anchored to admitted Mission Control checkpoints."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import ValidationError

from app.services.recovery_image import (
    MAX_RESUME_IMAGE_BYTES,
    ResumeImage,
    ResumeImageError,
)


def runner_state_directory(workspace: Path, configured: Path | None = None) -> Path:
    workspace = workspace.resolve()
    base = configured or Path(os.environ.get(
        "AGENTHUB_RUNNER_STATE_ROOT", str(Path.home() / ".agenthub-runner-state")))
    base = base.resolve()
    if base == workspace or workspace in base.parents:
        raise ResumeImageError("private Runner state must be outside the writable workspace")
    key = hashlib.sha256(str(workspace).encode()).hexdigest()
    directory = base / key
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    return directory


class ResumeImageStore:
    """Keep exact admitted anchors plus candidates, never pick the latest candidate."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS resume_images (
                checkpoint_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL,
                work_unit_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                sequence INTEGER NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL
            )""")
        if os.name != "nt":
            self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, image: ResumeImage) -> str:
        body, digest = image.encode()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT digest FROM resume_images WHERE checkpoint_id=?",
                                       (image.checkpoint_id,)).fetchone()
            if prior is not None and prior["digest"] != digest:
                raise ResumeImageError("checkpoint image identity cannot be overwritten")
            connection.execute("INSERT OR IGNORE INTO resume_images VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (image.checkpoint_id, image.mission_id, image.work_unit_id,
                                image.attempt, image.sequence, digest, body))
            connection.execute("COMMIT")
        return digest

    def load(self, checkpoint_id: str, digest: str) -> ResumeImage:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM resume_images WHERE checkpoint_id=?",
                                     (checkpoint_id,)).fetchone()
        if row is None:
            raise ResumeImageError("private resume image is missing")
        body = row["body"]
        if not isinstance(body, str) or len(body.encode()) > MAX_RESUME_IMAGE_BYTES:
            raise ResumeImageError("private resume image is invalid or oversized")
        actual = "sha256:" + hashlib.sha256(body.encode()).hexdigest()
        if row["digest"] != digest or actual != digest:
            raise ResumeImageError("private resume image digest does not match admitted checkpoint")
        try:
            image = ResumeImage.model_validate_json(body)
        except (ValueError, ValidationError) as exc:
            raise ResumeImageError("private resume image is malformed") from exc
        if image.checkpoint_id != checkpoint_id:
            raise ResumeImageError("private resume image identity drifted")
        return image

    def prune_before(self, image: ResumeImage) -> None:
        """Called only after public admission; retain current anchor and any candidate ahead."""
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM resume_images WHERE mission_id=? AND work_unit_id=? "
                               "AND attempt=? AND sequence<?", (image.mission_id, image.work_unit_id,
                                                               image.attempt, image.sequence))
