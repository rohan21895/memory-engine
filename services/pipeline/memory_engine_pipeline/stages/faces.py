"""`cluster_faces` -> person hypotheses -> assignments -> the review queue.

WHY THIS IS A STAGE OF ITS OWN AND NOT PART OF ANALYSIS

Detection and embedding are per photo: kill the process at photo 4,000 and the
first 4,000 are done. Clustering is not. A person's faces are spread across
every event they ever appeared in, so the answer for any one face depends on
every other face in the library, and there is no partial result to checkpoint.
Running it inside the per-record loop would recompute the whole library once per
photo; running it after is the only shape that is both correct and finite.

WHAT IT PRODUCES, AND WHAT IT DELIBERATELY DOES NOT

  * A cluster per person hypothesis, written into `FaceRecord.cluster`.
  * A `PersonAssignment` per face, written into `FaceRecord.identity`.
  * A review queue, written to `<workdir>/outputs/faces/review-queue.json`.

  * ZERO eligible faces, today and until somebody measures the precision of
    these weights. `assign_identities` will not emit an `auto_high_confidence`
    assignment while its calibrator reports `calibrated = False`, no
    `FittedCalibrator` exists (it cannot be constructed without a measured
    precision, 2,000 labelled pairs and the digest of the evaluation set), and
    no `Person` has been enrolled because nothing in this repository can enrol
    one yet.

    So the honest report is "N faces, 0 eligible for automated output, N
    awaiting review", and that is what this stage prints. It is not a
    degradation to be worked around. CLAUDE.md rule 5 -- a wrong person in a
    family album is a catastrophic failure -- is exactly the reason an
    unmeasured automated path stays shut.

WHAT THE ALBUM GETS OUT OF THIS RUN, WHICH IS NOT NOTHING

Face SAFETY, which needs no identity at all. Every detected face above the
detector floor now has a stored rectangle, so album-engine's trim-zone and
gutter checks have something to check. Before this stage existed the album was
planned against an empty face set and those checks passed vacuously -- "no face
is known to be in the trim zone", which is honest and is not the same as
"checked and safe".

The two are wired separately on purpose. `records.face_boxes_for_layout` takes
no assignment argument, so a child whose parent has not consented to labelling
is still protected from the guillotine.
"""

from __future__ import annotations

import json
import math
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..events import utc_now
from ..ids import blake3_hex, digest_of
from ..jobstore import build_job
from .base import (
    StageContext,
    StageResult,
    StageStatus,
    blocked_by,
    write_json_atomically,
)

STAGE = "faces"
JOB_TYPE = "cluster_faces"
PAGE = 500


