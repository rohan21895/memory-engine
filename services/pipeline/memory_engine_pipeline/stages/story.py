"""`story` — video proxies -> FeatureStream -> plan_moments -> a reel AND a film.

WHAT THIS FILE USED TO SAY, AND WHAT CHANGED

It used to report UNAVAILABLE and list five producers nothing had wired. Three
of the five now exist and are driven from here:

    generate_video_proxy   workers/ingest, one JobSpec per video, run below
    shot detection         workers/video-analysis, classical content-hysteresis
    the feature stream     workers/video-analysis `analyse_proxy`
    moment scoring         packages/story-engine `plan_moments`
    the reel plan          packages/story-engine `plan_reel` -> an EDL
    the film plan          packages/story-engine `plan_film` -> a second EDL

TWO OF THE FIVE STILL DO NOT EXIST, AND THAT IS REPORTED ON EVERY RUN.

    transcription (faster-whisper)  -> `Frame.speech` is None and `words` is
                                       empty for every stream. `moments.py`
                                       renormalises the absence rather than
                                       reading it as silence, so the scores are
                                       honest -- but NO CUT IN THE OUTPUT CAN BE
                                       CERTIFIED WORD-SAFE. `plan_reel` and
                                       `plan_film` both emit their
                                       `no_mid_word_cut` finding only when words
                                       exist, so neither plan produced here
                                       carries one at all. A cut made without
                                       speech data is a different product from
                                       one made with it, and the difference is
                                       invisible in the file.

                                       It costs the FILM more than the reel.
                                       Speech-aware trimming, the L-cut and the
                                       error-severity mid-word gate are the
                                       three things that make a film planner
                                       different from a long reel, and all
                                       three are inert here. `FilmPlan.
                                       word_safe_certified` is False and the
                                       film's notes say so in those words.
    face / smile / audio-event      -> `face_presence`, `max_face_area_ratio`,
    detection (SCRFD, CLAP)            `smile_intensity`, `noise` are None.
                                       The reel therefore cannot prefer a face,
                                       and `AmbientSettings.wind_noise_ratio`
                                       can never fire.

THE ONE THING THIS STAGE STILL REFUSES TO DO

It does not synthesise a FeatureStream. Every frame-level number it hands
`plan_moments` was measured by `workers/video-analysis` off the 480p proxy that
`workers/ingest` produced; everything else is None, all the way down, and
`AnalysisReport.not_measured` travels with the run. A stream assembled from
"whatever the records happen to contain" would produce a real EDL full of
moments nobody measured, it would render, and a video artifact is the hardest
output for a person to audit against its source.

WHAT THIS STAGE PLANS

TWO EDLs, FROM ONE POOL OF MOMENTS, AND THEY ARE DIFFERENT CUTS.

  * A REEL. `plan_reel`: 15 seconds asked for, cut on a tick grid (there is no
    music, so nothing is beat-locked), strongest moment moved to the front.
  * A FILM. `plan_film`: a three-act arc over the SAME moments in chronological
    order, held for seconds rather than beats, with the pacing varying by act
    and by content. `kind: "film"`, `story_arc.template: "three_act"`.

    This file used to say "NOT A FILM ... relabelling a reel `kind: film` would
    cost one line and would be a lie about the product". The planner now exists,
    and the line above is what changed: the film is a different cut list, a
    different running order and a different set of holds, planned by a different
    module. What has NOT changed is the honesty about what is missing, and the
    film loses more to it than the reel does -- see the transcription entry
    above.

BOTH PLANS DESCRIBE THE SAME FOOTAGE, DELIBERATELY.

The rate/raster group, the media refs and the moment pool are chosen once and
handed to both planners. Two outputs planned from two different selections
would be two answers to two questions, and "why is the person in the reel not
in the film" would have no answer that mentions taste.

THREE THINGS THE PLANNER HAS TO BE TOLD, AND WHERE EACH COMES FROM

  RATE. Exactly, from the frame-index sidecar's time base and PTS delta, so
  30000/1001 stays 30000/1001. An EDL has ONE timeline rate and the renderer
  refuses a source at any other rate rather than resampling it, so a library at
  five different rates is planned at one of them and the rest are REPORTED as
  excluded, not silently dropped.

  GEOMETRY. This used to be the gap worth reading twice: `MediaRecord.video`
  was null for every video, so the SOURCE's pixel geometry was unmeasured, the
  render target fell back to the 480p proxy raster, and reframing was disabled
  because a crop needs an aspect ratio nobody had measured. `workers/ingest`
  now populates `VideoProperties` (issue #78), so:

    - The render target is `MediaRecord.video.oriented_size` -- the size AFTER
      the container's display matrix is applied, which is the space every
      normalised coordinate in this system lives in. A 1920x1080 library plans
      a 1920x1080 master rather than an 854x480 one.
    - `reframe` is ENABLED, because the source aspect ratio is now a measured
      number rather than an assumption.

  THE FALLBACK IS STILL THERE, AND IT STILL MATTERS. A record whose `video` is
  null -- ingested before #78, or written by a worker that could not probe its
  source -- goes back to the proxy raster with reframing off. Measured and
  unmeasured media are never mixed into one reel: where the geometry came from
  is part of the grouping key, not a detail, because a target derived from a
  measurement and one derived from a fallback are different claims about the
  picture. Which of the two this run used is printed.

  MUSIC. There is none. `packages/story-engine/music/library.json` is
  `audio_bundled: false` -- three placeholder entries defining the shape, no
  playable file -- so there is no track to beat-detect, no `BeatGrid`, and
  therefore NO CUT IN THIS REEL IS BEAT-LOCKED. Cuts fall on the planner's
  unlocked tick grid. This is reported in the counts as `beat_locked: 0` beside
  the clip count, because "the cuts landed on beats" is the single most
  plausible thing to assume about a reel and it is false here.

AMBIENT, WHICH IS A DECISION AND NOT A DEFAULT

`AmbientSettings`' defaults (-14 dB ambient, a 120 Hz high-pass, `light` noise
suppression) are calibrated for location sound sitting UNDER a music bed. With
no music the ambient IS the mix, so:

    music present -> the story-engine defaults, unchanged. render-video will
                     refuse them on contracts#53 and #54 and this stage reports
                     the refusal verbatim. That refusal is correct: the gain
                     composition rule and the filter response are genuinely
                     unspecified.
    no music      -> ambient at unity, no high-pass, no noise suppression.

The second is not a default chosen to get past the gate; it is the only level
that means anything without a bed, and "no DSP" is a statement the renderer can
execute rather than one it has to interpret. It is announced every run.

UNITY MEANS ALL THREE GAINS, NOT JUST THE DEFAULT ONE

`AmbientSettings` carries three levels -- `default_gain_db`, `speech_gain_db`
and `wind_gain_db` -- and only the first is named "default". Opening the bed to
unity by setting `default_gain_db=0` and leaving `speech_gain_db` at its -6
inverts the type's meaning: -6 is where speech sits when it comes UP out of a
bed ducked to -14, and against an open bed it is 6 dB DOWN. Every shot with
speech in it would then be the quietest shot in the cut -- the exact opposite
of `preserve_speech: true`, which is written into the plan beside it.

So this stage sets `speech_gain_db=0` too. There is no bed here for speech to
come up out of; the location sound IS the mix, and speech is part of it at the
level it was recorded. `wind_gain_db` is left alone, because wind is still wind
with no music playing.

This is not a hypothetical that was reasoned about. `plan_film` refuses the
combination outright and the story stage FAILED on every run until both gains
moved -- taking the reel down with it, since the refusal is a ValueError and
the caller only catches `FilmTooShort`. The reel planner does not check, so the
reel had been carrying the same inversion silently: harmless only because
nothing in this pipeline measures speech yet, and waiting for the transcript
backend to arrive and make the speaking shots the hardest ones to hear.

RESUMABILITY

Per record and per step, in `MediaRecord.processing.stages`, using the stage
names the contract already defines (`video_proxy`, `shot_detection`,
`moment_scoring`). One JobSpec per video for the proxy, so a corrupt file fails
its own job and the other forty finish -- the ingest worker aborts a whole batch
on the first failure, which for a library with one truncated MP4 in it means no
proxies at all.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..events import utc_now
from ..jobstore import build_job
from .base import (
    StageContext,
    StageResult,
    StageStatus,
    blocked_by,
    write_json_atomically,
)

STAGE = "story"

PROXY_JOB_TYPE = "generate_video_proxy"
PROXY_STEP = "video_proxy"
SHOT_STEP = "shot_detection"
MOMENT_STEP = "moment_scoring"

INGEST_BINARY = Path("workers/ingest/target/release/memory-engine-ingest")

# Producers that would fill the rest of a FeatureStream and do not exist. Named
# once, here, so the report and this docstring cannot drift apart.
ABSENT_PRODUCERS: tuple[tuple[str, str], ...] = (
    (
        "transcription / faster-whisper (model host)",
        "word timings are unknown, so no cut can be certified word-safe",
    ),
    (
        "face detection / SCRFD + smile (model host)",
        "face_presence, max_face_area_ratio and smile_intensity are not measured",
    ),
    (
        "audio events / CLAP (model host)",
        "speech and noise ratios are not measured",
    ),
    (
        "beat detection (no music)",
        "music/library.json bundles no audio, so there is no beat grid and no "
        "cut is beat-locked",
    ),
)

# The 480p proxy profile. Fixed rather than configurable because
# `workers/ingest` refuses anything else -- height 480, h264, a frame index --
# and a knob whose only legal value is its default is a lie about the interface.
PROXY_PARAMS: dict[str, Any] = {
    "height": 480,
    "codec": "h264",
    "crf": 23,
    "hardware_decode": "videotoolbox",
    "emit_frame_index": True,
}

_HARDWARE_BACKENDS = {"darwin": "videotoolbox", "linux": "nvdec", "win32": "qsv"}


# ------------------------------------------------------------------ helpers --


def _hardware_backend() -> str:
    import sys  # noqa: PLC0415

    return _HARDWARE_BACKENDS.get(sys.platform, "videotoolbox")


def _step_done(record: Mapping[str, Any], step: str) -> bool:
    stage = ((record.get("processing") or {}).get("stages") or {}).get(step) or {}
    return stage.get("status") == "done"


def _mark(record: dict[str, Any], step: str, status: str, **extra: Any) -> None:
    processing = record.setdefault("processing", {"state": "discovered", "stages": {}})
    stages = processing.setdefault("stages", {})
    previous = stages.get(step) or {}
    entry: dict[str, Any] = {
        "status": status,
        "attempts": int(previous.get("attempts", 0)) + 1,
        "completed_at": utc_now() if status == "done" else previous.get("completed_at"),
    }
    entry.update(extra)
    stages[step] = entry


def _roll_up(record: dict[str, Any]) -> None:
    """`processing.state` for a video.

    The contract's enum already has the two positions this stage moves through:
    `proxied` once there is a 480p raster, `analyzed` once moments exist. A
    video that has a proxy and no moments must not read `analyzed`, because
    that is what a caller asking "is this library ready to cut" branches on.
    """
    processing = record.setdefault("processing", {"state": "discovered", "stages": {}})
    if processing.get("state") == "quarantined":
        return
    if _step_done(record, MOMENT_STEP):
        processing["state"] = "analyzed"
    elif _step_done(record, PROXY_STEP):
        processing["state"] = "proxied"


def _source_path(record: Mapping[str, Any]) -> str | None:
    for source in record.get("sources") or ():
        path = source.get("path")
        if source.get("present") and path and Path(path).is_file():
            return str(path)
    return None


def _video_ids(ctx: StageContext) -> list[str]:
    return [
        row[0]
        for row in ctx.database.connection.execute(
            "SELECT media_id FROM media WHERE kind = 'video' ORDER BY media_id"
        )
    ]


@lru_cache(maxsize=1)
def _moment_validator() -> Any:
    from jsonschema import Draft202012Validator  # noqa: PLC0415
    from referencing import Registry, Resource  # noqa: PLC0415

    repo_root = Path(__file__).resolve().parents[4]
    schema_dir = repo_root / "contracts" / "schemas"
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(schema_dir.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(
        documents["moment-record.schema.json"], registry=registry
    )


@lru_cache(maxsize=1)
def _edl_validator() -> Any:
    from jsonschema import Draft202012Validator  # noqa: PLC0415
    from referencing import Registry, Resource  # noqa: PLC0415

    repo_root = Path(__file__).resolve().parents[4]
    schema_dir = repo_root / "contracts" / "schemas"
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(schema_dir.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(documents["edl.schema.json"], registry=registry)


# -------------------------------------------------------------------- stage --


def run(ctx: StageContext) -> StageResult:
    upstream = ctx.require("ingest")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    videos = _video_ids(ctx)
    if not videos:
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="the library holds no video, so there is nothing to score",
            counts={"videos": 0},
        )

    binary = ctx.repo_root / INGEST_BINARY
    if not binary.is_file():
        return StageResult(
            stage=STAGE,
            status=StageStatus.UNAVAILABLE,
            detail=(
                f"{len(videos)} video(s) and no proxy generator: the ingest worker "
                f"is not built. cd {ctx.repo_root / 'workers/ingest'} && "
                "cargo build --release"
            ),
            counts={"videos": len(videos)},
        )

    import shutil  # noqa: PLC0415

    if shutil.which("ffmpeg") is None:
        return StageResult(
            stage=STAGE,
            status=StageStatus.UNAVAILABLE,
            detail=(
                "ffmpeg is not on PATH. Every number in a feature stream comes out "
                "of a decode, so there is nothing to measure and nothing is claimed."
            ),
            counts={"videos": len(videos)},
        )

    for producer, consequence in ABSENT_PRODUCERS:
        ctx.reporter.event(STAGE, "note", f"not wired: {producer} — {consequence}")

    proxies = _generate_proxies(ctx, videos, binary=binary)
    moments = _score_moments(ctx, videos)

    if proxies["no_source"] == len(videos):
        # Every video record is a span assembly, or its file is on a drive that
        # is not plugged in. Neither is a failure and neither is work.
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail=(
                f"none of the {len(videos)} video record(s) resolves to a file on "
                "this machine, so there is nothing to decode"
            ),
            counts={"videos": len(videos), "proxies": proxies},
        )

    if moments["scored"] == 0 and moments["already"] == 0:
        detail = (
            f"no video produced a moment: {proxies['done'] + proxies['already']} of "
            f"{len(videos)} have a 480p proxy and {moments['failed']} failed analysis"
        )
        ctx.reporter.event(STAGE, "stage_failed", detail)
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=detail,
            counts={"videos": len(videos), "proxies": proxies, "moments": moments},
        )

    counts: dict[str, Any] = {
        "videos": len(videos),
        "proxies": proxies,
        "moments": moments,
        "not_wired": [producer for producer, _ in ABSENT_PRODUCERS],
    }
    selection = _select(ctx)
    if selection is None:
        detail = (
            "moments were scored but nothing could be planned: no group of videos "
            "shares a frame rate and a proxy raster, so there is no single timeline "
            "rate and target geometry a contract EDL could declare"
        )
        ctx.reporter.event(STAGE, "stage_failed", detail)
        return StageResult(
            stage=STAGE, status=StageStatus.FAILED, detail=detail, counts=counts
        )

    planned_at = utc_now()
    for note in selection.notes:
        ctx.reporter.event(STAGE, "note", note)

    from memory_engine_story.film import FilmTooShort  # noqa: PLC0415

    reel = _plan_reel(ctx, selection, planned_at)
    # The film is planned independently of the reel: they are two products, and
    # a library that affords one and not the other must produce the one it
    # affords rather than nothing. `plan_film` REFUSES below three usable
    # moments -- a three-act arc needs a shot in each act, and a two-shot
    # artifact labelled a film is the relabelling this stage has always
    # refused to do. Its own message is reported verbatim.
    film_refusal = ""
    try:
        film = _plan_film(ctx, selection, planned_at)
    except FilmTooShort as error:
        film, film_refusal = None, f"no film planned: {error}"

    if reel is None and film is None:
        detail = (
            "moments were scored and neither a reel nor a film could be planned "
            f"from them. {film_refusal}".strip()
        )
        ctx.reporter.event(STAGE, "stage_failed", detail)
        return StageResult(
            stage=STAGE, status=StageStatus.FAILED, detail=detail, counts=counts
        )

    outputs: list[str] = []
    details: list[str] = []
    for outcome in (reel, film):
        if outcome is None:
            continue
        counts[outcome.kind] = outcome.counts
        outputs.append(str(outcome.path))
        details.append(outcome.detail)
        for note in outcome.notes:
            ctx.reporter.event(STAGE, "note", note)
    if reel is None:
        counts["reel"] = "not planned"
    if film is None:
        counts["film"] = film_refusal
        details.append(film_refusal)
        ctx.reporter.event(STAGE, "note", film_refusal)

    detail = "; ".join(details)
    ctx.reporter.event(STAGE, "stage_done", detail)
    return StageResult(
        stage=STAGE,
        status=StageStatus.COMPLETED,
        detail=detail,
        outputs=tuple(outputs),
        counts=counts,
    )


# ------------------------------------------------------------------ proxies --


def _generate_proxies(
    ctx: StageContext, videos: Sequence[str], *, binary: Path
) -> dict[str, int]:
    """One `generate_video_proxy` JobSpec per video.

    Per video, not per batch: the ingest worker returns an error for the whole
    job at the first file it cannot decode, so one truncated MP4 in a library
    means no proxies at all. A job whose identity is one media_id also makes
    resumption exact -- there is nothing to re-do for a file that finished.
    """
    counts = {"done": 0, "already": 0, "failed": 0, "no_source": 0}
    records_dir = ctx.workdir / "records"
    total = len(videos)
    for index, media_id in enumerate(videos, start=1):
        record = ctx.database.get_media(media_id)
        if record is None:  # pragma: no cover - the row cannot vanish mid-run
            continue
        if _proxy_of(record) is None:
            # The worker attaches the ProxyRef to the record ON DISK and this
            # stage copies it into the database afterwards, so a kill between
            # those two moments leaves a finished proxy the database has never
            # heard of. Adopting it costs one file read; re-encoding it costs a
            # full decode of the source and produces the same content-addressed
            # bytes.
            written = _record_on_disk(records_dir, media_id)
            if written is not None and _proxy_of(written) is not None:
                record = written
                _mark(record, PROXY_STEP, "done")
                _roll_up(record)
                record["updated_at"] = utc_now()
                ctx.database.put_media(record)
                ctx.reporter.event(
                    STAGE,
                    "note",
                    f"{media_id[:12]}: a previous run produced this proxy and was "
                    "killed before the library recorded it",
                )
        if _proxy_of(record) is not None:
            counts["already"] += 1
            continue
        if (record.get("processing") or {}).get("state") == "quarantined":
            counts["no_source"] += 1
            continue
        if _source_path(record) is None:
            # A span assembly, or a file on an unplugged drive. Neither is an
            # error and neither is a proxy this stage can make.
            counts["no_source"] += 1
            continue

        job = build_job(
            job_type=PROXY_JOB_TYPE,
            scope=ctx.settings.scope,
            params={**PROXY_PARAMS, "hardware_decode": _hardware_backend()},
            media_ids=[media_id],
            priority=450,
            requirements={
                "hardware_decode": True,
                "requires_source_file": True,
                "min_disk_mb": 512,
            },
        )
        job = ctx.jobs.get(job["job_id"]) or ctx.jobs.ensure(job)
        if job["state"]["status"] == "failed" and not _worth_retrying(job):
            # Already tried, already recorded, and the worker either said the
            # failure is not retryable or it has been retried enough. Retrying a
            # file the decoder rejects on every run forever is how a library
            # with one corrupt clip in it never finishes; retrying a transient
            # GPU failure exactly once and then giving up for ever is the
            # opposite mistake, which is why the worker's own `retryable` flag
            # decides rather than a blanket rule here.
            counts["failed"] += 1
            continue

        job = ctx.jobs.begin(job)
        request = ctx.path("video-proxy", f"{job['job_id']}.request.json")
        checkpoint = ctx.path("video-proxy", f"{job['job_id']}.json")
        write_json_atomically(request, job)
        process = subprocess.run(  # noqa: S603
            [str(binary), str(request), str(records_dir), str(checkpoint)],
            capture_output=True,
            text=True,
            check=False,
        )
        persisted = _read_json(checkpoint)
        if isinstance(persisted, dict) and persisted.get("job_id") == job["job_id"]:
            # The worker owns the cursor and rewrites the whole JobSpec, so its
            # copy is ahead of ours whenever the two disagree.
            job = persisted
            ctx.jobs.put(job)

        if process.returncode != 0:
            message = (process.stderr or process.stdout or "").strip().splitlines()
            detail = message[-1] if message else f"exit code {process.returncode}"
            # The worker's own JobError is kept, never overwritten. It names the
            # actual diagnosis -- `unsupported_codec` for a file whose moov atom
            # is missing -- and this stage knows only that a subprocess exited
            # non-zero. Replacing a precise cause with a vague one is how a
            # library full of HEVC clips comes to look like a library full of
            # generic failures.
            worker_error = (job.get("error") or {}) if job["state"][
                "status"
            ] == "failed" else {}
            if not worker_error:
                ctx.jobs.fail(
                    job,
                    code="internal_error",
                    message="the proxy generator exited non-zero without recording a cause",
                    retryable=False,
                )
                worker_error = job.get("error") or {}
            record = ctx.database.get_media(media_id) or record
            _mark(
                record,
                PROXY_STEP,
                "failed",
                last_error={
                    "code": str(worker_error.get("code") or "internal-error").replace(
                        "_", "-"
                    ),
                    "message": "the proxy generator refused this file; path redacted",
                    "retryable": bool(worker_error.get("retryable", False)),
                    "occurred_at": utc_now(),
                },
            )
            _roll_up(record)
            record["updated_at"] = utc_now()
            ctx.database.put_media(record)
            counts["failed"] += 1
            ctx.reporter.event(
                STAGE, "note", f"proxy failed for {media_id[:12]}: {detail}"
            )
            continue

        # The worker rewrote the MediaRecord on disk with its ProxyRef attached.
        # Read that back rather than the database copy, which predates it.
        written = _record_on_disk(records_dir, media_id)
        record = written if written is not None else record
        _mark(record, PROXY_STEP, "done", job_id=job["job_id"])
        _roll_up(record)
        record["updated_at"] = utc_now()
        ctx.database.put_media(record)
        if job["state"]["status"] != "completed":  # pragma: no cover - defensive
            ctx.jobs.complete(job)
        counts["done"] += 1
        ctx.reporter.progress(
            STAGE,
            units_done=float(index),
            units_total=float(total),
            unit="videos",
            message="480p proxies",
        )
    return counts


# A truncated MP4 fails identically every time and the worker still reports it
# retryable, because from inside FFmpeg a decode failure and a busy GPU look the
# same. Three attempts is enough to ride out the transient one and few enough
# that a permanently broken file stops costing a decode on every run.
_MAX_PROXY_ATTEMPTS = 3


def _worth_retrying(job: Mapping[str, Any]) -> bool:
    error = job.get("error") or {}
    if not error.get("retryable", False):
        return False
    return int(job["state"].get("attempts", 0)) < _MAX_PROXY_ATTEMPTS


def _proxy_of(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for proxy in record.get("proxies") or ():
        if proxy.get("kind") == "video_proxy_480p":
            return proxy
    return None


def _record_on_disk(records_dir: Path, media_id: str) -> dict[str, Any] | None:
    path = records_dir / "records" / media_id[:2] / media_id[2:4] / f"{media_id}.json"
    return _read_json(path)


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------ moments --


def _score_moments(ctx: StageContext, videos: Sequence[str]) -> dict[str, Any]:
    """FeatureStream -> plan_moments -> MomentRecords, one video at a time."""
    from memory_engine_story.moments import plan_moments  # noqa: PLC0415
    from memory_engine_video_analysis import stream as stream_module  # noqa: PLC0415
    from memory_engine_video_analysis.proxy import (  # noqa: PLC0415
        ProxyError,
        proxies_in_record,
    )
    from memory_engine_video_analysis.transcript import (  # noqa: PLC0415
        NullTranscriptBackend,
    )

    validator = _moment_validator()
    counts: dict[str, Any] = {
        "scored": 0,
        "already": 0,
        "failed": 0,
        "no_proxy": 0,
        "kept": 0,
        "eliminated": 0,
        "word_safe_cuts_certified": 0,
    }
    total = len(videos)
    for index, media_id in enumerate(videos, start=1):
        record = ctx.database.get_media(media_id)
        if record is None:  # pragma: no cover
            continue
        if _step_done(record, MOMENT_STEP):
            counts["already"] += 1
            continue
        try:
            found = proxies_in_record(record)
        except ProxyError as error:
            counts["failed"] += 1
            ctx.reporter.event(STAGE, "note", f"{media_id[:12]}: {error}")
            continue
        if not found:
            counts["no_proxy"] += 1
            continue
        if len(found) > 1:
            ctx.reporter.event(
                STAGE,
                "note",
                f"{media_id[:12]} carries {len(found)} video proxies; analysing "
                f"{found[0].proxy_id[:12]} (lowest proxy_id). Detach the stale one.",
            )
        proxy = found[0]

        analysed_at = utc_now()
        try:
            feature_stream, report = stream_module.analyse_proxy(
                proxy, transcript_backend=NullTranscriptBackend()
            )
            plan = plan_moments(
                feature_stream,
                run_id=stream_module.SCORER_RUN_ID,
                created_at=analysed_at,
                model_runs=stream_module.model_runs_for(
                    proxy, report, ran_at=analysed_at
                ),
            )
        except (ValueError, RuntimeError, OSError) as error:
            counts["failed"] += 1
            _mark(
                record,
                MOMENT_STEP,
                "failed",
                last_error={
                    "code": "internal-error",
                    "message": f"{type(error).__name__}: {error}",
                    "retryable": False,
                    "occurred_at": utc_now(),
                },
            )
            record["updated_at"] = utc_now()
            ctx.database.put_media(record)
            ctx.reporter.event(
                STAGE, "note", f"{media_id[:12]}: {type(error).__name__}: {error}"
            )
            continue

        invalid: list[str] = []
        for position, row in enumerate(plan.records):
            errors = sorted(
                validator.iter_errors(row), key=lambda error: list(error.path)
            )
            if errors:
                invalid.append(
                    f"record {position}: "
                    + "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:2])
                )
        if invalid:
            # A record the contract rejects is not stored and not counted. The
            # alternative -- storing it anyway -- puts a shape nothing else in
            # the system can read into the one table the reel planner reads.
            counts["failed"] += 1
            _mark(
                record,
                MOMENT_STEP,
                "failed",
                last_error={
                    "code": "validation-failed",
                    "message": invalid[0],
                    "retryable": False,
                    "occurred_at": utc_now(),
                },
            )
            record["updated_at"] = utc_now()
            ctx.database.put_media(record)
            ctx.reporter.event(STAGE, "note", f"{media_id[:12]}: {invalid[0]}")
            continue

        for row in plan.records:
            ctx.database.put_moment(row)

        _mark(record, SHOT_STEP, "done", job_id=None)
        _mark(record, MOMENT_STEP, "done", job_id=None)
        _roll_up(record)
        record["updated_at"] = utc_now()
        ctx.database.put_media(record)

        counts["scored"] += 1
        counts["kept"] += len(plan.moments)
        counts["eliminated"] += len(plan.eliminated)
        if report.transcript_available:  # pragma: no cover - no backend exists
            counts["word_safe_cuts_certified"] += len(plan.moments)
        ctx.reporter.progress(
            STAGE,
            units_done=float(index),
            units_total=float(total),
            unit="videos",
            message="moment scoring",
        )
    return counts


# ------------------------------------------------------------------ selection --


@dataclass(frozen=True, slots=True)
class _PlanOutcome:
    kind: str
    path: Path
    detail: str
    notes: tuple[str, ...]
    counts: dict[str, Any]


# Where a candidate's target geometry came from. Part of the grouping key, so a
# reel is never assembled half out of measurements and half out of fallbacks.
_SOURCE_GEOMETRY = "source"
_PROXY_GEOMETRY = "proxy"


@dataclass(frozen=True, slots=True)
class _Candidate:
    media_id: str
    rate: float
    start_value: int
    frame_count: int
    raster: tuple[int, int]
    #: `MediaRecord.video.oriented_size` when ingest measured it, else the proxy
    #: raster. `geometry_from` says which, so it never has to be guessed.
    geometry: tuple[int, int]
    geometry_from: str
    path: str
    label: str | None
    captured_local: str | None


@dataclass(frozen=True, slots=True)
class _Selection:
    """One rate, one raster, one moment pool — shared by both planners.

    Chosen once and handed to the reel and the film alike. Selecting twice
    would let the two outputs describe different footage, and the difference
    would look like an editorial choice rather than an accident of grouping.
    """

    rate: float
    raster: tuple[int, int]
    aspect: tuple[int, int]
    media: tuple[Any, ...]
    moments: tuple[Any, ...]
    sources: dict[str, dict[str, list[str]]]
    media_sequence: tuple[str, ...]
    excluded: int
    notes: tuple[str, ...]


def _oriented_size(record: Mapping[str, Any]) -> tuple[int, int] | None:
    """The source's displayed size, if `workers/ingest` measured it.

    `oriented_size` is the size AFTER the container's display matrix is applied.
    A portrait phone clip is stored as a landscape raster with a rotation flag,
    and reading the stored raster here would put every reframe crop on its side
    with nothing downstream able to tell -- so this reads the one field that
    already has the turn in it, and nothing else.
    """
    video = record.get("video")
    if not isinstance(video, Mapping):
        return None
    oriented = video.get("oriented_size")
    if not isinstance(oriented, Mapping):
        return None
    width, height = oriented.get("width"), oriented.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _candidates(ctx: StageContext) -> list[_Candidate]:
    """Every video whose proxy pins a rate and a raster, and whose file is here.

    The rate comes from the sidecar's exact time base, not from its float
    `source_rate`. The proxy raster is still read, because it is what the
    geometry falls back to for a record ingest could not measure.
    """
    from memory_engine_video_analysis.proxy import (  # noqa: PLC0415
        ProxyError,
        proxies_in_record,
    )

    out: list[_Candidate] = []
    for media_id in _video_ids(ctx):
        record = ctx.database.get_media(media_id)
        if record is None or not _step_done(record, MOMENT_STEP):
            continue
        source = _source_path(record)
        if source is None:
            continue
        try:
            found = proxies_in_record(record)
            if not found:
                continue
            grid = found[0].proxy_grid
        except ProxyError:
            continue
        width, height = found[0].width, found[0].height
        if not width or not height:
            continue
        oriented = _oriented_size(record)
        out.append(
            _Candidate(
                media_id=media_id,
                rate=grid.rate_float,
                start_value=grid.start_value,
                frame_count=grid.frame_count,
                raster=(width, height),
                geometry=oriented if oriented is not None else (width, height),
                geometry_from=(
                    _PROXY_GEOMETRY if oriented is None else _SOURCE_GEOMETRY
                ),
                path=source,
                label=(record.get("sources") or [{}])[0].get("original_filename"),
                captured_local=_captured_local(record),
            )
        )
    return out


def _captured_local(record: Mapping[str, Any]) -> str | None:
    """The device's wall-clock reading, or None when there is no usable one.

    `MediaRecord.capture.captured_at` is a TimeAssertion precisely so a file
    with no EXIF gets `precision: unknown` rather than a fabricated date, and
    this is the one caller that must respect that: a film ordered by fabricated
    dates is a story told in the wrong order, which is worse than a film that
    admits it does not know the order.
    """
    assertion = ((record.get("capture") or {}).get("captured_at") or {})
    if assertion.get("precision") in (None, "unknown"):
        return None
    local = assertion.get("local")
    return str(local) if local else None


def _select(ctx: StageContext) -> _Selection | None:
    from memory_engine_story import reel as reel_module  # noqa: PLC0415

    candidates = _candidates(ctx)
    if not candidates:
        return None

    # One timeline rate, one target geometry, and one PROVENANCE for that
    # geometry. `render-video` refuses a source at a rate other than the
    # timeline's rather than resampling it, and a source of a different shape
    # would be scaled into the target without anything saying so. The third
    # component is the one that is easy to leave out: a clip whose oriented size
    # ingest measured and a clip that fell back to its proxy raster can land on
    # the same numbers by coincidence, and mixing them would put a measurement
    # and an assumption behind the same declared target.
    groups: dict[tuple[float, tuple[int, int], str], list[_Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(
            (candidate.rate, candidate.geometry, candidate.geometry_from), []
        ).append(candidate)
    # Most sources first, then measured geometry over fallback, then the higher
    # rate, then the wider frame: a stable total order, so the same library
    # plans the same reel on every machine.
    key = max(
        groups,
        key=lambda item: (
            len(groups[item]),
            item[2] == _SOURCE_GEOMETRY,
            item[0],
            item[1][0],
            item[1][1],
        ),
    )
    rate, raster, geometry_from = key
    chosen = sorted(groups[key], key=lambda candidate: candidate.media_id)
    excluded = [
        c for c in candidates if (c.rate, c.geometry, c.geometry_from) != key
    ]
    measured = geometry_from == _SOURCE_GEOMETRY

    notes: list[str] = []
    if excluded:
        notes.append(
            f"{len(excluded)} video(s) are not in these plans because an EDL has "
            "one timeline rate and one target geometry: "
            + ", ".join(
                sorted(
                    {
                        f"{c.rate:g} fps {c.geometry[0]}x{c.geometry[1]}"
                        f" ({c.geometry_from})"
                        for c in excluded
                    }
                )
            )
            + f" against {rate:g} fps {raster[0]}x{raster[1]} ({geometry_from})"
        )
    if measured:
        notes.append(
            f"the render target is {raster[0]}x{raster[1]}, the source's own "
            "MediaRecord.video.oriented_size -- the size AFTER the container's "
            "display matrix is applied, which is the space every normalised "
            "coordinate in this system lives in"
        )
        notes.append(
            "reframe is enabled: the source aspect ratio is measured, so a crop "
            "computed from it frames the picture rather than an assumption about it"
        )
    else:
        notes.append(
            "the render target is the 480p proxy raster: MediaRecord.video is null "
            "for these records, so the source's oriented size is unmeasured and no "
            "larger master can be planned honestly. Re-run ingest to measure it"
        )
        notes.append(
            "reframe is disabled: a landscape-to-vertical crop needs the source "
            "aspect ratio, which is the same missing measurement"
        )
    notes.append(
        "no music: packages/story-engine/music/library.json bundles no audio, so "
        "there is no beat grid, no cut in the reel is beat-locked, and the film "
        "has no bed for its ducking rule to pull down"
    )
    notes.append(
        "ambient is at unity gain with no high-pass and no noise suppression. The "
        "AmbientSettings defaults (-14 dB, 120 Hz, 'light') describe location sound "
        "under a music bed; with no bed the ambient is the whole mix"
    )

    aspect = _reduced(raster)
    media: list[Any] = []
    sources: dict[str, dict[str, list[str]]] = {}
    for index, candidate in enumerate(chosen):
        media.append(
            reel_module.SourceMedia(
                media_ref_id=f"src-{index:03d}",
                media_id=candidate.media_id,
                available_start=float(candidate.start_value),
                available_duration=float(candidate.frame_count),
                aspect_ratio=aspect,
                expected_frame_rate=rate,
                label=candidate.label,
            )
        )
        sources[candidate.media_id] = {"paths": [candidate.path]}

    moments: list[Any] = []
    for candidate in chosen:
        for row in ctx.database.best_moments(media_id=candidate.media_id, limit=200):
            try:
                moments.append(reel_module.moment_from_record(row, rate=rate))
            except (ValueError, KeyError) as error:
                notes.append(f"moment {row.get('moment_id', '?')[:12]} skipped: {error}")
    if not moments:
        return None

    # ALL OR NOTHING. A partial capture order would run some files by the clock
    # and the rest by content hash, and the seam between the two would read as
    # chronology. `plan_film` refuses a partial sequence for the same reason;
    # this is where the decision is made rather than discovered.
    dated = [c for c in chosen if c.captured_local is not None]
    if len(dated) == len(chosen):
        media_sequence = tuple(
            c.media_id
            for c in sorted(chosen, key=lambda c: (c.captured_local or "", c.media_id))
        )
    else:
        media_sequence = ()
        notes.append(
            f"{len(chosen) - len(dated)} of {len(chosen)} sources carry no usable "
            "capture time (MediaRecord.capture.captured_at precision 'unknown'), so "
            "the film is given no chronology and runs its sources in declaration "
            "order. It says so in its own notes"
        )

    return _Selection(
        rate=rate,
        raster=raster,
        aspect=aspect,
        media=tuple(media),
        moments=tuple(moments),
        sources=sources,
        media_sequence=media_sequence,
        excluded=len(excluded),
        notes=tuple(notes),
    )


def _write_plan(
    ctx: StageContext,
    edl: Mapping[str, Any],
    edl_id: str,
    sources: Mapping[str, Any],
    notes: list[str],
) -> Path:
    """Persist one EDL and the source map the renderer needs to open it."""
    errors = sorted(_edl_validator().iter_errors(edl), key=lambda error: list(error.path))
    if errors:
        raise ValueError(
            f"the {edl['kind']} planner produced an EDL the contract rejects: "
            + "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:3])
        )
    path = ctx.workdir / "outputs" / "edl" / f"{edl_id}.json"
    manifest = ctx.workdir / "outputs" / "edl" / f"{edl_id}.sources.json"
    if path.is_file():
        # Same id means the same plan. Rewriting it would move `generated_at`
        # and change the bytes of a file the render job is keyed on, so a
        # re-run would look like new work to anything comparing files.
        notes.append(f"the {edl['kind']} plan {edl_id[:12]} was already on disk")
    else:
        write_json_atomically(path, edl)
    if not manifest.is_file():
        # Written unconditionally when absent, even beside a plan that already
        # exists: the EDL addresses sources by hash and never by path, so
        # without this map the renderer has nothing to open and the render
        # stage fails on a file it could have rewritten.
        write_json_atomically(manifest, dict(sources))
    return path


def _video_clips(edl: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        item
        for track in edl["tracks"]
        if track["kind"] == "video"
        for item in track["items"]
        if item["item_type"] == "clip"
    ]


# --------------------------------------------------------------------- reel --


def _plan_reel(
    ctx: StageContext, selection: _Selection, planned_at: str
) -> _PlanOutcome | None:
    from memory_engine_story import reel as reel_module  # noqa: PLC0415

    rate = selection.rate
    notes: list[str] = []
    request = reel_module.ReelRequest(
        rate=rate,
        target=reel_module.RenderTarget(
            destination="master",
            resolution=selection.raster,
            aspect_ratio=selection.aspect,
            target_duration=float(ctx.settings.reel_seconds) * rate,
            loudness_target_lufs=ctx.settings.reel_loudness_lufs,
        ),
        media=selection.media,
        moments=selection.moments,
        name=f"{ctx.settings.scope} reel",
        reframe=reel_module.ReframeSettings(enabled=measured),
        ambient=reel_module.AmbientSettings(
            default_gain_db=0.0,
            # Unity for speech as well: with no bed to duck, -6 would put every
            # speaking shot BELOW every other one. See the ambient section of
            # the module docstring.
            speech_gain_db=0.0,
            high_pass_hz=None,
            noise_suppression="none",
        ),
        # REQUEST fields, because `plan_reel` is a pure function and reading a
        # clock inside it would make the plan irreproducible. The real instant
        # is safe to pass: `EDL_ID_EXCLUDED` strips `determinism.generated_at`
        # and `validation.validated_at` before the id is digested, so the id
        # stays the render cache key while the plan still records honestly when
        # it was made.
        generated_at=planned_at,
        validated_at=planned_at,
    )
    plan = reel_module.plan_reel(request)
    notes.extend(plan.notes)
    path = _write_plan(ctx, plan.edl, plan.edl_id, selection.sources, notes)

    clips = _video_clips(plan.edl)
    beat_locked = sum(1 for clip in clips if clip.get("beat_lock"))
    counts = {
        "edl_id": plan.edl_id,
        "kind": plan.edl["kind"],
        "rate": rate,
        "resolution": f"{raster[0]}x{raster[1]}",
        "resolution_measured_from": geometry_from,
        "reframe": "enabled" if measured else "disabled (source geometry unmeasured)",
        "clips": len(clips),
        "beat_locked": beat_locked,
        "duration_s": round(plan.duration_frames / rate, 3),
        "moments_available": len(selection.moments),
        "sources": len(selection.media),
        "sources_excluded": selection.excluded,
        "validation": plan.status,
        "word_safe_cuts_certified": 0,
    }
    detail = (
        f"reel EDL {plan.edl_id[:12]}: {len(clips)} clips, "
        f"{plan.duration_frames / rate:.2f}s at {rate:g} fps, "
        f"{raster[0]}x{raster[1]} from the "
        f"{'measured source' if measured else 'proxy raster'}, "
        f"{beat_locked} of {len(clips)} cuts beat-locked, "
        "0 cuts certified word-safe (no transcript backend)"
    )
    return _PlanOutcome(
        kind="reel", path=path, detail=detail, notes=tuple(notes), counts=counts
    )


# --------------------------------------------------------------------- film --


def _plan_film(
    ctx: StageContext, selection: _Selection, planned_at: str
) -> _PlanOutcome | None:
    """The second output, from the same moments and a different cut.

    Nothing here asks for a duration. The reel is told "fifteen seconds" and
    ends on the nearest cut point; a film is as long as the footage it holds,
    and asking for a length would make the planner drop shots to hit a number.
    `FilmRequest.min_film_seconds` is the OTHER direction -- it is what makes
    the plan say "this ran 22 seconds against a 60 second floor for the form"
    instead of quietly presenting a short thing as a film.
    """
    from memory_engine_story import film as film_module  # noqa: PLC0415

    rate = selection.rate
    notes: list[str] = []
    request = film_module.FilmRequest(
        rate=rate,
        target=film_module.RenderTarget(
            destination="master",
            resolution=selection.raster,
            aspect_ratio=selection.aspect,
            loudness_target_lufs=ctx.settings.reel_loudness_lufs,
        ),
        media=selection.media,
        moments=selection.moments,
        media_sequence=selection.media_sequence,
        name=f"{ctx.settings.scope} film",
        # Same two reasons as the reel: the source geometry is unmeasured, and
        # there is no music bed for the ambient defaults to sit under.
        reframe=film_module.ReframeSettings(enabled=False),
        ambient=film_module.AmbientSettings(
            # `plan_film` REFUSES default_gain_db=0 with speech_gain_db=-6, and
            # is right to: it would plan every speaking shot 6 dB under every
            # other one. Same reasoning as the reel above, enforced here.
            default_gain_db=0.0,
            speech_gain_db=0.0,
            high_pass_hz=None,
            noise_suppression="none",
        ),
        # Hard cuts. `plan_film` will place dissolves at act boundaries, and
        # `workers/render-video` refuses them over unmuted ambient beds on
        # contracts#52 — the contract never says whether the beds cross-fade
        # with the picture or hard-cut at the cut point. Asking for one here
        # would produce a film nothing in this repository can render.
        act_transition_frames=0,
        generated_at=planned_at,
        validated_at=planned_at,
    )
    plan = film_module.plan_film(request)
    notes.extend(plan.notes)
    path = _write_plan(ctx, plan.edl, plan.edl_id, selection.sources, notes)

    clips = _video_clips(plan.edl)
    arc = plan.edl["story_arc"]
    acts = {
        act["act_id"]: len(act["beats"][0]["satisfied_by_clip_ids"])
        for act in arc["acts"]
    }
    counts = {
        "edl_id": plan.edl_id,
        "kind": plan.edl["kind"],
        "rate": rate,
        "resolution": f"{selection.raster[0]}x{selection.raster[1]}",
        "clips": len(clips),
        "duration_s": round(plan.duration_frames / rate, 3),
        "arc_template": arc["template"],
        "acts": acts,
        "shot_seconds": [round(frames / rate, 3) for frames in plan.shot_frames],
        "pacing_spread": plan.pacing_spread,
        "window_limited_shots": plan.window_limited,
        "l_cuts": plan.l_cuts,
        "chronology": "capture_time" if selection.media_sequence else "declaration_order",
        "moments_available": len(selection.moments),
        "sources": len(selection.media),
        "sources_excluded": selection.excluded,
        "validation": plan.status,
        "word_safe_cuts_certified": 0,
    }
    detail = (
        f"film EDL {plan.edl_id[:12]}: {len(clips)} shots in 3 acts, "
        f"{plan.duration_frames / rate:.2f}s at {rate:g} fps, "
        f"holds {min(plan.shot_frames) / rate:.2f}-{max(plan.shot_frames) / rate:.2f}s "
        f"(pacing spread {plan.pacing_spread:.2f}, "
        f"{plan.window_limited} of {len(clips)} window-limited), "
        f"{plan.l_cuts} L-cuts, "
        "0 cuts certified word-safe (no transcript backend)"
    )
    return _PlanOutcome(
        kind="film", path=path, detail=detail, notes=tuple(notes), counts=counts
    )


def _reduced(raster: tuple[int, int]) -> tuple[int, int]:
    divisor = math.gcd(raster[0], raster[1])
    return raster[0] // divisor, raster[1] // divisor
