"""ffmpeg concat wrapper.

We rely on the conda-forge ffmpeg in the active env. Concat demuxer requires
all inputs to share codec / dimensions / fps — Hera's `outputs` config
guarantees that when we use the same spec across shots.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class StitchError(Exception):
    """ffmpeg returned non-zero."""


async def stitch_concat(local_paths: list[Path], dest: Path) -> Path:
    if not local_paths:
        raise StitchError("stitch_concat called with no inputs")
    for p in local_paths:
        if not p.exists():
            raise StitchError(f"Input mp4 missing: {p}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    list_file = dest.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in local_paths) + "\n"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(dest),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            tail = (stderr or b"").decode("utf-8", errors="replace")[-500:]
            raise StitchError(f"ffmpeg exit {proc.returncode}: {tail}")
    finally:
        list_file.unlink(missing_ok=True)

    log.info("Stitched %d clips → %s", len(local_paths), dest)
    return dest