def run(ctx: StageContext) -> StageResult:
    upstream = ctx.require("ingest", "analysis")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    from .. import faceidentity  # noqa: PLC0415

    records = _all_faces(ctx)
    if not records:
        return StageResult(
            stage=STAGE,
            status=StageStatus.COMPLETED,
            detail="no face was detected anywhere in this library",
            counts={"faces": 0},
        )

    backfilled = _backfill_face_sharpness(ctx, records)
    if backfilled:
        ctx.reporter.event(
            STAGE, "note", f"measured face quality for {backfilled} face(s)"
        )

    from memory_engine_face.clustering import FaceObservation  # noqa: PLC0415
    from memory_engine_face.embeddings import EmbeddingError  # noqa: PLC0415
    from memory_engine_face.identity import FaceContext  # noqa: PLC0415
    from memory_engine_face.records import (  # noqa: PLC0415
        detected_face_from_record,
        face_context_from_record,
    )

    embeddings: dict[str, Any] = {}
    embedding_inputs: list[dict[str, Any]] = []
    unreadable: list[str] = []
    spaces: set[str] = set()
    for record in records:
        reference = record.get("embedding")
        if not reference:
            embedding_inputs.append(_embedding_input(record, None, None))
            continue
        space = reference.get("space")
        values = ctx.database.vectors.get(
            "face", reference.get("index_key") or record["face_id"], space
        )
        if values is None:
            # The record claims a vector the index does not hold. That is a
            # missing embedding, not a zero one, and it must not be filled in:
            # a face with no vector goes to review, which is where a face whose
            # vector was lost belongs too.
            unreadable.append(record["face_id"])
            embedding_inputs.append(_embedding_input(record, reference, None))
            continue
        embedding_inputs.append(_embedding_input(record, reference, values))
        try:
            # The record's OWN space, never a constant. See
            # `faceidentity.embedding_of`: stamping one space on every vector
            # disables the only guard that stops two incomparable vectors
            # producing a plausible distance.
            embeddings[record["face_id"]] = faceidentity.embedding_of(
                record["face_id"], values, space=space
            )
            spaces.add(space)
        except EmbeddingError as error:
            # A stored vector that is not unit-norm, holds a NaN, or names a
            # space this repository does not define, is a producer bug.
            # Clustering it would produce plausible distances.
            ctx.reporter.event(
                STAGE, "note", f"{record['face_id'][:12]}: {error}"
            )
            unreadable.append(record["face_id"])

    if len(spaces) > 1:
        # Clustering across spaces is arithmetic on two different coordinate
        # systems. `FaceEmbedding` would raise SpaceMismatch somewhere in the
        # middle of the pass; saying it here names both spaces and the remedy,
        # and leaves the library untouched rather than half-rewritten.
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=(
                f"this library holds face vectors in {len(spaces)} different spaces "
                f"({', '.join(sorted(spaces))}). Two vectors may only be compared when "
                "their space matches exactly, so there is no distance between them to "
                "cluster on. Re-run with --reanalyze-faces so every face is embedded "
                "by one model"
            ),
            counts={"faces": len(records), "embedded": len(embeddings)},
        )

    from .. import modelconfigs  # noqa: PLC0415

    try:
        recognition_pin = modelconfigs.registry_pin(
            ctx.repo_root, ctx.settings.face_embedding_model
        )
    except modelconfigs.ModelConfigError as error:
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=str(error),
            counts={"faces": len(records), "embedded": len(embeddings)},
        )

    embedding_set_digest = digest_of(
        {"face_embeddings": sorted(embedding_inputs, key=lambda item: item["face_id"])}
    )
    run_id = _run_id(
        ctx,
        records,
        spaces=spaces,
        embedding_set_digest=embedding_set_digest,
        recognition_pin=recognition_pin,
    )
    job = build_job(
        job_type=JOB_TYPE,
        scope=ctx.settings.scope,
        params={
            "clustering_run_id": run_id,
            "merge_threshold": faceidentity.MERGE_THRESHOLD,
            # The spaces the vectors were ACTUALLY found in, not the space the
            # pipeline expects them to be in. Re-embedding a library with a
            # different recognition model leaves the face ids untouched (they
            # address the detector), so without this the job identity would be
            # unchanged and this stage would skip -- reporting the previous
            # model's clusters as the answer for the new one's vectors.
            "spaces": sorted(spaces),
            "faces": len(records),
            "embedded": len(embeddings),
            "embedding_set_digest": embedding_set_digest,
            "model_pins": [recognition_pin],
        },
        models=[recognition_pin],
        priority=350,
    )
    job = ctx.jobs.get(job["job_id"]) or ctx.jobs.ensure(job)
    queue_path = ctx.workdir / "outputs" / "faces" / "review-queue.json"
    if job["state"]["status"] == "completed" and queue_path.is_file():
        # The job id is a digest over the face set, the merge threshold and the
        # space, so a completed job means this exact library was clustered with
        # this exact configuration. Re-deriving it would produce identical
        # records with a new `updated_at` on every one of them, which is the
        # kind of churn that makes "what changed?" unanswerable.
        return StageResult(
            stage=STAGE,
            status=StageStatus.COMPLETED,
            detail="this face set was already clustered and assigned",
            job_id=job["job_id"],
            counts={"faces": len(records), "embedded": len(embeddings)},
            outputs=(str(queue_path),),
        )

    job = ctx.jobs.begin(job)
    ctx.reporter.event(
        STAGE,
        "stage_start",
        f"{len(records):,} faces, {len(embeddings):,} of them embedded",
    )

    clustering = faceidentity.cluster(
        [
            FaceObservation(
                face_id=record["face_id"],
                embedding=embeddings.get(record["face_id"]),
                quality=(
                    (record.get("attributes") or {}).get("quality") or {}
                ).get("value"),
            )
            for record in records
        ],
        run_id=run_id,
    )

    now = utc_now()
    contexts = []
    for record in records:
        fields = face_context_from_record(record)
        membership = clustering.membership_of(record["face_id"])
        contexts.append(
            FaceContext(
                face_id=fields["face_id"],
                embedding=embeddings.get(record["face_id"]),
                quality=fields["quality"],
                yaw_deg=fields["yaw_deg"],
                # Not `fields["minor_status"]` directly: a stored
                # `confirmed_minor` carries its consent in the record, while
                # the assignment reads consent from a gallery that is empty
                # because nothing enrols yet. See
                # `faceidentity.assignable_minor_status` -- the substitution is
                # strictly conservative, and the stored envelope is written
                # back verbatim below.
                minor_status=faceidentity.assignable_minor_status(
                    record.get("sensitive")
                ),
                cluster_is_noise=bool(membership.is_noise) if membership else False,
            )
        )
    assignments = faceidentity.assign(contexts, now=now)

    by_id = {record["face_id"]: record for record in records}
    eligible = 0
    for assignment in assignments:
        record = by_id[assignment.face_id]
        rewritten = faceidentity.restore_sensitive(
            faceidentity.record_for(
                detected_face_from_record(record),
                assignment,
                membership=clustering.membership_of(assignment.face_id),
                created_at=record.get("created_at") or now,
                updated_at=now,
            ),
            record.get("sensitive"),
        )
        ctx.database.put_face(rewritten)
        if assignment.eligible_for_automated_output:
            eligible += 1

    _refresh_media_summaries(ctx, assignments, by_id)

    from memory_engine_face.review import build_review_queue  # noqa: PLC0415

    queue = build_review_queue(
        assignments,
        thresholds=faceidentity.THRESHOLDS,
        clustering=clustering,
        embeddings=embeddings,
    )
    write_json_atomically(queue_path, _queue_payload(queue, run_id=run_id, now=now))

    counts = {
        "faces": len(records),
        "embedded": len(embeddings),
        "without_embedding": len(records) - len(embeddings),
        "unreadable_embeddings": len(unreadable),
        "clusters": len(clustering.clusters),
        "gallery_people": 0,
        "eligible_for_automated_output": eligible,
        "awaiting_review": len(records) - eligible,
        "review_questions": len(queue),
        "embedding_set_digest": embedding_set_digest,
    }
    # No JobSpec outputs are declared. The contract's JobOutput.kind has no
    # value for a review queue -- it is not a record of anything, because
    # answering a question is what produces the record -- and the FaceRecords
    # this stage does write are addressed by their own ids in the database
    # rather than as a list of thousands of entries on a job row. Inventing a
    # kind would be a contracts change for a file whose path is already
    # reported on the stage result and in run.json.
    ctx.jobs.complete(job)
    detail = (
        f"{len(records)} faces, {eligible} eligible for automated output, "
        f"{len(records) - eligible} awaiting review"
    )
    if eligible == 0:
        # Said every run, in plain words, because a reader who sees "0
        # eligible" and no explanation reasonably concludes something is
        # broken. Nothing is: there is no calibration and no enrolled person,
        # and until there is, naming anybody automatically is the failure mode.
        detail += (
            " (no calibrated threshold and no enrolled person, so no face may be "
            "named unattended)"
        )
    ctx.reporter.event(STAGE, "stage_done", detail, **counts)
    return StageResult(
        stage=STAGE,
        status=StageStatus.COMPLETED,
        detail=detail,
        job_id=job["job_id"],
        counts=counts,
        outputs=(str(queue_path),),
    )


