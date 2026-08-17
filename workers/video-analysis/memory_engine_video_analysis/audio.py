"""Per-frame loudness, the RMS envelope, and measured silence.

WHAT `moments.py` DOES WITH THIS

`_level_snaps` detects an AUDIO ONSET as a frame-to-frame RISE in
`Frame.loudness_lufs` of at least `Policy.audio_onset_db` (6.0 by default),
non-maximum-suppressed. So this module does not detect onsets: it produces the
loudness series they are detected from, on the video frame grid, and the single
threshold lives in the policy where the planner can see it.

That has one consequence worth stating loudly, because getting it wrong would
have produced a stream on which NO audio onset could ever fire:

    THIS IS NOT THE 400 ms MOMENTARY LOUDNESS OF BS.1770.

The unit is the same (LUFS: K-weighting, mean square, the -0.691 offset). The
integration time is not: the window here is ONE FRAME, centred on the frame,
floored at `MIN_WINDOW_SECONDS`.

Measured, on a 1 kHz tone stepping up by a fixed amount at 30 fps — the
largest frame-to-frame rise the planner would see:

    step      33 ms window (one frame)     400 ms (BS.1770 momentary)
    12 LU     12.0 LU                       2.5 LU
    20 LU     20.0 LU                       7.1 LU
    30 LU     29.7 LU                      16.3 LU

A 12 LU step is a voice starting over room tone. At one frame the rise equals
the step and the planner's 6 LU rule fires exactly where the sound starts; at
400 ms the same event produces 2.5 LU and NO ONSET IS EVER DETECTED. The
failure is invisible — the loudness series looks entirely reasonable, the
planner simply never snaps to sound and every cut lands on a visual boundary
instead.

`momentary_loudness()` is exposed with the by-the-book 400 ms window so the
same arithmetic can be cross-checked against FFmpeg's `ebur128` in
`tests/test_audio.py`, which is how the filter is verified against a reference
implementation rather than against itself.

WHY FFmpeg APPLIES THE FILTER

K-weighting is two biquads, and a biquad is a sequential recurrence: numpy
cannot vectorise it, and a Python loop over 48 kHz samples is minutes per hour
of footage. FFmpeg's `biquad` filter takes raw coefficients, so the EXACT
BS.1770-4 48 kHz coefficients below are handed to it and the recursion runs in
C. The audio is resampled to 48 kHz first, because those coefficients are
specified for 48 kHz and using them at another rate would move the filter's
corner frequencies without any error being raised.

SILENCE IS NOT A SPEECH GAP
Silence is measured here: a run of frames whose unweighted RMS sits at or below
a floor. A speech gap is an absence of WORDS, which comes from the transcript
and nowhere else — a passage can be entirely free of speech and far from
silent, and cutting on "the music got quiet" while calling it a speech gap is
how a cut lands mid-sentence under a music bed. `moments.py` keeps them apart
too: `_speech_snaps` reads `stream.words` and never touches the audio level.

NOT MEASURED HERE
`speech` and `noise` (the wind/handling term) stay None on every frame. Both
need a classifier — a VAD or the CLAP event head — running in the model host,
and no weights for either are wired. A spectral-flatness heuristic dressed up
as `speech_presence` would feed the score fusion, the dead-time rescue for
locked-off interviews and the L-cut decision with a number nobody calibrated.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AudioAnalysis",
    "AudioError",
    "EXECUTOR_ID",
    "EXECUTOR_VERSION",
    "K_WEIGHTING_FILTER",
    "LOUDNESS_FLOOR_LUFS",
    "analyse_audio",
    "count_loudness_rises",
    "loudness_series",
    "silence_runs",
]

EXECUTOR_ID = "video-audio-features"
EXECUTOR_VERSION = "1.0.0"

# ITU-R BS.1770-4 K-weighting at 48 kHz: a high-shelf "head" filter followed by
# the RLB high-pass. Transcribed from the recommendation's own table.
PRE_FILTER_B = (1.53512485958697, -2.69169618940638, 1.19839281085285)
PRE_FILTER_A = (1.0, -1.69065929318241, 0.73248077421585)
RLB_FILTER_B = (1.0, -2.0, 1.0)
RLB_FILTER_A = (1.0, -1.99004745483398, 0.99007225036621)

# The FFmpeg filter chain that applies exactly the above. `aresample` comes
# first: the coefficients are 48 kHz coefficients.
K_WEIGHTING_FILTER = (
    "aresample=48000,"
    "biquad=b0={:.14f}:b1={:.14f}:b2={:.14f}:a0=1:a1={:.14f}:a2={:.14f},"
    "biquad=b0={:.14f}:b1={:.14f}:b2={:.14f}:a0=1:a1={:.14f}:a2={:.14f}"
).format(
    *PRE_FILTER_B, *PRE_FILTER_A[1:], *RLB_FILTER_B, *RLB_FILTER_A[1:]
)

# The -0.691 dB offset BS.1770 applies so that a reference signal reads its
# nominal level.
LOUDNESS_OFFSET_DB = -0.691

# MomentRecord.features.audio.loudness_lufs is bounded [-70, 0], and -70 LUFS
# is also BS.1770's absolute gate. Anything at or below it is reported as the
# floor: "at or under the measurement floor", not "exactly this quiet".
LOUDNESS_FLOOR_LUFS = -70.0
LOUDNESS_CEILING_LUFS = 0.0

# Floor under the one-frame window. NOT a filter-settling allowance — the
# K-weighting is applied to the whole signal before any window is taken, so the
# filter has long since settled. The floor is about the mean square being a
# stable statistic: a window shorter than one cycle of the lowest frequency
# that survives the RLB high-pass measures where in the cycle it happened to
# land rather than how loud the sound is. 25 ms is one cycle at 40 Hz, just
# above that corner.
#
# It is deliberately BELOW a frame at every common rate (33 ms at 30 fps, 42 ms
# at 24 fps), so those windows are exactly one frame and a step in level
# arrives as one step in the series. At 120 fps the frame is 8 ms and this
# floor takes over, which costs some onset sharpness on high-speed footage and
# is the correct trade against measuring phase.
MIN_WINDOW_SECONDS = 0.025

# Unweighted RMS, in dBFS, at or below which a frame counts as silent, and the
# shortest run that is reported as silence rather than as a gap between words.
SILENCE_FLOOR_DBFS = -60.0
MIN_SILENCE_SECONDS = 0.25


class AudioError(ValueError):
    """The audio cannot be measured. Not silence -- an absent measurement."""


@dataclass(frozen=True, slots=True)
class AudioAnalysis:
    """Per-frame audio signals, aligned to the video frame grid.

    `available` False means THERE IS NO AUDIO STREAM. Every per-frame value is
    then empty and the assembler leaves `loudness_lufs` None on every frame,
    which is what the demo library's deliberately silent clip exists to
    exercise. It is not the same as a stream of digital silence, which is
    measured, reads -70 LUFS, and is reported as such.
    """

    available: bool
    reason: str = ""
    loudness_lufs: tuple[float, ...] = ()
    rms_dbfs: tuple[float, ...] = ()
    silent: tuple[bool, ...] = ()
    sample_rate: int = 0
    channels: int = 0
    channel_layout: str = ""
    surround_weighted: bool = False
    window_seconds: float = 0.0
    audio_video_offset_s: float = 0.0
    executor_id: str = EXECUTOR_ID
    executor_version: str = EXECUTOR_VERSION
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def run_id(self) -> str:
        return f"{self.executor_id}-{self.executor_version.replace('.', '-')}"

    def silence_runs(self, rate: float) -> tuple[tuple[int, int], ...]:
        return silence_runs(self.silent, rate)


def _as_channel_matrix(raw: bytes, channels: int) -> Any:
    import numpy  # noqa: PLC0415

    if channels <= 0:
        raise AudioError(f"channel count {channels} is not usable")
    if len(raw) % 4:
        raise AudioError(
            f"{len(raw)} bytes is not a whole number of float32 samples; the "
            "decode was cut mid-sample"
        )
    flat = numpy.frombuffer(raw, dtype="<f4")
    if flat.size % channels:
        raise AudioError(
            f"{flat.size} float samples do not divide into {channels} channels; "
            "the decode was truncated mid-frame"
        )
    return flat.reshape(-1, channels).astype(numpy.float64, copy=False)


def _windowed_mean_square(samples: Any, centres: Any, half: int) -> Any:
    """Mean of squares over [c-half, c+half) for every centre, by prefix sums.

    Exact and O(n + m). The alternative — slicing per frame — is O(n*m) and at
    30 fps over an hour that is the difference between seconds and minutes.
    Windows are clipped to the signal, never wrapped and never zero-padded:
    padding with zeroes would drag the first and last frame of every clip
    towards silence and put a false audio onset at the head of the timeline.
    """
    import numpy  # noqa: PLC0415

    squares = samples * samples
    cumulative = numpy.concatenate(
        [numpy.zeros((1,) + squares.shape[1:]), numpy.cumsum(squares, axis=0)]
    )
    total = squares.shape[0]
    lo = numpy.clip(centres - half, 0, total)
    hi = numpy.clip(centres + half, 0, total)
    counts = numpy.maximum(hi - lo, 1)
    sums = cumulative[hi] - cumulative[lo]
    return sums / counts[:, None]


def loudness_series(
    weighted: Any,
    *,
    sample_rate: int,
    centres_seconds: Sequence[float],
    window_seconds: float,
    channel_weights: Sequence[float] | None = None,
) -> list[float]:
    """K-weighted loudness in LUFS at each centre, over `window_seconds`.

    `weighted` is the ALREADY K-weighted signal (samples x channels): this
    function does the mean-square, the channel sum and the -0.691 offset, and
    nothing else. Keeping the filter out of here is what lets the test feed a
    reference-filtered signal through the identical arithmetic.
    """
    import numpy  # noqa: PLC0415

    if window_seconds <= 0:
        raise AudioError("loudness window must be positive")
    channels = weighted.shape[1]
    weights = (
        numpy.ones(channels)
        if channel_weights is None
        else numpy.asarray(channel_weights, dtype=numpy.float64)
    )
    if weights.shape[0] != channels:
        raise AudioError(
            f"{weights.shape[0]} channel weights for {channels} channels"
        )
    half = max(1, int(window_seconds * sample_rate / 2.0 + 0.5))
    centres = numpy.asarray(
        [int(value * sample_rate + 0.5) for value in centres_seconds], dtype=numpy.int64
    )
    mean_squares = _windowed_mean_square(weighted, centres, half)
    energy = mean_squares @ weights
    with numpy.errstate(divide="ignore"):
        loudness = LOUDNESS_OFFSET_DB + 10.0 * numpy.log10(
            numpy.where(energy > 0.0, energy, 1e-30)
        )
    clamped = numpy.clip(loudness, LOUDNESS_FLOOR_LUFS, LOUDNESS_CEILING_LUFS)
    return [round(float(value) + 0.0, 6) for value in clamped]


def momentary_loudness(
    weighted: Any, *, sample_rate: int, centres_seconds: Sequence[float]
) -> list[float]:
    """BS.1770 momentary loudness (400 ms). Exposed for the reference check."""
    return loudness_series(
        weighted,
        sample_rate=sample_rate,
        centres_seconds=centres_seconds,
        window_seconds=0.400,
    )


def silence_runs(silent: Sequence[bool], rate: float) -> tuple[tuple[int, int], ...]:
    """Half-open runs of silent frames at least `MIN_SILENCE_SECONDS` long.

    Short dips are not reported: the pause between two words is not silence in
    any sense a planner should act on, and reporting it as such would flood the
    culling UI with a hundred entries per minute of dialogue.
    """
    minimum = max(1, int(MIN_SILENCE_SECONDS * rate + 0.5))
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(silent):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= minimum:
                runs.append((start, index))
            start = None
    if start is not None and len(silent) - start >= minimum:
        runs.append((start, len(silent)))
    return tuple(runs)


def count_loudness_rises(loudness: Sequence[float], threshold_db: float) -> int:
    """How many frame-to-frame rises clear `threshold_db`.

    A REPORT, not a detector. `moments.py` owns onset detection; this exists so
    an operator can see whether the loudness series this module produced is
    capable of firing the planner's onset rule at all, and it is deliberately
    fed the planner's own `Policy.audio_onset_db` rather than a threshold of
    its own — two thresholds for one decision is how they drift apart.
    """
    return sum(
        1
        for index in range(1, len(loudness))
        if loudness[index] - loudness[index - 1] >= threshold_db
    )


def analyse_audio(
    proxy_path: str,
    *,
    frame_count: int,
    rate: float,
    video_start_s: float = 0.0,
) -> AudioAnalysis:
    """Measure one proxy's audio onto its video frame grid."""
    import numpy  # noqa: PLC0415

    from . import decode  # noqa: PLC0415

    if frame_count <= 0:
        raise AudioError("cannot align audio to a zero-frame video")
    if not (isinstance(rate, (int, float)) and math.isfinite(rate) and rate > 0):
        raise AudioError(f"rate={rate!r} must be a positive finite number")

    probe = decode.probe(proxy_path)
    if probe.audio is None:
        return AudioAnalysis(
            available=False,
            reason="the proxy carries no audio stream",
        )

    raw_plain = decode.read_audio(proxy_path)
    raw_weighted = decode.read_audio(proxy_path, filters=K_WEIGHTING_FILTER)
    if not raw_plain or not raw_weighted:
        return AudioAnalysis(
            available=False,
            reason="the proxy declares an audio stream that decoded to no samples",
        )
    channels = probe.audio.channels
    plain = _as_channel_matrix(raw_plain, channels)
    weighted = _as_channel_matrix(raw_weighted, channels)
    if plain.shape[0] == 0 or weighted.shape[0] == 0:
        return AudioAnalysis(
            available=False,
            reason="the proxy's audio stream decoded to no samples",
        )

    sample_rate = decode.AUDIO_SAMPLE_RATE
    offset = float(video_start_s - (probe.audio_start_s or 0.0))
    window = max(MIN_WINDOW_SECONDS, 1.0 / float(rate))
    centres = [offset + (index + 0.5) / float(rate) for index in range(frame_count)]

    notes: list[str] = []
    layout = probe.audio.layout
    surround = channels > 2
    if surround:
        notes.append(
            f"channel layout {layout!r} has {channels} channels; BS.1770's 1.41 "
            "weighting for the surround pair is NOT applied, so the reported "
            "loudness of a surround mix is low by up to ~1.5 LU. Mono and "
            "stereo — every phone, action camera and consumer proxy — are exact."
        )

    loudness = loudness_series(
        weighted,
        sample_rate=sample_rate,
        centres_seconds=centres,
        window_seconds=window,
    )

    half = max(1, int(window * sample_rate / 2.0 + 0.5))
    centre_indices = numpy.asarray(
        [int(value * sample_rate + 0.5) for value in centres], dtype=numpy.int64
    )
    mean_squares = _windowed_mean_square(plain, centre_indices, half)
    power = mean_squares.mean(axis=1)
    with numpy.errstate(divide="ignore"):
        rms = 10.0 * numpy.log10(numpy.where(power > 0.0, power, 1e-30))
    rms_dbfs = [round(float(max(-120.0, value)) + 0.0, 6) for value in rms]
    silent = tuple(value <= SILENCE_FLOOR_DBFS for value in rms_dbfs)

    return AudioAnalysis(
        available=True,
        loudness_lufs=tuple(loudness),
        rms_dbfs=tuple(rms_dbfs),
        silent=silent,
        sample_rate=sample_rate,
        channels=channels,
        channel_layout=layout,
        surround_weighted=False,
        window_seconds=window,
        audio_video_offset_s=offset,
        notes=tuple(notes),
    )
