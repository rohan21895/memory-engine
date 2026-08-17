"""The proxy and its frame index — CONSUMED from `workers/ingest`, not rebuilt.

WHAT INGEST ALREADY DOES, AND WHY THERE IS NO SECOND PROXY GENERATOR HERE

`workers/ingest/src/video.rs::execute_video_proxy` is a complete single-pass
480p proxy pipeline: hardware decode (VideoToolbox / NVDEC / QSV, verified
against the host's FFmpeg before it starts), `showinfo`-derived frame index,
atomic sidecar write, BLAKE3-addressed output path, resumable checkpoint every
120 frames, GoPro span reconciliation, and a `ProxyRef` attached to the
MediaRecord. It emits exactly the artifact this worker needs. So this module
READS that artifact; it does not produce one.

That is a deliberate refusal. A second proxy generator would be a second
answer to "what is the 480p raster of this video", and the two would differ in
scaler, in chroma handling and eventually in frame count — at which point every
moment scored against one proxy addresses source timecode derived from the
other. The one thing this worker must never get wrong is the mapping from a
sample index to a source frame.

THE SIDECAR FORMAT (ingest's, mirrored here as a reader only)

    line 1   {"schema":"memory-engine-frame-index","version":1,
              "mapping":"identity"|"table","entry_count":N,
              "source_rate":R,"proxy_rate":R,
              "source_time_base_numerator":a,"source_time_base_denominator":b}
    line 2+  {"proxy_frame":i,"source_pts":p,"source_time_seconds":t}

`mapping` is ingest's own summary of whether the source PTS deltas were
uniform. This reader does not trust the label — it recomputes it. A label is a
claim about the data; the data is right there.

WHY THE RATE COMES FROM THE TIME BASE AND NOT FROM `source_rate`

`source_rate` is a float, and 30000/1001 has no float form. The time base and
the PTS delta give the rate EXACTLY: tb 1/30000 with a delta of 1001 is
30000/1001, not 29.97002997002997. The float is then cross-checked against the
exact value and a disagreement raises, because the two disagreeing means the
sidecar was written by something other than the pipeline this reader was built
against.

WHY A NON-UNIFORM GRID IS REFUSED RATHER THAN RESAMPLED

`FeatureStream` states that sample i sits at source time {value: start + i,
rate: rate}. That is only true if the source frames are on a uniform grid. For
variable-frame-rate footage they are not, and the honest options are to carry
the mapping table through every feature (a larger contract change) or to
refuse. Refusing is what this does, naming VFR as the reason, because the
alternative — resampling onto a uniform grid and saying nothing — produces
moments whose source timecode is wrong by up to a frame with nothing anywhere
to indicate it. That is a seam, and it is recorded as one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

__all__ = [
    "FrameIndex",
    "FrameIndexEntry",
    "Proxy",
    "ProxyError",
    "SourceGrid",
    "VariableFrameRate",
    "proxies_in_record",
    "read_frame_index",
]

SIDECAR_SCHEMA = "memory-engine-frame-index"
SUPPORTED_SIDECAR_VERSIONS = frozenset({1})
VIDEO_PROXY_KIND = "video_proxy_480p"

# How far the sidecar's float `source_rate` may sit from the rate computed
# exactly from the time base before the two are treated as describing different
# things. 1e-9 relative is far looser than a double's precision on these
# magnitudes and far tighter than any real rate difference.
RATE_AGREEMENT = 1e-9


class ProxyError(ValueError):
    """The proxy or its frame index cannot be used to place a moment in time."""


class VariableFrameRate(ProxyError):
    """The source frames are not on a uniform grid. A seam, not a bug."""


@dataclass(frozen=True, slots=True)
class FrameIndexEntry:
    proxy_frame: int
    source_pts: int
    source_time_seconds: float


@dataclass(frozen=True, slots=True)
class SourceGrid:
    """The uniform source frame grid a proxy maps onto.

    `rate` is exact. `rate_float` is what goes into a `FeatureStream`, whose
    validation rejects anything that is not an int or a float — and float() of
    the exact Fraction is the same double the sidecar carries, so nothing is
    lost by the conversion that was not already lost by JSON.
    """

    rate: Fraction
    start_value: int
    frame_count: int

    @property
    def rate_float(self) -> float:
        return float(self.rate)


@dataclass(frozen=True, slots=True)
class FrameIndex:
    path: Path
    version: int
    declared_mapping: str
    entry_count: int
    declared_source_rate: float | None
    declared_proxy_rate: float | None
    time_base: Fraction | None
    entries: tuple[FrameIndexEntry, ...]

    def source_grid(self) -> SourceGrid:
        """The uniform grid this index describes, or a refusal saying why not.

        Checks, in order, each of which has a distinct failure meaning:

          * the proxy frames are 0..N-1 with no gaps (a gap means the sidecar
            lost rows, and every later sample would be misattributed);
          * the source PTS strictly increase (a repeat or a reversal is a
            reordered or duplicated frame);
          * every PTS delta is identical (uniform grid);
          * the first PTS is a whole number of deltas from zero, so the source
            frame index of proxy frame 0 is an integer rather than a rounding.
        """
        entries = self.entries
        if len(entries) < 2:
            raise ProxyError(
                f"{self.path.name} holds {len(entries)} frame(s); a grid cannot be "
                "derived from fewer than two, and a one-frame video has no "
                "moments in it"
            )
        for index, entry in enumerate(entries):
            if entry.proxy_frame != index:
                raise ProxyError(
                    f"{self.path.name} row {index} declares proxy_frame="
                    f"{entry.proxy_frame}; the index must be contiguous from 0, "
                    "and a gap silently shifts every later sample onto the wrong "
                    "source frame"
                )
        deltas = {
            entries[i + 1].source_pts - entries[i].source_pts
            for i in range(len(entries) - 1)
        }
        if any(delta <= 0 for delta in deltas):
            raise ProxyError(
                f"{self.path.name} has non-increasing source PTS; the frames are "
                "reordered or duplicated and no time mapping can be trusted"
            )
        if len(deltas) > 1:
            raise VariableFrameRate(
                f"{self.path.name} has {len(deltas)} distinct PTS deltas "
                f"({sorted(deltas)[:4]}...): the source is variable frame rate. "
                "FeatureStream places sample i at source frame start+i, which "
                "assumes a uniform grid, so this footage is refused rather than "
                "resampled onto a grid it does not sit on."
            )
        delta = deltas.pop()

        if self.time_base is None:
            raise ProxyError(
                f"{self.path.name} carries no source time base, so the frame rate "
                "cannot be derived exactly. The declared float rate is not a "
                "substitute: 30000/1001 has no float form."
            )
        rate = 1 / (self.time_base * delta)
        if self.declared_source_rate is not None:
            declared = float(self.declared_source_rate)
            if declared <= 0 or abs(float(rate) - declared) > RATE_AGREEMENT * max(
                1.0, declared
            ):
                raise ProxyError(
                    f"{self.path.name} declares source_rate={declared} but its "
                    f"time base and PTS delta give {rate} ({float(rate)}). Two "
                    "different answers to 'what rate is this' means the sidecar "
                    "was not written by the pipeline this reader was built for."
                )
        first = entries[0].source_pts
        if first % delta != 0:
            raise ProxyError(
                f"{self.path.name} starts at PTS {first}, which is not a whole "
                f"multiple of the frame delta {delta}; the source frame index of "
                "proxy frame 0 would have to be rounded, and a rounded start "
                "offsets every moment in the clip"
            )
        return SourceGrid(
            rate=rate, start_value=first // delta, frame_count=len(entries)
        )


def read_frame_index(path: str | Path) -> FrameIndex:
    """Parse one `.idx` sidecar written by `workers/ingest`."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as error:
        raise ProxyError(f"frame index is unreadable: {target}") from error
    lines = text.splitlines()
    if not lines:
        raise ProxyError(f"frame index is empty: {target}")
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise ProxyError(f"{target.name} has no JSON header") from error
    if not isinstance(header, dict) or header.get("schema") != SIDECAR_SCHEMA:
        raise ProxyError(
            f"{target.name} is not a {SIDECAR_SCHEMA} sidecar "
            f"(schema={header.get('schema') if isinstance(header, dict) else None!r})"
        )
    version = header.get("version")
    if version not in SUPPORTED_SIDECAR_VERSIONS:
        raise ProxyError(
            f"{target.name} is sidecar version {version!r}; this reader "
            f"understands {sorted(SUPPORTED_SIDECAR_VERSIONS)}. A newer writer "
            "may have changed what a row means, so it is refused rather than "
            "read optimistically."
        )

    entries: list[FrameIndexEntry] = []
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            entries.append(
                FrameIndexEntry(
                    proxy_frame=int(row["proxy_frame"]),
                    source_pts=int(row["source_pts"]),
                    source_time_seconds=float(row["source_time_seconds"]),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ProxyError(f"{target.name} line {number} is malformed") from error

    declared_count = header.get("entry_count")
    if isinstance(declared_count, int) and declared_count != len(entries):
        raise ProxyError(
            f"{target.name} declares {declared_count} entries and holds "
            f"{len(entries)}. A truncated sidecar read as complete would put "
            "the tail of the video outside the index entirely."
        )

    numerator = header.get("source_time_base_numerator")
    denominator = header.get("source_time_base_denominator")
    time_base: Fraction | None = None
    if isinstance(numerator, int) and isinstance(denominator, int) and denominator > 0:
        if numerator <= 0:
            raise ProxyError(f"{target.name} declares a non-positive time base")
        time_base = Fraction(numerator, denominator)

    return FrameIndex(
        path=target,
        version=int(version),
        declared_mapping=str(header.get("mapping") or "unknown"),
        entry_count=len(entries),
        declared_source_rate=_optional_float(header.get("source_rate")),
        declared_proxy_rate=_optional_float(header.get("proxy_rate")),
        time_base=time_base,
        entries=tuple(entries),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass(frozen=True, slots=True)
class Proxy:
    """One video proxy, with everything needed to place it in source time."""

    media_id: str
    path: Path
    frame_index: FrameIndex
    proxy_id: str
    width: int | None = None
    height: int | None = None
    generator_version: str | None = None

    @property
    def proxy_grid(self) -> SourceGrid:
        """The proxy's own grid.

        Ingest writes `proxy_rate == source_rate` — the proxy is a spatial
        downscale, not a temporal one, and `-fps_mode passthrough` keeps every
        frame — so proxy frame i is source frame start+i. That equality is
        checked in `source_grid()` rather than assumed here.
        """
        return self.frame_index.source_grid()


def proxies_in_record(record: Mapping[str, Any]) -> tuple[Proxy, ...]:
    """Every usable 480p video proxy attached to a MediaRecord.

    Empty means the record has no video proxy — the ingest `generate_video_proxy`
    job has not been run for it — which is a normal, reportable state and not an
    error. A proxy of the right kind WITHOUT a frame index is an error: analysis
    could still decode it, and every result would address proxy time while
    claiming to address source time.
    """
    media_id = str(record.get("media_id") or "")
    found: list[Proxy] = []
    entries: Sequence[Any] = record.get("proxies") or ()
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("kind") != VIDEO_PROXY_KIND:
            continue
        index = entry.get("frame_index")
        if not isinstance(index, Mapping) or not index.get("path"):
            raise ProxyError(
                f"{media_id[:12]}: a {VIDEO_PROXY_KIND} proxy carries no frame "
                "index. Without it a sample index cannot be mapped to source "
                "timecode, and every moment scored from it would be placed by "
                "assumption."
            )
        size = entry.get("size") if isinstance(entry.get("size"), Mapping) else {}
        found.append(
            Proxy(
                media_id=media_id,
                path=Path(str(entry.get("path"))),
                frame_index=read_frame_index(str(index["path"])),
                proxy_id=str(entry.get("proxy_id") or ""),
                width=_optional_int(size.get("width")),
                height=_optional_int(size.get("height")),
                generator_version=(
                    str(entry["generator_version"])
                    if entry.get("generator_version")
                    else None
                ),
            )
        )
    found.sort(key=lambda proxy: proxy.proxy_id)
    return tuple(found)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