#: Fusion formula version for `attributes.quality`. Bumped when the formula
#: changes so the backfill recomputes every face rather than a silent
#: redefinition riding on old numbers. 1-1-0: sharpness x visibility.
#: 1-2-0: adds `attributes.head_sharpness` (same fusion formula).
#: 1-3-0: adds per-face expression (`attributes.eyes_open`, `attributes.smile`)
#: for significant faces, and replaces the MAX face-quality rollup with
#: `memory_engine_ranking.fusion.aggregate_face_quality`.
_FACE_QUALITY_RUN_ID = "face-quality-1-3-0"

#: The head region is the face box grown by this factor about its centre:
#: wide enough to take in hair and shoulders, the parts that smear first when
#: the subject is moving. A sharp face inside a motion-blurred head is a
#: mid-gesture frame -- the face gate cannot see it, this one exists to.
_HEAD_BOX_SCALE = 1.8

#: The expression crop is the face box grown by this factor about its centre:
#: a TIGHT face crop with just enough margin that eyes near the box edge are
#: not sliced -- the expression-head axes were written for tight face crops,
#: not for head-and-shoulders regions.
_EXPRESSION_BOX_SCALE = 1.3

#: FROZEN contrast -> Confidence mapping for `attributes.eyes_open` and
#: `attributes.smile`: round(min(1.0, max(0.0, 0.5 + contrast * 5.0)), 6).
#: Any monotone map works -- selection ranks percentiles -- but this one is
#: fixed so stored values stay comparable across runs.
PER_FACE_CONTRAST_SCALE = 5.0

#: The expression-head axes scored per face, and the FaceAttributes slot each
#: one is stored into. The `face_*` axes are the ones written for tight face
#: crops; the unprefixed `smile`/`awake` axes are whole-photo axes.
_EXPRESSION_AXES = (("eyes_open", "face_eyes_open"), ("smile", "face_smile"))

