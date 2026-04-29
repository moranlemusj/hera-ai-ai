"""Shared frame-sampling helpers for the v1 vision nodes.

Both `critic` (per-shot rubric) and `coherence` (between-shot review) sample
frames from rendered mp4s and ship them to Gemini multimodal. Centralized
here so neither service has to import from the other's "private" namespace.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


async def ffprobe_duration(path: Path) -> float:
    """Return the duration of an mp4 in seconds. Falls back to 1.0 on error."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except (ValueError, AttributeError):
        return 1.0


async def sample_frame(video_path: Path, at_seconds: float, dest: Path) -> Path:
    """Extract a single PNG frame at the given timestamp."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-ss",
        f"{at_seconds:.2f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-loglevel",
        "error",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed sampling frame at {at_seconds}s: "
            f"{stderr.decode()[-200:]!r}"
        )
    return dest
