"""Getting pixels and samples out of a proxy, deterministically.

Everything in this worker reads the 480p proxy through this module, and this
module shells out to FFmpeg. There is no OpenCV here on purpose: the system
OpenCV on at least one machine in this project is broken, `classical.py` in
`services/pipeline` already refused to depend on it for the same reason, and a
quality measure that cannot run is worse than one that is slightly slower.

THREE RULES THIS FILE ENFORCES

1. ONE DECODE PASS FOR THE PICTURE. Every visual signal — photometry,
   sharpness, motion, shake, novelty, shot distance — is computed from the same
   stream of frames in one pass. Two passes would be cheaper to write and would
   introduce the failure this project keeps finding: pass A yields 180 frames,
   pass B yields 179, every feature after the dropped frame is attributed to
   its neighbour, and nothing raises. The frames are yielded one at a time, so
   memory is one frame, not one clip.

2. A SHORT READ IS AN ERROR. FFmpeg exiting non-zero, or the pipe ending on a
   partial frame, raises. It does not return the frames it managed to get. A
   truncated decode that returns silently is a video scored on its first two
   seconds and reported as if it were scored on all of it.

3. NOTHING IS RESAMPLED IN TIME. No `-r`, no `-fps_mode` beyond the default, no
   frame dropping or duplication. The caller asserts the decoded frame count
   against the frame-index sidecar, and that assertion is only meaningful if
   the decoder was not allowed to invent or discard frames.

WHY stderr GOES TO A FILE

Reading a large stdout while stderr fills its own pipe buffer deadlocks. A
temporary file has no buffer to fill, and the whole log is available when the
process fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

__all__ = [
    "AudioTrack",
    "DecodeError",
    "MediaProbe",
    "ToolMissing",
    "analysis_size",
    "ffmpeg_path",
    "ffprobe_path",
    "iter_frames",
    "probe",
    "read_audio",
]

# The analysis raster. The long edge is capped at 512px, NEVER upscaled, for
# one reason: `services/pipeline/memory_engine_pipeline/classical.py` calibrates
# its Laplacian-variance sharpness constant against a 512px long edge and
# refuses any other proxy size, because Laplacian variance is
# resolution-dependent and a library measured at a mix of sizes cannot be
# ranked. Video frames are measured on the same raster so that a frame of video
# and a still are judged on the same scale.
ANALYSIS_MAX_EDGE = 512

# The resampler is pinned, not defaulted. FFmpeg's default scaler has changed
# between releases; a sharpness number that depends on which FFmpeg was
# installed is not a measurement.
SCALE_FLAGS = "bicubic"

# Audio is decoded at this rate because the BS.1770-4 K-weighting coefficients
# in `audio.py` are the published 48 kHz reference values. Resampling costs a
# little accuracy; using coefficients specified for a different rate costs much
# more, and silently.
AUDIO_SAMPLE_RATE = 48000


class ToolMissing(RuntimeError):
    """FFmpeg or FFprobe is not installed, or not where we were told to look."""


class DecodeError(RuntimeError):
    """The proxy could not be decoded. Not an empty result -- an absent one."""


def _tool(env_name: str, binary: str) -> str:
    override = os.environ.get(env_name)
    if override:
        if not Path(override).is_file():
            raise ToolMissing(f"{env_name}={override!r} is not a file")
        return override
    found = shutil.which(binary)
    if found is None:
        raise ToolMissing(
            f"{binary} is not on PATH. Set {env_name} to its absolute path. "
            "This worker cannot decode a proxy without it, and will not guess."
        )
    return found


def ffmpeg_path() -> str:
    return _tool("MEMORY_ENGINE_FFMPEG", "ffmpeg")


def ffprobe_path() -> str:
    return _tool("MEMORY_ENGINE_FFPROBE", "ffprobe")


@dataclass(frozen=True, slots=True)
class AudioTrack:
    sample_rate: int
    channels: int
    layout: str


@dataclass(frozen=True, slots=True)
class MediaProbe:
    """What FFprobe says about a proxy, in the units the rest of the worker uses.

    `rate` is an exact Fraction, taken from the container's declared frame rate
    (`r_frame_rate`). 30000/1001 has no float form, and `beats.py` in the story
    engine makes the same choice for the same reason: a rate rounded to 29.97
    drifts every derived timecode.
    """

    width: int
    height: int
    rate: Fraction
    nb_frames: int | None
    duration_s: float | None
    audio: AudioTrack | None
    video_start_s: float = 0.0
    audio_start_s: float | None = None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None


def _run(command: list[str], *, stdout_to: int | None = None) -> subprocess.CompletedProcess:
    with tempfile.TemporaryFile() as errors:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE if stdout_to is None else stdout_to,
            stderr=errors,
            check=False,
        )
        errors.seek(0)
        log = errors.read().decode("utf-8", "replace")
    if completed.returncode != 0:
        raise DecodeError(
            f"{Path(command[0]).name} exited {completed.returncode}: {log.strip()[-2000:]}"
        )
    return completed


def _fraction(text: str | None, *, what: str) -> Fraction | None:
    if not text or text in {"0/0", "N/A"}:
        return None
    try:
        value = Fraction(text)
    except (ValueError, ZeroDivisionError) as error:
        raise DecodeError(f"{what}={text!r} is not a rational number") from error
    return value if value > 0 else None


def probe(path: str | Path) -> MediaProbe:
    """Stream properties of one proxy.

    Raises when there is no video stream, when the frame rate is missing, or
    when the geometry is degenerate. Every one of those would otherwise become
    a plausible-looking feature stream over the wrong time base.
    """
    target = Path(path)
    if not target.is_file():
        raise DecodeError(f"proxy file is missing: {target}")
    completed = _run(
        [
            ffprobe_path(), "-hide_banner", "-loglevel", "error",
            "-show_streams", "-show_format", "-of", "json", str(target),
        ]
    )
    try:
        parsed = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DecodeError("ffprobe did not return JSON") from error

    streams = parsed.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise DecodeError(f"{target.name} has no video stream")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise DecodeError(f"{target.name} declares a degenerate size {width}x{height}")

    rate = _fraction(video.get("r_frame_rate"), what="r_frame_rate")
    if rate is None:
        rate = _fraction(video.get("avg_frame_rate"), what="avg_frame_rate")
    if rate is None:
        raise DecodeError(
            f"{target.name} declares no usable frame rate. Every time this "
            "worker emits is a frame index at that rate, so guessing one would "
            "put every moment at the wrong point in the source."
        )

    nb_frames = video.get("nb_frames")
    try:
        frames = int(nb_frames) if nb_frames not in (None, "N/A") else None
    except (TypeError, ValueError):
        frames = None

    duration = (parsed.get("format") or {}).get("duration")
    try:
        duration_s = float(duration) if duration not in (None, "N/A") else None
    except (TypeError, ValueError):
        duration_s = None

    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    audio = None
    if audio_stream is not None:
        try:
            channels = int(audio_stream.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0
        if channels > 0:
            audio = AudioTrack(
                sample_rate=int(audio_stream.get("sample_rate") or 0) or AUDIO_SAMPLE_RATE,
                channels=channels,
                layout=str(audio_stream.get("channel_layout") or "unknown"),
            )

    return MediaProbe(
        width=width,
        height=height,
        rate=rate,
        nb_frames=frames,
        duration_s=duration_s,
        audio=audio,
        video_start_s=_start_time(video),
        audio_start_s=None if audio_stream is None else _start_time(audio_stream),
    )


def _start_time(stream: dict) -> float:
    """A stream's first presentation time, in seconds.

    Carried because audio and video in one container do not have to start at
    the same instant, and `audio.py` places every loudness window by video
    frame index. A container with a 40 ms audio lead, analysed as if both
    started at zero, puts every audio onset one frame early — which is one
    frame of a cut landing before the sound that justified it.
    """
    value = stream.get("start_time")
    try:
        return 0.0 if value in (None, "N/A") else float(value)
    except (TypeError, ValueError):
        return 0.0


def analysis_size(width: int, height: int, *, max_edge: int = ANALYSIS_MAX_EDGE) -> tuple[int, int]:
    """Proxy geometry -> analysis raster geometry.

    Never upscales: a 270x480 vertical proxy is measured at 270x480, because
    inventing pixels would inflate every texture measure taken from it.

    Rounding is explicit half-up. Python's round() is half-to-even, so a
    854x480 proxy would round its 287.75 to 288 while a hypothetical 287.5
    would round to 288 and 288.5 to 288 — a raster whose size depends on the
    parity of the number next to it.
    """
    if width <= 0 or height <= 0:
        raise DecodeError(f"cannot size an analysis raster for {width}x{height}")
    longest = max(width, height)
    if longest <= max_edge:
        return width, height
    scale = max_edge / longest
    return (
        max(1, int(width * scale + 0.5)),
        max(1, int(height * scale + 0.5)),
    )


def iter_frames(path: str | Path, size: tuple[int, int]) -> Iterator[bytes]:
    """Yield raw RGB24 frames of exactly `size`, in decode order.

    Yields bytes rather than arrays so this module carries no numpy dependency
    and the caller decides the dtype. Every yielded buffer is exactly
    width*height*3 bytes; a short final read raises.
    """
    width, height = size
    if width <= 0 or height <= 0:
        raise DecodeError(f"cannot decode into a {width}x{height} raster")
    frame_bytes = width * height * 3
    command = [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(path),
        "-map", "0:v:0",
        "-vf", f"scale={width}:{height}:flags={SCALE_FLAGS}",
        "-pix_fmt", "rgb24",
        "-f", "rawvideo",
        "-",
    ]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            command, stdout=subprocess.PIPE, stderr=errors
        )
        assert process.stdout is not None
        drained = False
        try:
            while True:
                buffer = _read_exactly(process.stdout, frame_bytes)
                if buffer is None:
                    drained = True
                    break
                yield buffer
        finally:
            process.stdout.close()
            if drained:
                # The pipe reached a clean end, which means FFmpeg closed its
                # stdout, which means it is finishing. WAIT for it and let its
                # exit status be checked. Terminating here instead — which an
                # earlier version did, because poll() is still None for the
                # moment between the last write and the exit — replaced every
                # real failure with SIGTERM and made a truncated source
                # indistinguishable from a complete one. A container that
                # yields 400 whole frames and then errors is exactly the case:
                # every frame read cleanly, and the video was half decoded.
                process.wait()
            else:
                # The consumer stopped early. FFmpeg must not be left writing
                # into a pipe nobody drains, and its exit status is not
                # meaningful because we are the ones who ended it.
                if process.poll() is None:
                    process.terminate()
                process.wait()
        errors.seek(0)
        log = errors.read().decode("utf-8", "replace")
    if drained and process.returncode != 0:
        raise DecodeError(
            f"ffmpeg exited {process.returncode} after producing whole frames; "
            "the decode is incomplete and the frames it did produce describe "
            f"only part of the video: {log.strip()[-2000:]}"
        )


def _read_exactly(stream, count: int) -> bytes | None:
    """`count` bytes, None at a clean end of stream, DecodeError on a short read."""
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if remaining == count:
        return None
    if remaining > 0:
        raise DecodeError(
            f"the decoder stopped {remaining} bytes into a frame. A partial "
            "frame means the proxy is truncated; scoring the frames that did "
            "arrive would report a full analysis of a fraction of the video."
        )
    return b"".join(chunks)


def read_audio(
    path: str | Path,
    *,
    sample_rate: int = AUDIO_SAMPLE_RATE,
    filters: str | None = None,
) -> bytes | None:
    """All audio samples as interleaved little-endian float32, or None.

    None means THERE IS NO AUDIO STREAM, which is a real state — the demo
    library ships a silent clip precisely because the ambient-music path has to
    cope with one — and is not the same as a stream of zeroes.

    The channel layout is preserved (no downmix): BS.1770 loudness sums channel
    powers, so folding stereo to mono before measuring changes the answer.

    `filters` is an FFmpeg audio filtergraph applied before the samples are
    handed back. `audio.py` uses it to run the K-weighting biquads in C; a
    filter chain that itself resamples must do so as its FIRST link, because
    `-ar` here applies at the output, after the graph.
    """
    if probe(path).audio is None:
        return None
    command = [
        ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(path),
        "-map", "0:a:0",
        "-vn",
    ]
    if filters:
        command += ["-af", filters]
    command += [
        "-acodec", "pcm_f32le",
        "-ar", str(sample_rate),
        "-f", "f32le",
        "-",
    ]
    return _run(command).stdout
