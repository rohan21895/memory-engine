"""The FeatureStream assembler: everything above, in the shape `plan_moments` eats.

THE ONE THING THIS FILE MUST NOT GET WRONG

`FeatureStream` states that sample i sits at source time
`{value: start_value + i, rate: rate}`. Every moment's source timecode, every
snap point, every word boundary and the whole 50 ms beat-alignment gate rest on
that sentence being true. So the mapping is derived, not assumed, and checked
three ways before a stream is built:

  1. the frame index sidecar must describe a UNIFORM source grid — uniform PTS
     deltas, an integral start — which is where `rate` and `start_value` come
     from (`proxy.SourceGrid`);
  2. the number of frames the decoder actually produced must EQUAL the
     sidecar's entry count. If FFmpeg drops or duplicates one frame, every
     feature after it is attributed to the wrong source frame and no later
     check could notice;
  3. the sidecar's proxy rate must equal its source rate, because
     `_proxy_range` maps between the two grids by scaling, and a temporally
     resampled proxy would need the mapping table rather than a ratio.

Failing any of the three raises. None of them produces a degraded stream,
because a stream that is silently off by one frame is worse than no stream:
it renders, it looks like a reel, and the only way to notice is to watch the
output against the source.

WHAT ENDS UP IN A Frame, AND WHAT STAYS None

    luma, clipped_highlights, clipped_shadows,      visual.py
    sharpness, exposure_stability, motion,
    shake, novelty
    loudness_lufs                                   audio.py (absent when the
                                                    proxy has no audio track)
    face_presence, max_face_area_ratio,             None — needs the model host
    smile_intensity
    speech, noise                                   None — needs a VAD / CLAP
    lens_obstructed                                 False — derived by
                                                    `eliminate_frames` from
                                                    luma and sharpness instead

`AnalysisReport` carries what the Frame has nowhere to put: which detector ran,
what the model gate said about the one that did not, whether a transcript
exists, and the elimination-relevant distributions. A caller that prints it is
telling the truth about a run; a caller that ignores it still cannot be misled,
because every absent signal is absent in the stream too.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import audio as audio_module
from . import decode, shots as shots_module, visual as visual_module
from .proxy import Proxy
from .transcript import NullTranscriptBackend, TranscriptBackend, TranscriptResult

__all__ = [
    "AnalysisReport",
    "StreamAssemblyError",
    "analyse_proxy",
    "model_runs_for",
]

ANALYSER_ID = "video-analysis"
ANALYSER_VERSION = "0.1.1"

# The Slug every Score in the emitted records points at. It names the SCORER,
# because that is what produced the numbers in `scores`; the feature producers
# appear in `model_runs` alongside it, so the provenance of the inputs is
# recorded without pretending they computed the output.
SCORER_RUN_ID = "moment-fusion-linear-1-0-0"


class StreamAssemblyError(RuntimeError):
    """The proxy cannot be turned into a feature stream that can be trusted."""


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Everything about the run that the stream itself cannot carry."""

    media_id: str
    proxy_id: str
    proxy_path: str
    frame_count: int
    rate: float
    start_value: int
    analysis_size: tuple[int, int]
    shot_detector: str
    shot_detector_version: str
    shot_count: int
    cut_frames: tuple[int, ...]
    suppressed_flashes: tuple[int, ...]
    transnetv2_seam: str
    audio_available: bool
    audio_reason: str
    audio_window_seconds: float
    silence_runs: tuple[tuple[int, int], ...]
    loudness_rises: int
    transcript_available: bool
    transcript_reason: str
    not_measured: tuple[str, ...]
    elapsed_s: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def lines(self) -> list[str]:
        """A block a runner can print verbatim. Absences first."""
        out = [
            f"media {self.media_id[:12]}  proxy {self.proxy_id[:12]}",
            f"  {self.frame_count} frames at {self.rate} fps from source frame "
            f"{self.start_value}, analysed at {self.analysis_size[0]}x{self.analysis_size[1]}",
            f"  shots: {self.shot_count} via {self.shot_detector} "
            f"{self.shot_detector_version}; cuts at {list(self.cut_frames)}"
            + (f"; flashes rejected at {list(self.suppressed_flashes)}"
               if self.suppressed_flashes else ""),
            f"  learned detector: {self.transnetv2_seam}",
        ]
        if self.audio_available:
            out.append(
                f"  audio: loudness over {self.audio_window_seconds * 1000:.0f}ms "
                f"windows, {len(self.silence_runs)} silence run(s), "
                f"{self.loudness_rises} onset-sized rise(s)"
            )
        else:
            out.append(f"  audio: NOT MEASURED ({self.audio_reason})")
        out.append(
            f"  transcript: {'available' if self.transcript_available else 'NONE'}"
            f" ({self.transcript_reason})"
        )
        out.append(f"  not measured: {', '.join(self.not_measured)}")
        out.extend(f"  note: {note}" for note in self.notes)
        out.append(f"  took {self.elapsed_s:.2f}s")
        return out