#: Where the built expression head lives, relative to the repo root. Mirrors
#: `stages.album._load_expression_head` -- replicated rather than imported so
#: this stage does not couple to a module another agent is editing.
_EXPRESSION_HEAD_PATH = (
    "models", "weights", "expression-head", "expression-siglip-head.v1.json"
)

#: Below this many pixels on either edge, a crop carries no expression
#: evidence worth embedding; the face keeps null expression attributes.
_EXPRESSION_MIN_EDGE_PX = 8


def _grown_box(box: dict[str, Any], scale: float) -> dict[str, float]:
    """The face bbox grown `scale`x about its centre, clamped to the frame."""
    cx = float(box["x"]) + float(box["w"]) / 2.0
    cy = float(box["y"]) + float(box["h"]) / 2.0
    w = min(1.0, float(box["w"]) * scale)
    h = min(1.0, float(box["h"]) * scale)
    x = min(max(cx - w / 2.0, 0.0), 1.0 - w)
    y = min(max(cy - h / 2.0, 0.0), 1.0 - h)
    return {"x": x, "y": y, "w": w, "h": h}


def _head_box(box: dict[str, Any]) -> dict[str, float]:
    """The face bbox grown `_HEAD_BOX_SCALE`x about its centre, clamped."""
    return _grown_box(box, _HEAD_BOX_SCALE)


def _expression_confidence(contrast: float) -> float:
    """The frozen contrast -> Confidence mapping. See PER_FACE_CONTRAST_SCALE."""
    return round(
        min(1.0, max(0.0, 0.5 + float(contrast) * PER_FACE_CONTRAST_SCALE)), 6
    )

#: A face below this fraction of the frame is background; it neither lifts a
#: photo's face_quality nor should its blur condemn one. Mirrors the album
#: stage's significance floor.
_FACE_SUMMARY_MIN_AREA = 0.02


def _expression_crops(
    path: Path, boxes: Sequence[Mapping[str, float]]
) -> list[Any]:
    """RGB face crops from one image, in box order. None = too small to crop.

    `boxes` are normalised {x, y, w, h} against the ORIENTED frame -- exactly
    the convention `classical.measure_face_sharpness` reads -- so the stored
    image is EXIF-transposed before any pixel coordinate is computed. Each box
    is grown `_EXPRESSION_BOX_SCALE`x about its centre and clamped: a tight
    face crop with margin enough that eyes at the box edge survive.
    """
    from PIL import Image, ImageOps  # noqa: PLC0415

    with Image.open(path) as handle:
        oriented = ImageOps.exif_transpose(handle).convert("RGB")
    width, height = oriented.size
    crops: list[Any] = []
    for box in boxes:
        grown = _grown_box(box, _EXPRESSION_BOX_SCALE)
        x0 = max(0, int(grown["x"] * width))
        y0 = max(0, int(grown["y"] * height))
        x1 = min(width, int((grown["x"] + grown["w"]) * width))
        y1 = min(height, int((grown["y"] + grown["h"]) * height))
        if x1 - x0 < _EXPRESSION_MIN_EDGE_PX or y1 - y0 < _EXPRESSION_MIN_EDGE_PX:
            crops.append(None)
            continue
        crops.append(oriented.crop((x0, y0, x1, y1)))
    return crops


def _expression_source_path(ctx: StageContext, media_id: str) -> Path | None:
    """The image the expression crop is cut from: the ORIGINAL when it can be
    resolved, else the thumbnail proxy.

    The original is preferred because the median significant face is small: on
    a 4000px+ original its crop is still hundreds of pixels, while the same
    crop from a 512px thumbnail is a few dozen -- measurably noisier evidence.
    This is a read of the source file for local measurement only; nothing is
    copied and nothing leaves the device.
    """
    record = ctx.database.get_media(media_id)
    for source in (record or {}).get("sources") or []:
        path = source.get("path")
        if path and Path(path).is_file():
            return Path(path)
    proxies = ctx.database.proxies_for_media(media_id, kind="thumbnail_512")
    path = proxies[0].get("path") if proxies else None
    if path and Path(path).is_file():
        return Path(path)
    return None


def _needs_expression(face: dict[str, Any]) -> bool:
    """True for a significant face whose expression is not yet stored.

    Insignificant faces (< `_FACE_SUMMARY_MIN_AREA`) keep null expression
    attributes on purpose: their crops are noise, not evidence, and the
    rollup never reads them.
    """
    if (face["detection"].get("face_area_ratio") or 0.0) < _FACE_SUMMARY_MIN_AREA:
        return False
    attributes = face.get("attributes") or {}
    return attributes.get("eyes_open") is None or attributes.get("smile") is None


