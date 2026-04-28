"""End-to-end test for the ffmpeg concat wrapper.

Generates two tiny color clips with ffmpeg, stitches them, asserts the output
exists and is non-empty. Skipped if ffmpeg isn't on PATH (it should be — the
conda env installs it from conda-forge).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from app.services.stitch import StitchError, stitch_concat


async def _make_color_clip(dest: Path, color: str = "blue") -> Path:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=320x180:d=1:r=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-loglevel",
        "error",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode())
    return dest


@pytest.fixture(autouse=True)
def _require_ffmpeg() -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")


async def test_stitch_concat_produces_combined_mp4(tmp_path: Path) -> None:
    a = await _make_color_clip(tmp_path / "a.mp4", "blue")
    b = await _make_color_clip(tmp_path / "b.mp4", "red")
    dest = tmp_path / "combined.mp4"

    result = await stitch_concat([a, b], dest)

    assert result == dest
    assert dest.exists()
    assert dest.stat().st_size > 0
    # Each input is ~1s; combined should be larger than either alone.
    assert dest.stat().st_size >= max(a.stat().st_size, b.stat().st_size) // 2


async def test_stitch_raises_on_empty_input(tmp_path: Path) -> None:
    with pytest.raises(StitchError):
        await stitch_concat([], tmp_path / "out.mp4")


async def test_stitch_raises_on_missing_input(tmp_path: Path) -> None:
    with pytest.raises(StitchError):
        await stitch_concat([tmp_path / "nope.mp4"], tmp_path / "out.mp4")