# Signals nothing in this worker produces, named once so the report and the
# README cannot drift from the code.
NOT_MEASURED = (
    "face_presence",
    "max_face_area_ratio",
    "smile_intensity",
    "speech",
    "noise",
    "audio_events",
    "transcript",
)


def analyse_proxy(
    proxy: Proxy,
    *,
    transcript_backend: TranscriptBackend | None = None,
    shot_backend: shots_module.ShotBackend | None = None,
    language: str | None = None,
    consult_seam: bool = True,
) -> tuple[Any, AnalysisReport]:
    """Decode one proxy and assemble its FeatureStream.

    Returns `(FeatureStream, AnalysisReport)`. The stream is validated by
    `moments.FeatureStream.validate()` before it is handed back, so a stream
    that would be rejected by `plan_moments` is rejected HERE, next to the
    producer that made it, rather than three modules downstream.
    """
    from memory_engine_story.moments import FeatureStream, Frame  # noqa: PLC0415

    started = time.monotonic()
    # Content identity is checked before any decoder sees the path. Otherwise
    # replaced bytes can produce new features and ModelRuns under an old
    # `input_proxy_id`, defeating both provenance and resumable job identity.
    proxy.verify_content()
    grid = proxy.proxy_grid
    index = proxy.frame_index

    declared_proxy_rate = index.declared_proxy_rate
    if declared_proxy_rate is not None and abs(
        declared_proxy_rate - grid.rate_float
    ) > 1e-9 * max(1.0, grid.rate_float):
        raise StreamAssemblyError(
            f"{index.path.name} declares proxy_rate={declared_proxy_rate} against a "
            f"source rate of {grid.rate_float}. This worker maps proxy frame i to "
            "source frame start+i, which only holds while the proxy is a spatial "
            "downscale; a temporally resampled proxy needs the mapping table and "
            "is refused rather than approximated."
        )

    probe = decode.probe(proxy.path)
    size = decode.analysis_size(probe.width, probe.height)
    measured = visual_module.analyse_frames(
        decode.iter_frames(proxy.path, size), size=size, rate=grid.rate_float
    )
    if len(measured.frames) != grid.frame_count:
        raise StreamAssemblyError(
            f"{proxy.path.name} decoded to {len(measured.frames)} frames but its "
            f"frame index holds {grid.frame_count}. Sample i would no longer be "
            "source frame start+i, so every moment this stream produced would "
            "address the wrong timecode — by a whole frame at the point of the "
            "discrepancy and by more after it."
        )

    sound = audio_module.analyse_audio(
        str(proxy.path),
        frame_count=grid.frame_count,
        rate=grid.rate_float,
        video_start_s=probe.video_start_s,
    )
    detection = shots_module.detect_shots(
        measured.signatures,
        rate=grid.rate_float,
        proxy_path=str(proxy.path),
        backend=shot_backend,
        seam=shots_module.transnetv2_seam() if consult_seam else None,
    )
    spoken: TranscriptResult = (transcript_backend or NullTranscriptBackend()).transcribe(
        str(proxy.path), language=language
    )

    frames = tuple(
        Frame(
            luma=frame.luma,
            clipped_highlights=frame.clipped_highlights,
            clipped_shadows=frame.clipped_shadows,
            sharpness=frame.sharpness,
            exposure_stability=frame.exposure_stability,
            motion=frame.motion,
            shake=frame.shake,
            novelty=frame.novelty,
            loudness_lufs=sound.loudness_lufs[position] if sound.available else None,
            # Everything below needs a model this worker does not run. None is
            # NOT MEASURED and renormalises out of the fusion; a zero would be
            # a measurement that nobody took.
            face_presence=None,
            max_face_area_ratio=None,
            smile_intensity=None,
            speech=None,
            noise=None,
            lens_obstructed=False,
        )
        for position, frame in enumerate(measured.frames)
    )

    stream = FeatureStream(
        media_id=proxy.media_id,
        rate=grid.rate_float,
        frames=frames,
        start_value=float(grid.start_value),
        shots=detection.shots,
        words=spoken.to_words(rate=grid.rate_float, start_value=float(grid.start_value)),
        # Only ever set when a transcript exists: `_transcript` emits a
        # TranscriptSegment only when the language is known, and a default of
        # "en" would label a Hindi moment English in the database forever.
        language=spoken.language if spoken.available else None,
        audio_events=(),
        proxy_rate=grid.rate_float,
        proxy_start_value=0.0,
    )
    stream.validate()

    from memory_engine_story.moments import Policy  # noqa: PLC0415

    report = AnalysisReport(
        media_id=proxy.media_id,
        proxy_id=proxy.proxy_id,
        proxy_path=str(proxy.path),
        frame_count=grid.frame_count,
        rate=grid.rate_float,
        start_value=grid.start_value,
        analysis_size=size,
        shot_detector=detection.detector_id,
        shot_detector_version=detection.detector_version,
        shot_count=len(detection.shots),
        cut_frames=detection.cut_frames,
        suppressed_flashes=detection.suppressed_flashes,
        transnetv2_seam=detection.seam.describe(),
        audio_available=sound.available,
        audio_reason=sound.reason,
        audio_window_seconds=sound.window_seconds,
        silence_runs=sound.silence_runs(grid.rate_float) if sound.available else (),
        loudness_rises=(
            audio_module.count_loudness_rises(
                sound.loudness_lufs, Policy().audio_onset_db
            )
            if sound.available
            else 0
        ),
        transcript_available=spoken.available,
        transcript_reason=spoken.reason,
        not_measured=NOT_MEASURED,
        elapsed_s=time.monotonic() - started,
        notes=sound.notes,
    )
    return stream, report