class _ExpressionScorer:
    """SigLIP embeddings for tight face crops, scored on the head's face axes.

    Unavailability is a STATE here, not an exception: the ml host being down,
    the head artifact missing, or the head lacking the face axes each produce
    one WARNING and a scorer that answers nothing -- the backfill still writes
    sharpness and every face keeps null eyes_open/smile. That is deliberate
    degradation, not silence: the warning names the reason every time.
    """

    def __init__(self, ctx: StageContext) -> None:
        self._ctx = ctx
        self._client: Any = None
        self._axes: dict[str, Any] | None = None
        self._prepared = False
        self._disabled = False

    def _disable(self, reason: str) -> None:
        self._disabled = True
        self._ctx.reporter.event(
            STAGE,
            "warning",
            f"face expression scoring is unavailable ({reason}); "
            "eyes_open/smile stay null and sharpness is still written",
        )

    def available(self) -> bool:
        if not self._prepared:
            self._prepared = True
            self._prepare()
        return not self._disabled

    def _prepare(self) -> None:
        from .. import mlruntime  # noqa: PLC0415
        from .analysis import EMBEDDING_SPACES  # noqa: PLC0415

        settings = self._ctx.settings
        space = EMBEDDING_SPACES.get(settings.embedding_model, (None, 0))[0]
        if space is None:
            self._disable(f"no vector space for {settings.embedding_model!r}")
            return

        # Mirrors stages.album._load_expression_head: a built, gitignored
        # artifact; absence is a working configuration, not an error.
        path = self._ctx.repo_root.joinpath(*_EXPRESSION_HEAD_PATH)
        try:
            head = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._disable(f"no readable expression head at {path.name}")
            return
        if head.get("space") != space:
            self._disable(
                f"the expression head was built for space {head.get('space')!r}, "
                f"not {space!r}"
            )
            return
        axes = {
            axis.get("name"): axis
            for axis in head.get("axes") or []
            if axis.get("name")
        }
        missing = [name for _, name in _EXPRESSION_AXES if name not in axes]
        if missing:
            self._disable(f"the expression head lacks axes {missing}")
            return

        try:
            status = mlruntime.probe(
                endpoint=settings.ml_runtime_endpoint,
                required_models=[settings.embedding_model],
                timeout_s=settings.ml_runtime_timeout_s,
            )
        except Exception as error:  # noqa: BLE001 - degradation, never a crash
            self._disable(f"probing the ml host failed: {error}")
            return
        if not status.available:
            self._disable(status.detail)
            return
        try:
            self._client = mlruntime.MlRuntimeClient(
                settings.ml_runtime_endpoint, expected_pins=status.model_pins
            )
        except Exception as error:  # noqa: BLE001 - degradation, never a crash
            self._disable(f"the ml client could not be built: {error}")
            return
        self._axes = {name: axes[name] for _, name in _EXPRESSION_AXES}

    def score(self, crops: Mapping[str, Any]) -> dict[str, dict[str, float]]:
        """face_id -> {"eyes_open": Confidence, "smile": Confidence}.

        A face missing from the answer stays null; a transport failure
        disables the scorer for the rest of the run (the host caches request
        ids, so a blind retry of the same payload is not an option, and a
        host that dropped one batch mid-backfill is most likely gone).
        """
        if not crops or not self.available():
            return {}
        from uuid import uuid4  # noqa: PLC0415

        from .. import mlruntime  # noqa: PLC0415

        items: dict[str, Any] = {}
        for face_id, crop in crops.items():
            try:
                items[face_id] = mlruntime.siglip2_preprocess(crop)
            except mlruntime.MlRuntimeError as error:
                self._ctx.reporter.event(
                    STAGE, "note", f"{face_id[:12]}: expression crop refused: {error}"
                )
        if not items:
            return {}
        try:
            outcome = self._client.infer_tensors(
                model_id=self._ctx.settings.embedding_model,
                # Unique per call: the host caches request ids and refuses a
                # reused id with a different payload.
                request_id=f"face-expression-{uuid4().hex}",
                items=items,
                input_name=mlruntime.SIGLIP2_INPUT_NAME,
            )
        except mlruntime.MlRuntimeError as error:
            self._disable(f"inference failed: {error}")
            return {}
        for failure in outcome.failures:
            self._ctx.reporter.event(
                STAGE,
                "note",
                f"{failure.item_id[:12]}: expression embedding failed: "
                f"{failure.message or failure.code}",
            )
        results: dict[str, dict[str, float]] = {}
        for face_id, values in outcome.tensors.items():
            scores = self._score_axes(values)
            if scores is not None:
                results[face_id] = scores
        return results

    def _score_axes(self, values: Sequence[float]) -> dict[str, float] | None:
        """Unit-normalise, then contrast = cos(emb, positive) - cos(emb, negative)."""
        norm = math.sqrt(sum(float(v) * float(v) for v in values))
        if norm == 0.0 or not math.isfinite(norm):
            return None
        unit = [float(v) / norm for v in values]
        out: dict[str, float] = {}
        for slot, axis_name in _EXPRESSION_AXES:
            axis = (self._axes or {}).get(axis_name) or {}
            positive, negative = axis.get("positive"), axis.get("negative")
            if (
                not positive
                or not negative
                or len(positive) != len(unit)
                or len(negative) != len(unit)
            ):
                self._disable(
                    f"axis {axis_name!r} does not match the embedding dimensions"
                )
                return None
            contrast = sum(u * p for u, p in zip(unit, positive)) - sum(
                u * n for u, n in zip(unit, negative)
            )
            out[slot] = _expression_confidence(contrast)
        return out

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def _backfill_face_sharpness(ctx: StageContext, records: list[dict[str, Any]]) -> int:
    """Measure and fuse face quality for every face not on the current formula.

    Self-repairing rather than a migration: the same pass covers faces written
    before the measure existed, faces written by this run, and faces measured
    under an older fusion formula. A face whose proxy has gone missing is left
    unmeasured (None) -- absence of the proxy must not fabricate a claim.

    Writes, per face, `attributes.sharpness` (the focus measurement),
    `attributes.quality` = sharpness x visibility (the fused score album
    selection sorts on; visibility is the detector-confidence proxy from
    `classical.face_visibility`), and -- for significant faces, when the ml
    host and the expression head are available -- `attributes.eyes_open` and
    `attributes.smile` from SigLIP contrast against the head's face axes.
    Expression degrades gracefully: host down, artifact missing, or axes
    absent means ONE warning, sharpness still written, expression left null.
    Then, per touched photo, `MediaRecord.quality.face_quality` =
    `memory_engine_ranking.fusion.aggregate_face_quality` over its significant
    faces (min-anchored, eyes-open aware -- the group-photo fix; the rollup
    renormalises when no face has a measured eye state).
    """
    from ..classical import face_visibility, measure_face_sharpness  # noqa: PLC0415

    pending: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        attributes = record.get("attributes") or {}
        quality = attributes.get("quality") or {}
        if (
            attributes.get("sharpness") is None
            or attributes.get("head_sharpness") is None
            or quality.get("run_id") != _FACE_QUALITY_RUN_ID
            # A significant face without a stored expression re-pends, so a
            # backfill run while the host was down is repaired by the next
            # run with the host up rather than frozen forever.
            or _needs_expression(record)
        ):
            pending.setdefault(record["media_id"], []).append(record)
    if not pending:
        return 0

    updated = 0
    scorer = _ExpressionScorer(ctx)
    try:
        for media_id, faces in sorted(pending.items()):
            changed: set[str] = set()
            needs_measure = [
                face for face in faces
                if (face.get("attributes") or {}).get("sharpness") is None
                or (face.get("attributes") or {}).get("head_sharpness") is None
            ]
            if needs_measure:
                proxies = ctx.database.proxies_for_media(media_id, kind="thumbnail_512")
                path = proxies[0].get("path") if proxies else None
                if path and Path(path).is_file():
                    boxes = [face["detection"]["bbox"] for face in needs_measure]
                    try:
                        values = measure_face_sharpness(path, boxes)
                        head_values = measure_face_sharpness(
                            path, [_head_box(box) for box in boxes]
                        )
                    except Exception as error:  # noqa: BLE001 - one bad proxy must
                        # not abort the stage; the face stays unmeasured and the
                        # album gate treats that as "no focus evidence", never as
                        # sharp.
                        ctx.reporter.event(
                            STAGE, "note",
                            f"face sharpness failed for {media_id[:12]}: {error}",
                        )
                        values = [None] * len(needs_measure)
                        head_values = [None] * len(needs_measure)
                    for face, value, head in zip(
                        needs_measure, values, head_values, strict=True
                    ):
                        attributes = dict(face.get("attributes") or {})
                        # Changed means CHANGED: a face whose crop is too
                        # small to measure re-measures its (deterministic)
                        # head value every run, and rewriting the identical
                        # record each time is churn, not repair.
                        if value is not None and attributes.get("sharpness") != value:
                            attributes["sharpness"] = value
                            changed.add(face["face_id"])
                        if head is not None and attributes.get("head_sharpness") != head:
                            attributes["head_sharpness"] = head
                            changed.add(face["face_id"])
                        face["attributes"] = attributes

            needs_expression = [face for face in faces if _needs_expression(face)]
            if needs_expression and scorer.available():
                source = _expression_source_path(ctx, media_id)
                crops: list[Any] = [None] * len(needs_expression)
                if source is not None:
                    try:
                        crops = _expression_crops(
                            source,
                            [face["detection"]["bbox"] for face in needs_expression],
                        )
                    except Exception as error:  # noqa: BLE001 - one unreadable
                        # image must not abort the backfill; its faces keep
                        # null expression and the note says why.
                        ctx.reporter.event(
                            STAGE, "note",
                            f"expression crop failed for {media_id[:12]}: {error}",
                        )
                # Batched: one inference call carries every significant face
                # of this photo.
                scores = scorer.score({
                    face["face_id"]: crop
                    for face, crop in zip(needs_expression, crops, strict=True)
                    if crop is not None
                })
                for face in needs_expression:
                    result = scores.get(face["face_id"])
                    if result is None:
                        continue
                    attributes = dict(face.get("attributes") or {})
                    attributes["eyes_open"] = result["eyes_open"]
                    attributes["smile"] = result["smile"]
                    face["attributes"] = attributes
                    changed.add(face["face_id"])

            for face in faces:
                attributes = dict(face.get("attributes") or {})
                sharpness = attributes.get("sharpness")
                if sharpness is not None:
                    fused = round(
                        sharpness
                        * face_visibility(face["detection"]["detection_score"]),
                        6,
                    )
                    quality = {"value": fused, "run_id": _FACE_QUALITY_RUN_ID}
                    if attributes.get("quality") != quality:
                        attributes["quality"] = quality
                        changed.add(face["face_id"])
                if face["face_id"] not in changed:
                    continue
                face["attributes"] = attributes
                ctx.database.put_face(face)
                updated += 1

            _refresh_face_quality_summary(ctx, media_id)
    finally:
        scorer.close()
    return updated


