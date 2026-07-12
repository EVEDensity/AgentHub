"""Video Processor — NATS subscriber for video frame extraction requests.

Subscribes to NATS ``agenthub.session.>`` stream (specifically for
``video.frame_extraction.requested`` events published by the Go gateway).

Attempts to extract keyframes using ffmpeg (subprocess). Falls back to a
clear error status when ffmpeg is unavailable.

Publishes ``video.frame_extraction.completed`` with frame data or error info
back to NATS.

Usage:
    This module is typically loaded and started by main.py during lifespan.

Events:
    Subscribe:  video.frame_extraction.requested (via agenthub.session.>)
    Publish:    video.frame_extraction.completed
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

# NATS subjects for video frame extraction.
VIDEO_FRAME_REQUEST_SUBJECT = "agenthub.session.events"
VIDEO_FRAME_COMPLETED_SUBJECT = "agenthub.session.events"

# Custom event type strings (not in shared.events.EventType enum).
EVENT_VIDEO_FRAME_REQUEST = "video.frame_extraction.requested"
EVENT_VIDEO_FRAME_COMPLETED = "video.frame_extraction.completed"

# Default limits.
DEFAULT_INTERVAL_SECONDS = 5
DEFAULT_MAX_FRAMES = 10
MAX_VIDEO_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB
EXTRACTION_TIMEOUT_SECONDS = 300  # 5 minutes


# ── Data Models ────────────────────────────────────────────────────────

@dataclass
class VideoFrameExtractionRequest:
    """Parsed request from NATS event payload."""

    video_id: str
    video_path: str
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    max_frames: int = DEFAULT_MAX_FRAMES
    event_id: str = ""
    trace_id: str = ""
    tenant_id: str = ""
    session_id: str = ""


@dataclass
class ExtractedFrame:
    """A single extracted video frame."""

    index: int
    timestamp_seconds: float
    data: str  # base64-encoded JPEG


@dataclass
class VideoFrameExtractionResult:
    """Result published back to NATS after extraction."""

    video_id: str
    status: str  # "completed", "failed", "no_ffmpeg"
    frames: list[dict[str, Any]] = field(default_factory=list)
    error_message: str = ""
    ffmpeg_available: bool = False
    completed_at: str = ""


# ── Public API ─────────────────────────────────────────────────────────


class VideoProcessor:
    """NATS subscriber that processes video frame extraction requests."""

    def __init__(self, nats_url: str = "nats://127.0.0.1:4222") -> None:
        self._nats_url = nats_url
        self._nc: Any = None
        self._js: Any = None
        self._sub: Any = None
        self._running = False
        self._ffmpeg_path: str | None = None

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Connect to NATS and start listening for video frame requests."""
        self._ffmpeg_path = self._find_ffmpeg()
        if self._ffmpeg_path:
            logger.info("video_processor: ffmpeg found at %s", self._ffmpeg_path)
        else:
            logger.warning(
                "video_processor: ffmpeg not found — frame extraction will return "
                "no_ffmpeg status. Install ffmpeg for video processing support."
            )

        try:
            import nats
        except ImportError:
            logger.warning("video_processor: nats-py not installed; cannot subscribe")
            return

        try:
            self._nc = await nats.connect(
                self._nats_url,
                name="agenthub-video-processor",
                max_reconnect_attempts=-1,
                reconnect_time_wait=2,
            )
            self._js = self._nc.jetstream()
            logger.info("video_processor: connected to NATS at %s", self._nats_url)
        except Exception as e:
            logger.error("video_processor: NATS connection failed: %s", e, exc_info=True)
            return

        # Subscribe to session events stream
        try:
            self._sub = await self._js.subscribe(
                VIDEO_FRAME_REQUEST_SUBJECT,
                durable="video-processor",
                cb=self._on_message,
            )
            logger.info(
                "video_processor: subscribed to %s (durable=video-processor)",
                VIDEO_FRAME_REQUEST_SUBJECT,
            )
        except Exception as e:
            logger.error("video_processor: subscription failed: %s", e, exc_info=True)

        self._running = True

    async def stop(self) -> None:
        """Drain subscriptions and disconnect."""
        self._running = False
        if self._sub:
            try:
                await self._sub.unsubscribe()
            except Exception:
                pass
            self._sub = None
        if self._nc and not self._nc.is_closed:
            try:
                await self._nc.drain()
                await self._nc.close()
            except Exception:
                pass
        logger.info("video_processor: stopped")

    # ── Message Handler ────────────────────────────────────────────

    async def _on_message(self, msg: Any) -> None:
        """Process incoming NATS messages.

        Filters for video.frame_extraction.requested event type and
        delegates to the extraction pipeline.
        """
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            await msg.ack()
            return

        event_type = data.get("event_type", "")
        if event_type != EVENT_VIDEO_FRAME_REQUEST:
            # Not a video event — ack and skip
            await msg.ack()
            return

        payload = data.get("payload", {})
        request = VideoFrameExtractionRequest(
            video_id=payload.get("video_id", ""),
            video_path=payload.get("video_path", ""),
            interval_seconds=int(payload.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)),
            max_frames=int(payload.get("max_frames", DEFAULT_MAX_FRAMES)),
            event_id=data.get("event_id", ""),
            trace_id=data.get("trace_id", ""),
            tenant_id=data.get("tenant_id", ""),
            session_id=data.get("session_id", ""),
        )

        if not request.video_id or not request.video_path:
            logger.error("video_processor: invalid request (missing video_id or video_path)")
            await msg.ack()
            return

        logger.info(
            "video_processor: processing video %s (interval=%ds, max_frames=%d)",
            request.video_id,
            request.interval_seconds,
            request.max_frames,
        )

        try:
            result = await self._extract_frames(request)
        except Exception as e:
            logger.error(
                "video_processor: extraction failed for %s: %s",
                request.video_id, e, exc_info=True,
            )
            result = VideoFrameExtractionResult(
                video_id=request.video_id,
                status="failed",
                error_message=str(e),
                ffmpeg_available=self._ffmpeg_path is not None,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        await self._publish_result(result, data)
        await msg.ack()

    # ── Frame Extraction ───────────────────────────────────────────

    async def _extract_frames(
        self, request: VideoFrameExtractionRequest
    ) -> VideoFrameExtractionResult:
        """Extract keyframes from the video file.

        If ffmpeg is not available, returns a result with status "no_ffmpeg".
        Otherwise, uses ffmpeg subprocess to extract frames at the requested
        interval.
        """
        if not self._ffmpeg_path:
            return VideoFrameExtractionResult(
                video_id=request.video_id,
                status="no_ffmpeg",
                error_message=(
                    "ffmpeg is not installed on this server. "
                    "Install ffmpeg to enable video frame extraction."
                ),
                ffmpeg_available=False,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        # Validate video file exists
        video_path = Path(request.video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {request.video_path}")

        file_size = video_path.stat().st_size
        if file_size > MAX_VIDEO_SIZE_BYTES:
            raise ValueError(
                f"Video file too large: {file_size} bytes (max {MAX_VIDEO_SIZE_BYTES})"
            )

        # Get video duration using ffprobe
        duration_seconds = await self._get_video_duration(video_path)
        if duration_seconds <= 0:
            raise ValueError(f"Could not determine video duration for {request.video_id}")

        # Calculate frame timestamps
        interval = max(request.interval_seconds, 1)
        max_frames = min(request.max_frames, DEFAULT_MAX_FRAMES * 5)  # cap at 50
        timestamps: list[float] = []
        t = float(interval)
        while t < duration_seconds and len(timestamps) < max_frames:
            timestamps.append(t)
            t += float(interval)

        if not timestamps:
            timestamps = [duration_seconds / 2.0]  # at least one frame at midpoint

        # Create temp directory for extracted frames
        with tempfile.TemporaryDirectory(prefix=f"agenthub-video-{request.video_id}-") as tmpdir:
            tmp_path = Path(tmpdir)
            frames: list[ExtractedFrame] = []

            for idx, ts in enumerate(timestamps):
                output_file = tmp_path / f"frame_{idx:04d}.jpg"
                try:
                    await self._extract_single_frame(
                        video_path, ts, output_file
                    )
                    if output_file.exists() and output_file.stat().st_size > 0:
                        # Read and base64-encode the frame
                        frame_data = output_file.read_bytes()
                        frame_b64 = base64.b64encode(frame_data).decode("ascii")
                        frames.append(ExtractedFrame(
                            index=idx,
                            timestamp_seconds=ts,
                            data=frame_b64,
                        ))
                except Exception as e:
                    logger.warning(
                        "video_processor: failed to extract frame %d at %.1fs: %s",
                        idx, ts, e,
                    )

            result_frames = [
                {
                    "index": f.index,
                    "timestamp_seconds": f.timestamp_seconds,
                    "data": f.data,
                }
                for f in frames
            ]

            return VideoFrameExtractionResult(
                video_id=request.video_id,
                status="completed",
                frames=result_frames,
                ffmpeg_available=True,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    async def _get_video_duration(self, video_path: Path) -> float:
        """Get video duration in seconds using ffprobe."""
        cmd = [
            self._ffmpeg_path.replace("ffmpeg", "ffprobe"),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )
            if proc.returncode != 0:
                logger.warning(
                    "video_processor: ffprobe failed for %s: %s",
                    video_path, stderr.decode(errors="replace"),
                )
                return 0.0
            duration_str = stdout.decode().strip()
            return float(duration_str)
        except (ValueError, asyncio.TimeoutError) as e:
            logger.warning(
                "video_processor: could not get duration for %s: %s",
                video_path, e,
            )
            return 0.0

    async def _extract_single_frame(
        self, video_path: Path, timestamp: float, output_path: Path
    ) -> None:
        """Extract a single frame at the given timestamp using ffmpeg."""
        cmd = [
            self._ffmpeg_path,
            "-ss", str(timestamp),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",  # high quality
            "-y",  # overwrite output
            str(output_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=EXTRACTION_TIMEOUT_SECONDS
            )
            if proc.returncode != 0:
                stderr_text = stderr.decode(errors="replace")
                raise RuntimeError(f"ffmpeg exited with {proc.returncode}: {stderr_text[:200]}")
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Frame extraction timed out after {EXTRACTION_TIMEOUT_SECONDS}s"
            )

    # ── Result Publishing ──────────────────────────────────────────

    async def _publish_result(
        self, result: VideoFrameExtractionResult, original_event: dict[str, Any]
    ) -> None:
        """Publish the extraction result back to NATS."""
        if self._js is None:
            return

        now = datetime.now(timezone.utc)
        envelope = {
            "event_id": str(uuid.uuid4()),
            "event_type": EVENT_VIDEO_FRAME_COMPLETED,
            "event_version": 1,
            "occurred_at": now.isoformat(),
            "trace_id": original_event.get("trace_id", ""),
            "tenant_id": original_event.get("tenant_id", ""),
            "session_id": original_event.get("session_id", ""),
            "message_id": original_event.get("event_id", ""),
            "actor_id": "",
            "producer": {
                "service": "offline-knowledge-service",
                "instance": os.getenv("HOSTNAME", "local"),
                "region": None,
            },
            "routing": {
                "channel": "video",
                "partition_key": result.video_id,
                "priority": "normal",
            },
            "payload": {
                "video_id": result.video_id,
                "status": result.status,
                "frames": result.frames,
                "error_message": result.error_message,
                "ffmpeg_available": result.ffmpeg_available,
                "completed_at": result.completed_at,
            },
        }

        data = json.dumps(envelope).encode()
        try:
            await self._js.publish(VIDEO_FRAME_COMPLETED_SUBJECT, data)
            logger.info(
                "video_processor: published result for %s (status=%s, frames=%d)",
                result.video_id, result.status, len(result.frames),
            )
        except Exception as e:
            logger.error("video_processor: publish failed: %s", e, exc_info=True)

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _find_ffmpeg() -> str | None:
        """Locate the ffmpeg binary on the system PATH."""
        import shutil
        path = shutil.which("ffmpeg")
        if path:
            return path
        # Check common installation directories
        common_paths = [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/opt/ffmpeg/bin/ffmpeg",
        ]
        for p in common_paths:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
        return None


# ── Module-level singleton ─────────────────────────────────────────────

_video_processor: VideoProcessor | None = None


async def start_video_processor(nats_url: str) -> None:
    """Start the video processor NATS subscriber (called from main.py lifespan)."""
    global _video_processor
    _video_processor = VideoProcessor(nats_url)
    await _video_processor.start()


async def stop_video_processor() -> None:
    """Stop the video processor (called from main.py lifespan)."""
    global _video_processor
    if _video_processor:
        await _video_processor.stop()
        _video_processor = None


def get_video_processor() -> VideoProcessor | None:
    """Return the module-level video processor instance."""
    return _video_processor