def model_runs_for(
    proxy: Proxy, report: AnalysisReport, *, ran_at: str | None
) -> tuple[dict[str, Any], ...]:
    """`ModelRun` entries for the producers behind one stream.

    Empty when `ran_at` is None. `ModelRun.ran_at` is REQUIRED and is a real
    wall-clock instant; inventing one to satisfy the schema would put a
    fabricated timestamp in the provenance record of every moment, which is
    exactly the field a later audit would trust. A caller that wants provenance
    passes the time it actually ran.

    `weights_blake3` and `config_blake3` are null throughout because none of
    these producers has weights or a model config — they are classical
    executors, which is the case `ModelRun.input_proxy_id` already anticipates.
    A null weights hash is what makes a record non-reproducible in general;
    here the executor VERSION is the whole of the behaviour, so the pin that
    matters is the version string, and it changes whenever a constant does.
    """
    if ran_at is None:
        return ()
    producers = [
        (visual_module.EXECUTOR_ID, visual_module.EXECUTOR_VERSION),
        (audio_module.EXECUTOR_ID, audio_module.EXECUTOR_VERSION),
        (report.shot_detector, report.shot_detector_version),
        ("moment-fusion-linear", "1.0.0"),
    ]
    runs: list[dict[str, Any]] = []
    for model_id, version in producers:
        runs.append(
            {
                "run_id": f"{model_id}-{version.replace('.', '-')}",
                "model": {
                    "model_id": model_id,
                    "version": version,
                    "weights_blake3": None,
                    "runtime": None,
                    "precision": None,
                    "config_blake3": None,
                },
                "ran_at": ran_at,
                "input_proxy_id": proxy.proxy_id or None,
                "duration_ms": None,
                "job_id": None,
            }
        )
    return tuple(runs)


def analysis_paths(workdir: Path) -> Path:
    """Where a runner writes this worker's output, mirroring the records tree."""
    return workdir / "moments"