def _refresh_face_quality_summary(ctx: StageContext, media_id: str) -> None:
    """MediaRecord.quality.face_quality from the photo's significant faces.

    This is the number photo-quality fusion already knows how to weigh (0.18
    in the default profile) -- the plug was in the contract and the ranking
    engine all along; this writes the value into it. The aggregate is
    `memory_engine_ranking.fusion.aggregate_face_quality`, NOT max(): a max
    let one excellent face mask four blinking ones in a group photo (the
    blind spot fusion.py names), while the aggregate anchors on the worst
    significant face and the eyes-open ratio, renormalising when no face has
    a measured eye state.
    """
    from memory_engine_ranking.fusion import aggregate_face_quality  # noqa: PLC0415

    qualities: list[float] = []
    eyes_open: list[float | None] = []
    for face in ctx.database.faces_for_media(media_id):
        if (face["detection"].get("face_area_ratio") or 0.0) < _FACE_SUMMARY_MIN_AREA:
            continue
        attributes = face.get("attributes") or {}
        value = (attributes.get("quality") or {}).get("value")
        if value is None:
            continue
        qualities.append(value)
        eyes_open.append(attributes.get("eyes_open"))
    if not qualities:
        return
    record = ctx.database.get_media(media_id)
    if record is None:
        return
    quality = dict(record.get("quality") or {})
    score = {
        "value": aggregate_face_quality(qualities, eyes_open),
        "run_id": _FACE_QUALITY_RUN_ID,
    }
    if quality.get("face_quality") == score:
        return
    quality["face_quality"] = score
    record["quality"] = quality
    ctx.database.put_media(record)


