"""`cluster_events` + `plan_album` -> a validated AlbumSpec.

Every decision here belongs to `packages/album-engine`, which does the
clustering, the selection under diversity constraints, the millimetre layout
and the print validation. This file supplies inputs and assembles the contract
object. It contains no layout arithmetic and no selection policy, on purpose:
those exist once, and a second copy inside a service is how a book that passes
one validator fails the other.

THE REFUSAL THAT MATTERS

A book is printed once. A validator that passes a bad page produces an object
in the post rather than a bug report. So this stage refuses in three places:

  * upstream ANALYSIS must be COMPLETED. Not "mostly done", not "classical
    quality only". Selection's diversity constraints run on embeddings and face
    counts, so a library without them yields a book chosen on sharpness alone
    -- which would look entirely plausible and be worthless. If analysis is
    blocked, so is this.
  * only records whose `processing.state` is `analyzed` become candidates.
  * the AlbumSpec is written ONLY when the print validator returns `pass`. A
    failing report is still written, to a `.rejected.json` beside it, because
    "no silent anything" cuts both ways -- a person needs to read why.

IDEMPOTENCE

`album_id` is a BLAKE3 over the planner's inputs: the selected ids in order,
the vendor profile, the layout parameters and the planner version. Re-running
over an unchanged library reproduces the id, finds the completed job and does
not re-plan, so the file on disk is not even rewritten -- which is what keeps
`generated_at` from making two identical albums look different.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..events import utc_now
from ..ids import digest_of
from ..jobstore import build_job
from .base import (
    StageContext,
    StageResult,
    StageStatus,
    blocked_by,
    write_json_atomically,
)

STAGE = "album"
PLANNER = "album-planner"
PLANNER_VERSION = "0.1.0"
SEED = 0

_VENDOR_PROFILE_DIR = Path("packages/album-engine/vendor_profiles")
_TIMELINE_PRECISIONS = frozenset({"second", "minute", "hour", "day"})


def _vendor_profile(ctx: StageContext) -> dict[str, Any] | None:
    path = ctx.repo_root / _VENDOR_PROFILE_DIR / f"{ctx.settings.vendor_profile}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


_PAGE = 1000
_ALBUM_KINDS = ("image", "live_photo", "motion_photo")


def _analysed_records(ctx: StageContext) -> list[dict[str, Any]]:
    """Fully analysed, non-excluded dedupe primaries.

    Asked through `list_media` rather than with SQL of our own. The predicate
    for "primary" is `dedupe_group_id IS NULL OR is_dedupe_primary = 1`, not
    `is_dedupe_primary = 1`: the column is `NOT NULL DEFAULT 0`, so a photo
    with no duplicates at all reads as `0` and a hand-rolled query drops the
    entire library while looking perfectly reasonable. That is exactly the
    class of bug the MediaQuery contract warns about -- a second reader
    reimplementing the query layer's rules and getting one of them wrong -- so
    the rule is used from where it lives instead.
    """
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = ctx.database.list_media(
            primaries_only=True, include_excluded=False, limit=_PAGE, offset=offset
        )
        if not page:
            break
        for summary in page:
            if summary.processing_state != "analyzed" or summary.kind not in _ALBUM_KINDS:
                continue
            record = ctx.database.get_media(summary.media_id)
            if record is not None:
                records.append(record)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return records


def run(ctx: StageContext) -> StageResult:
    upstream = ctx.require("ingest", "analysis", "ranking")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    profile = _vendor_profile(ctx)
    if profile is None:
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=f"no vendor profile named {ctx.settings.vendor_profile!r}",
        )

    records = _analysed_records(ctx)
    if not records:
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail=(
                "no record in the library has finished analysis, so there is nothing "
                "to choose from"
            ),
        )

    from memory_engine_album.clustering import cluster_events  # noqa: PLC0415

    from .ranking import scores_for  # noqa: PLC0415

    scores = scores_for(ctx, records)
    by_id = {record["media_id"]: record for record in records}

    clusters = cluster_events([_event_item(ctx, record) for record in records])
    dated = [cluster for cluster in clusters if cluster.kind == "dated"]
    if not dated:
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail=(
                f"{len(clusters)} cluster(s) found, none of them dated; an album needs a "
                "chronology and undated media are deliberately not given a fabricated one"
            ),
            counts={"clusters": len(clusters)},
        )
    target = max(dated, key=lambda cluster: cluster.size)

    from memory_engine_album.selection import (  # noqa: PLC0415
        SelectionPolicy,
        candidate_from_media_record,
        select,
    )

    from .analysis import EMBEDDING_SPACES  # noqa: PLC0415

    space = EMBEDDING_SPACES.get(ctx.settings.embedding_model, (None, 0))[0]
    candidates = []
    for media_id in target.media_ids:
        record = by_id.get(media_id)
        score = scores.get(media_id)
        if record is None or score is None:
            continue
        embedding = (
            ctx.database.vectors.get("media", media_id, space) if space else None
        )
        candidates.append(
            candidate_from_media_record(record, score, embedding=embedding)
        )
    if not candidates:
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="the largest event yielded no scoreable candidates",
        )

    wanted = min(ctx.settings.album_target_count, len(candidates))
    policy = SelectionPolicy()
    selection = select(candidates, wanted, policy=policy)
    if not selection.selected:
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="the selector rejected every candidate",
            counts={
                "candidates": len(candidates),
                "rejections": [
                    f"{rejection.reason}: {rejection.detail}"
                    for rejection in selection.rejected[:5]
                ],
            },
        )

    inputs_digest = digest_of(
        {
            "planner": PLANNER,
            "planner_version": PLANNER_VERSION,
            "seed": SEED,
            "selected": list(selection.selected),
            "vendor_profile": profile,
            "photos_per_page": ctx.settings.album_photos_per_page,
            "target_count": wanted,
        }
    )
    job = build_job(
        job_type="plan_album",
        scope=ctx.settings.scope,
        params={
            "planner": PLANNER,
            "planner_version": PLANNER_VERSION,
            "vendor_profile": ctx.settings.vendor_profile,
            "inputs_digest": inputs_digest,
        },
        media_ids=list(selection.selected),
        priority=300,
    )
    job = ctx.jobs.get(job["job_id"]) or ctx.jobs.ensure(job)
    output_path = ctx.workdir / "outputs" / "album" / f"{inputs_digest}.json"
    if job["state"]["status"] == "completed" and output_path.is_file():
        spec = json.loads(output_path.read_text(encoding="utf-8"))
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="the same album was already planned and validated",
            job_id=job["job_id"],
            counts={"pages": len(spec["pages"]), "selected": len(selection.selected)},
            outputs=(str(output_path),),
        )

    job = ctx.jobs.begin(job)
    ctx.reporter.event(
        STAGE,
        "stage_start",
        f"{len(selection.selected)} of {len(candidates)} photos from the largest event",
        event_size=target.size,
    )

    from memory_engine_album.layout import (  # noqa: PLC0415
        LayoutError,
        PageRequest,
        layout_album,
    )

    photos = [_photo(by_id[media_id]) for media_id in selection.selected]
    missing_size = [photo.media_id for photo in photos if photo.pixel_width <= 0]
    if missing_size:
        ctx.jobs.fail(
            job, code="validation_failed",
            message="a selected photo has no oriented pixel size", retryable=False,
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=(
                f"{len(missing_size)} selected photos carry no oriented pixel size, so "
                "their printed DPI cannot be measured and the book cannot be validated"
            ),
            job_id=job["job_id"],
        )

    per_page = max(1, ctx.settings.album_photos_per_page)
    requests = [
        PageRequest(photos=tuple(photos[start : start + per_page]))
        for start in range(0, len(photos), per_page)
    ]
    try:
        pages = layout_album(requests, profile, cover=photos[0])
    except LayoutError as error:
        ctx.jobs.fail(job, code="validation_failed", message="layout failed",
                      retryable=False)
        return StageResult(
            stage=STAGE, status=StageStatus.FAILED,
            detail=f"layout refused this album: {error}", job_id=job["job_id"],
        )

    from memory_engine_album.validator import (  # noqa: PLC0415
        DocumentColor,
        SourceImage,
        validate_album,
    )

    sources = {
        photo.media_id: SourceImage(photo.pixel_width, photo.pixel_height)
        for photo in photos
    }
    document_color = DocumentColor(
        icc_name=profile["color_profile"]["icc_name"],
        intent=profile["color_profile"]["intent"],
        icc_hash=profile["color_profile"].get("icc_hash"),
    )
    validated_at = utc_now()
    report = validate_album(
        pages=pages,
        vendor_profile=profile,
        sources=sources,
        document_color=document_color,
        validated_at=validated_at,
    )

    spec = {
        "schema_version": "v0",
        "album_id": inputs_digest,
        "title": _title(target),
        "subtitle": None,
        "event": None,
        "vendor_profile": profile,
        "pages": pages,
        "selection": selection.to_selection_report(),
        "spread_harmony": None,
        "determinism": {
            "planner": PLANNER,
            "planner_version": PLANNER_VERSION,
            "seed": SEED,
            "inputs_digest": inputs_digest,
            "generated_at": validated_at,
        },
        "validation": report.to_dict(),
        "review": None,
    }

    if report.status != "pass":
        rejected = ctx.workdir / "outputs" / "album" / f"{inputs_digest}.rejected.json"
        write_json_atomically(rejected, spec)
        ctx.jobs.fail(
            job, code="validation_failed",
            message="the print validator refused this album", retryable=False,
        )
        blocking = [finding.detail for finding in report.blocking][:5]
        ctx.reporter.event(
            STAGE, "stage_failed", "print validation failed", errors=report.error_count
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=(
                f"the print validator refused this album ({report.error_count} errors): "
                + "; ".join(blocking)
            ),
            job_id=job["job_id"],
            outputs=(str(rejected),),
            counts={"pages": len(pages), "errors": report.error_count,
                    "warnings": report.warning_count},
        )

    _validate_spec(spec)
    write_json_atomically(output_path, spec)
    ctx.jobs.complete(
        job,
        outputs=[
            {
                "kind": "album_spec",
                "id": inputs_digest,
                "path": str(output_path),
                "byte_size": output_path.stat().st_size,
                "produced_at": validated_at,
            }
        ],
    )
    counts = {
        "pages": len(pages),
        "selected": len(selection.selected),
        "candidates": len(candidates),
        "warnings": report.warning_count,
    }
    ctx.reporter.event(STAGE, "stage_done", "album planned and validated", **counts)
    return StageResult(
        stage=STAGE, status=StageStatus.COMPLETED,
        detail=f"{len(pages)}-page album, print validation passed",
        job_id=job["job_id"], counts=counts, outputs=(str(output_path),),
    )


# ------------------------------------------------------------------ inputs --


def _event_item(ctx: StageContext, record: Mapping[str, Any]) -> Any:
    from memory_engine_album.clustering import EventItem  # noqa: PLC0415

    assertion = (record.get("capture") or {}).get("captured_at") or {}
    gps = (record.get("capture") or {}).get("gps") or {}
    utc = assertion.get("utc")
    captured_at = _epoch(utc) if assertion.get("precision") in _TIMELINE_PRECISIONS else None
    return EventItem(
        media_id=record["media_id"],
        captured_at=captured_at,
        time_precision=assertion.get("precision") or "unknown",
        time_confidence=float(assertion.get("confidence", 0.0) or 0.0),
        time_source=assertion.get("source"),
        utc_offset_minutes=assertion.get("utc_offset_minutes"),
        latitude=gps.get("latitude"),
        longitude=gps.get("longitude"),
        # Embeddings are deliberately not loaded here. Clustering uses them
        # only to attach UNDATED media to a nearby cluster, and this stage
        # already refuses to build an album out of undated media, so paying to
        # load the whole library's vectors would buy nothing.
        embedding=None,
    )


def _epoch(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    from datetime import datetime  # noqa: PLC0415

    try:
        return datetime.fromisoformat(timestamp).timestamp()
    except ValueError:
        return None


def _photo(record: Mapping[str, Any]) -> Any:
    from memory_engine_album.layout import Photo  # noqa: PLC0415

    size = (record.get("image") or {}).get("oriented_size") or {}
    return Photo(
        media_id=record["media_id"],
        pixel_width=int(size.get("width") or 0),
        pixel_height=int(size.get("height") or 0),
        # Face boxes are not carried on the MediaRecord -- they live on
        # FaceRecords, which `cluster_faces` would write and which this
        # pipeline does not run. So face-safe placement has nothing to work
        # with here. The validator's face checks pass vacuously on an empty
        # face set, which is honest (no face is known to be in the trim zone)
        # and is NOT the same as "checked and safe". Wiring faces into layout
        # is blocked on the face-identity stage, and is called out in the
        # README rather than approximated from a detection count.
        faces=(),
        salience=None,
    )


def _title(cluster: Any) -> str:
    from datetime import UTC, datetime  # noqa: PLC0415

    if cluster.start_utc is None:
        return "Album"
    return datetime.fromtimestamp(cluster.start_utc, UTC).strftime("%B %Y")


def _validate_spec(spec: Mapping[str, Any]) -> None:
    """Validate the assembled spec against the contract before it is written.

    render-print reads this file and enforces its own gate against it; a spec
    that does not validate would be rejected there for a reason that has
    nothing to do with the book. Catching it here names the real problem.
    """
    validator = _album_validator()
    errors = sorted(validator.iter_errors(spec), key=lambda error: list(error.path))
    if errors:
        rendered = "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
        raise ValueError(
            f"the assembled AlbumSpec does not satisfy the contract: {rendered}"
        )


_ALBUM_VALIDATOR: Any = None


def _album_validator() -> Any:
    global _ALBUM_VALIDATOR
    if _ALBUM_VALIDATOR is not None:
        return _ALBUM_VALIDATOR
    from jsonschema import Draft202012Validator  # noqa: PLC0415
    from referencing import Registry, Resource  # noqa: PLC0415

    schema_dir = Path(__file__).resolve().parents[4] / "contracts" / "schemas"
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(schema_dir.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    _ALBUM_VALIDATOR = Draft202012Validator(
        documents["album-spec.schema.json"], registry=registry
    )
    return _ALBUM_VALIDATOR
