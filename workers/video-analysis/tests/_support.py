"""Test scaffolding: real clips, real FFmpeg, real sidecars.

THERE ARE NO SKIPS IN THIS SUITE.

Every test here needs FFmpeg, because every line of this worker's production
path is "get pixels or samples out of a video". A suite that skips its way to
green when the decoder is missing would report that a video analyser works on a
machine that cannot decode video, and this repository has already found three
places where a skip shared an exit code with a pass. If FFmpeg is absent the
suite is RED, and the README says so as a prerequisite.

THE CLIPS ARE GENERATED, NOT COMMITTED, AND THEY ARE HOSTILE ON PURPOSE

    hard_cut        two visually unrelated scenes, spliced at a known frame
    flash           one white frame in a continuous scene
    whip_pan        fast camera motion over detailed content, no cut anywhere
    dissolve_short  a 0.4s cross fade
    dissolve_long   a 1.5s cross fade — the case the classical detector MISSES
    smooth_pan      a slow, even pan: the shake regression case
    tone_onset      silence, then a tone: the audio-onset case
    steady_tone     a constant tone at a known level, for the loudness check

Each is deterministic for a fixed FFmpeg build, is encoded once per test
session, and is small (a few seconds at 854x480).

THE SIDECAR

`golden_sidecar()` returns a frame index captured verbatim from a REAL run of
`workers/ingest`'s `generate_video_proxy` job on this machine (VideoToolbox,
FFmpeg 7.0). It is the only thing in this suite that pins the reader against
the writer rather than against another test helper.

`write_sidecar()` produces the same format for a generated clip so the rest of
the pipeline can be exercised without a Rust build. It mirrors ingest's writer
and is NOT a second implementation of it: nothing in production reads it, and
`test_proxy.py` checks the golden file to catch the two drifting apart.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = TESTS_ROOT.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
FIXTURES = TESTS_ROOT / "fixtures"

for _path in (PACKAGE_ROOT, REPO_ROOT / "packages" / "story-engine"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def ffmpeg() -> str:
    override = os.environ.get("MEMORY_ENGINE_FFMPEG")
    if override:
        return override
    found = shutil.which("ffmpeg")
    if found is None:
        raise RuntimeError(
            "FFmpeg is not installed. This worker decodes video for a living; a "
            "suite that skipped instead of failing here would report a working "
            "video analyser on a machine that cannot open a video."
        )
    return found


_CACHE: dict[str, Path] = {}
_TEMPORARY: list[tempfile.TemporaryDirectory] = []


def _workspace() -> Path:
    if not _TEMPORARY:
        _TEMPORARY.append(tempfile.TemporaryDirectory(prefix="video-analysis-tests-"))
    return Path(_TEMPORARY[0].name)


def _encode(name: str, arguments: list[str]) -> Path:
    cached = _CACHE.get(name)
    if cached is not None and cached.is_file():
        return cached
    destination = _workspace() / f"{name}.mp4"
    completed = subprocess.run(
        [ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
         *arguments, str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0 or not destination.is_file():
        raise RuntimeError(
            f"could not encode the {name} fixture: {completed.stderr.strip()[-800:]}"
        )
    _CACHE[name] = destination
    return destination


SIZE = "854x480"
RATE = 30


def hard_cut() -> Path:
    """Two unrelated scenes spliced at frame 60."""
    return _encode(
        "hard_cut",
        ["-f", "lavfi", "-i", f"testsrc2=size={SIZE}:rate={RATE}:duration=2",
         "-f", "lavfi", "-i", f"smptebars=size={SIZE}:rate={RATE}:duration=2",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
         "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-g", "30"],
    )


CUT_FRAME = 60


def flash() -> Path:
    """A continuous scene with one white frame at n=45."""
    return _encode(
        "flash",
        ["-f", "lavfi", "-i", f"testsrc2=size={SIZE}:rate={RATE}:duration=4",
         "-vf", "eq=brightness=1.0:enable='eq(n\\,45)'",
         "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-g", "30"],
    )


FLASH_FRAME = 45


def whip_pan() -> Path:
    """Fast camera motion over detailed content. There is no cut in this clip."""
    return _encode(
        "whip_pan",
        ["-f", "lavfi", "-i", "mandelbrot=size=1708x960:rate=30", "-t", "4",
         "-vf", "crop=854:480:'(iw-ow)*min(1\\,t/1.5)':'(ih-oh)*0.5',format=yuv420p",
         "-c:v", "libx264", "-crf", "20", "-g", "30"],
    )


def dissolve(seconds: float, name: str) -> Path:
    return _encode(
        name,
        ["-f", "lavfi", "-i", f"testsrc2=size={SIZE}:rate={RATE}:duration=3",
         "-f", "lavfi", "-i", f"smptebars=size={SIZE}:rate={RATE}:duration=3",
         "-filter_complex",
         f"[0:v][1:v]xfade=transition=fade:duration={seconds}:offset=1.5[v]",
         "-map", "[v]", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
         "-g", "30"],
    )


def dissolve_short() -> Path:
    return dissolve(0.4, "dissolve_short")


def dissolve_long() -> Path:
    return dissolve(1.5, "dissolve_long")


def smooth_pan() -> Path:
    """A slow even pan with sub-pixel per-frame motion. The shake regression."""
    return _encode(
        "smooth_pan",
        ["-f", "lavfi", "-i", "mandelbrot=size=1708x960:rate=30", "-t", "4",
         "-vf", "crop=854:480:'(iw-ow)*(0.2+0.1*t/4)':'(ih-oh)*0.5',format=yuv420p",
         "-c:v", "libx264", "-crf", "18", "-g", "30"],
    )


def tone_onset() -> Path:
    """1.5s of digital silence, then a 1 kHz tone. Picture is static.

    Built by muting a continuous sine for the first half rather than by
    concatenating two sources: a concat of a silence source and a tone source
    silently produced a clip that was quiet throughout, and the test that
    caught it was the one asserting the onset lands at 1.5s.
    """
    return _encode(
        "tone_onset",
        ["-f", "lavfi", "-i", f"color=c=gray:size={SIZE}:rate={RATE}:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=3",
         "-filter_complex", "[1:a]volume=0:enable='lt(t\\,1.5)'[a]",
         "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", "-shortest", "-f", "mov"],
    )


ONSET_SECOND = 1.5


def steady_tone() -> Path:
    """A constant 1 kHz sine at full scale, for the loudness cross-check."""
    return _encode(
        "steady_tone",
        ["-f", "lavfi", "-i", f"color=c=gray:size={SIZE}:rate={RATE}:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=3",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", "-shortest", "-f", "mov"],
    )


AUDIO_LEAD_S = 0.5


def offset_audio() -> Path:
    """The onset clip with its AUDIO STREAM STARTING 0.5s BEFORE the picture.

    Audio and video in one container do not have to start at the same instant.
    `-itsoffset` on the video input pushes the picture later, so the audio
    stream's first sample is `AUDIO_LEAD_S` earlier than the first frame. A
    producer that ignores the two start times places every loudness window half
    a second out and reports an onset that is nowhere near the sound.
    """
    return _encode(
        "offset_audio",
        ["-f", "lavfi", "-i", f"color=c=gray:size={SIZE}:rate={RATE}:duration=3",
         "-itsoffset", str(AUDIO_LEAD_S),
         "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=3",
         "-filter_complex", "[1:a]volume=0:enable='lt(t\\,1.5)'[a]",
         "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p",
         "-c:a", "pcm_s16le", "-copyts", "-f", "mov"],
    )


def silent_video() -> Path:
    """Picture with no audio stream at all — the demo library ships one too."""
    return _encode(
        "silent_video",
        ["-f", "lavfi", "-i", f"testsrc2=size={SIZE}:rate={RATE}:duration=2",
         "-an", "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p"],
    )


def black_clip() -> Path:
    """Pure black. Every frame must trip the black-frame elimination gate."""
    return _encode(
        "black_clip",
        ["-f", "lavfi", "-i", f"color=c=black:size={SIZE}:rate={RATE}:duration=2",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"],
    )


def probe_frames(path: Path) -> list[tuple[int, int, float]]:
    """(frame, pts, pts_time) for every frame, from FFmpeg's own showinfo.

    The same source ingest's sidecar writer parses, read the same way, so a
    sidecar written from this is in the same units as a real one.
    """
    completed = subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "info",
         "-i", str(path), "-map", "0:v:0", "-vf", "showinfo",
         "-fps_mode", "passthrough", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    rows: list[tuple[int, int, float]] = []
    for line in completed.stderr.splitlines():
        if "showinfo" not in line or " pts:" not in line or " pts_time:" not in line:
            continue
        try:
            number = int(line.split(" n:")[1].split()[0])
            pts = int(line.split(" pts:")[1].split()[0])
            seconds = float(line.split(" pts_time:")[1].split()[0])
        except (IndexError, ValueError):
            continue
        rows.append((number, pts, seconds))
    if not rows:
        raise RuntimeError(f"showinfo produced no frames for {path.name}")
    return rows


def time_base(path: Path) -> tuple[int, int]:
    completed = subprocess.run(
        [os.environ.get("MEMORY_ENGINE_FFPROBE") or shutil.which("ffprobe") or "ffprobe",
         "-hide_banner", "-loglevel", "error", "-select_streams", "v:0",
         "-show_entries", "stream=time_base", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    parsed = json.loads(completed.stdout or "{}")
    text = (parsed.get("streams") or [{}])[0].get("time_base") or "1/1000"
    numerator, denominator = text.split("/")
    return int(numerator), int(denominator)


def write_sidecar(clip: Path, destination: Path) -> Path:
    """Write an ingest-format frame index for a generated clip.

    Mirrors `workers/ingest/src/video.rs::write_sidecar`. Test scaffolding
    only: production never writes one of these, and `test_proxy.py` reads a
    REAL one captured from the Rust worker so that the two cannot drift
    unnoticed.
    """
    rows = probe_frames(clip)
    numerator, denominator = time_base(clip)
    deltas = {rows[i + 1][1] - rows[i][1] for i in range(len(rows) - 1)}
    delta = next(iter(deltas)) if len(deltas) == 1 else None
    rate = (denominator / numerator) / delta if delta else None
    header = {
        "schema": "memory-engine-frame-index",
        "version": 1,
        "mapping": "identity" if len(deltas) <= 1 else "table",
        "entry_count": len(rows),
        "source_rate": rate,
        "proxy_rate": rate,
        "source_time_base_numerator": numerator,
        "source_time_base_denominator": denominator,
    }
    lines = [json.dumps(header, separators=(",", ":"))]
    lines += [
        json.dumps(
            {"proxy_frame": number, "source_pts": pts, "source_time_seconds": seconds},
            separators=(",", ":"),
        )
        for number, pts, seconds in rows
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


MEDIA_ID = "a" * 64


def make_proxy(clip: Path, *, media_id: str = MEDIA_ID):
    """A `proxy.Proxy` over a generated clip, with its sidecar written."""
    from memory_engine_video_analysis.proxy import Proxy, read_frame_index

    sidecar = write_sidecar(clip, clip.with_suffix(".idx"))
    return Proxy(
        media_id=media_id,
        path=clip,
        frame_index=read_frame_index(sidecar),
        proxy_id="b" * 64,
        generator_version="tests",
    )