def _all_faces(ctx: StageContext) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = ctx.database.list_faces(limit=PAGE, offset=offset)
        records.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return records


def _run_id(
    ctx: StageContext,
    records: list[dict[str, Any]],
    *,
    spaces: set[str],
    embedding_set_digest: str,
    recognition_pin: dict[str, Any],
) -> str:
    """A Slug naming this clustering pass, stable for an unchanged library.

    `ClusterMembership.clustering_run_id` exists because "re-clustering a
    growing library reshuffles cluster ids; pinning the run makes the reshuffle
    auditable instead of mysterious". A run id derived from the face set does
    that AND makes a re-run over an unchanged library reproduce its cluster ids
    exactly, which is what keeps a second run from rewriting every record with
    the same content under new identifiers.
    """
    from .. import faceidentity  # noqa: PLC0415

    digest = digest_of(
        {
            "faces": sorted(record["face_id"] for record in records),
            "merge_threshold": faceidentity.MERGE_THRESHOLD,
            "spaces": sorted(spaces),
            "embedding_set_digest": embedding_set_digest,
            "recognition_model_pin": recognition_pin,
        }
    )
    return f"{faceidentity.CLUSTERING_METHOD_RUN_PREFIX}-{digest[:16]}"


def _embedding_input(
    record: dict[str, Any],
    reference: dict[str, Any] | None,
    values: list[float] | None,
) -> dict[str, Any]:
    """The immutable vector evidence that makes a clustering pass one job.

    Face ids address detections, not embeddings. A model upgrade normally
    preserves every face id, so the vector bytes and their exact reference
    must participate independently. Values come back from media-db as decoded
    float32; packing them as little-endian float32 reproduces the indexed bytes
    without relying on JSON float formatting.
    """
    if reference is None:
        reference_identity = None
    else:
        reference_identity = {
            key: reference.get(key)
            for key in (
                "space",
                "dimensions",
                "storage",
                "index_key",
                "quantization",
                "normalized",
            )
        }
    vector_digest = None
    if values is not None:
        encoded = struct.pack(f"<{len(values)}f", *values)
        vector_digest = blake3_hex(encoded)
    return {
        "face_id": record["face_id"],
        "reference": reference_identity,
        "vector_blake3": vector_digest,
    }


def _refresh_media_summaries(
    ctx: StageContext,
    assignments: tuple[Any, ...],
    by_id: dict[str, dict[str, Any]],
) -> None:
    """Rewrite each photo's face summary from the assignments just made.

    `confirmed_person_ids` is defined by the contract as "only people whose
    assignment is eligible for automated output", so it is derived here from
    the eligibility property rather than accumulated as faces are written. A
    summary that is computed from the same source as the records cannot drift
    from them; one that is appended to can, and the drift shows up as a person
    appearing in "album of X" whose face was never eligible.
    """
    per_media: dict[str, list[Any]] = {}
    for assignment in assignments:
        media_id = by_id[assignment.face_id]["media_id"]
        per_media.setdefault(media_id, []).append(assignment)

    for media_id, media_assignments in per_media.items():
        record = ctx.database.get_media(media_id)
        if record is None:  # pragma: no cover - a face outlives its media only if deleted
            continue
        summary = dict(record.get("faces") or {})
        confirmed = sorted(
            {
                assignment.person_id
                for assignment in media_assignments
                if assignment.eligible_for_automated_output
                and assignment.person_id is not None
            }
        )
        pending = sum(
            1
            for assignment in media_assignments
            if not assignment.eligible_for_automated_output
        )
        if (
            summary.get("confirmed_person_ids") == confirmed
            and summary.get("pending_review_count") == pending
        ):
            continue
        summary["confirmed_person_ids"] = confirmed
        summary["pending_review_count"] = pending
        record["faces"] = summary
        record["updated_at"] = utc_now()
        ctx.database.put_media(record)


def _queue_payload(queue: tuple[Any, ...], *, run_id: str, now: str) -> dict[str, Any]:
    """The review queue as a file a person (or apps/desktop) can read.

    Not a contract object: `ReviewItem` has no schema, because a question is
    not a record of anything -- answering it produces the record. The shape is
    therefore this stage's, and it says so by living in `outputs/faces/` rather
    than pretending to be a fixture.
    """
    return {
        "clustering_run_id": run_id,
        "generated_at": now,
        "question_count": len(queue),
        "questions": [
            {
                "item_id": item.item_id,
                "kind": item.kind.value,
                "face_ids": list(item.face_ids),
                "affected_face_ids": list(item.affected_face_ids),
                "cluster_ids": list(item.cluster_ids),
                "candidate_person_ids": list(item.candidate_person_ids),
                "subject_person_id": item.subject_person_id,
                # The review reason, unlike the one in the FaceRecord, is the
                # REAL one: four of this package's reasons have no equivalent
                # in the contract enum and serialise as null there. Reporting
                # `near_boundary` for a face held back by a missing consent
                # would be a plausible, wrong explanation, so the record says
                # nothing and the queue says the truth.
                "reason": item.reason.value if item.reason is not None else None,
                "boundary_proximity": item.boundary_proximity,
                "informativeness": item.informativeness,
            }
            for item in queue
        ],
    }


def load_review_queue(path: Any) -> dict[str, Any] | None:
    """Read back what this stage wrote. Used by tests and by the CLI summary."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
