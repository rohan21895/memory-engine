"""`analyze_image` — the resumable photo-analysis pipeline (issue #42).

WHAT ISSUE #42 SAID WAS MISSING, AND WHERE EACH PIECE NOW IS

    "photo_analysis is not a resumable executable pipeline"

  * `classical_quality` had no executor.  -> `memory_engine_pipeline.classical`,
    versioned, calibrated to the 512px proxy, no model and no host required.
  * no checkpoint semantics.              -> here. Completion is per RECORD and
    per STEP, stored in `MediaRecord.processing.stages`, which is the durable
    content-addressed truth the whole system already agrees on.
  * "killing it after image embeddings are stored has no analysis checkpoint"
    -> killing it after image embeddings are stored leaves exactly those
    records with `image_embedding: done`, and the next run starts from the
    first record that is not.
  * "treating classical_quality as a no-op would create MediaRecords that look
    analysed while missing the required cheap quality measures" -> the rollup
    `processing.state` only reaches `analyzed` when every step of the selected
    pipeline is done. A record with classical quality and no embedding sits at
    `analyzing`, forever if need be, and the album stage refuses it.

WHY COMPLETION LIVES IN THE RECORDS AND NOT IN `completed_input_ids`

The contract offers `checkpoint.completed_input_ids`, and for a 300,000-file
library it is the wrong tool: rewriting a 300,000-element array after every
image is quadratic, and it introduces a second answer to "is this photo done?"
that can disagree with the record. The records already have a per-stage state
field designed for exactly this ("granular per stage rather than one status
field because a 3TB scan is killed and resumed constantly"). The job's cursor
carries only a coarse position for progress reporting.

THE GATE, WHICH IS THE POINT OF THIS FILE

If the model host is not reachable, or is reachable but cannot serve the models
this pipeline names, the stage:

  1. runs `classical_quality`, which needs no host and is genuinely useful,
  2. leaves every model step un-run and UNMARKED -- not skipped, not zero,
  3. returns BLOCKED, naming the host and the models,
  4. and therefore leaves `processing.state` short of `analyzed`, which is what
     makes the album stage refuse to build a book out of an unanalysed library.

There is no flag that turns this into a warning. A library with no analysis in
it that nonetheless produced an album is the single most convincing wrong
output this system can make.

ONE ITEM'S FAILURE IS NOT THE BATCH'S
A corrupt proxy in a batch of 32 marks that one record's step `failed` with the
host's error code and leaves the other 31 done, which is the difference between
a scan that reports one bad file and one that fails wholesale.

FACES: DETECT, THEN ALIGN AND EMBED, AS TWO STEPS

`face_detection` writes a FaceRecord per detected face -- box, landmarks,
detector pin, minor-safety envelope -- with a null embedding and an unassigned
identity, which is exactly true at that moment. `face_embedding` then asks the
host to warp each face onto ArcFace's canonical five-point template and embed
it, and rewrites the record with a VectorRef.

They are separate steps because they fail separately and resume separately: a
detector that runs and an embedder that is missing must leave the library with
face BOXES (which the print validator's trim-zone check needs, and which have
nothing to do with identity) and no embeddings, rather than with neither.

THE ALIGNMENT PAIRING IS CHECKED BEFORE ANY FACE IS EMBEDDED

`models/configs/arcface-buffalo-l.json`: "ALIGNMENT IS NOT OPTIONAL ... Feeding
an unaligned crop does not fail -- it returns a confidently wrong embedding,
which then clusters wrongly and puts the wrong person in an album." Feeding a
crop warped from the wrong five points fails identically and is worse, because
the warp succeeds and looks right.

So this stage reads BOTH model configs and refuses the run outright when the
detector's `landmark_scheme` is not the one the embedder's template was built
for. Not per face, not as a warning: a mismatched pairing is a configuration
error, every face in the library would be affected, and the remedy is to change
a model rather than to skip a photo.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from .. import classical, mlruntime, modelconfigs
from ..events import utc_now
from ..ids import face_identity
from ..jobstore import build_job
from .base import StageContext, StageResult, StageStatus, blocked_by

STAGE = "analysis"
JOB_TYPE = "analyze_image"

CLASSICAL_STEP = "classical_quality"
EMBEDDING_STEP = "image_embedding"
FACE_STEP = "face_detection"
FACE_EMBEDDING_STEP = "face_embedding"
MODEL_STEPS = (EMBEDDING_STEP, FACE_STEP, FACE_EMBEDDING_STEP)
ALL_STEPS = (CLASSICAL_STEP, *MODEL_STEPS)

# Embedding spaces, keyed by the model that defines them. A SigLIP 2 upgrade
# creates a NEW space; it does not reinterpret the old one, so this mapping is
# the thing that has to change when a model is swapped, and a model with no
# entry here is refused rather than written into an arbitrary space.
EMBEDDING_SPACES: dict[str, tuple[str, int]] = {
    "siglip2-so400m-384": ("siglip2_so400m_1152", 1152),
    "siglip2-base-384": ("siglip2_base_768", 768),
}

ANALYSABLE_KINDS = frozenset({"image", "live_photo", "motion_photo"})


# --------------------------------------------------------------- selection --


def _analysable(record: Mapping[str, Any]) -> str | None:
    """Why this record cannot be analysed, or None if it can."""
    if record.get("kind") not in ANALYSABLE_KINDS:
        return f"kind {record.get('kind')!r} is not handled by analyze_image"
    processing = record.get("processing") or {}
    if processing.get("state") == "quarantined":
        # Hostile or unreadable. The contract is explicit that these must never
        # be retried automatically.
        return "quarantined at ingest"
    if not _thumbnail(record):
        return "no thumbnail_512 proxy"
    return None


def _thumbnail(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for proxy in record.get("proxies") or []:
        if proxy.get("kind") == "thumbnail_512":
            return proxy
    return None


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


def _roll_up(record: dict[str, Any], *, required: Sequence[str]) -> None:
    """`processing.state`, which is what everything downstream branches on.

    `analyzed` requires every step of the SELECTED pipeline. Anything less is
    `analyzing`, which reads correctly whether the missing steps are still
    queued or blocked on an absent host -- in both cases the honest statement
    is "analysis is not finished", and in neither is it "analysed".
    """
    processing = record.setdefault("processing", {"state": "discovered", "stages": {}})
    if processing.get("state") == "quarantined":
        return
    if all(_step_done(record, step) for step in required):
        processing["state"] = "analyzed"
    else:
        processing["state"] = "analyzing"


_CANDIDATE_SQL = (
    "SELECT media_id FROM media "
    "WHERE kind IN ('image', 'live_photo', 'motion_photo') "
    "AND processing_state NOT IN ('analyzed', 'quarantined') "
    "ORDER BY media_id"
)


def _pending(
    ctx: StageContext, *, steps: Sequence[str]
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Records still missing at least one of `steps`, in media_id order.

    The SQL narrows on the `processing_state` rollup first, so a second run
    over a fully analysed library reads zero record bodies. The per-step check
    then happens on the few that are genuinely outstanding. Doing it the other
    way round -- fetching every record and filtering in Python -- costs a full
    JSON parse of the library on every run, which is the difference between a
    no-op re-run and a five-minute one.

    Streaming in id order rather than materialising the library means a run
    over 300k photos does not need 300k records resident, and makes the
    traversal stable across a kill so the cursor means something.
    """
    for row in ctx.database.connection.execute(_CANDIDATE_SQL).fetchall():
        media_id = row[0]
        record = ctx.database.get_media(media_id)
        if record is None:  # pragma: no cover - a row cannot vanish mid-query
            continue
        if _analysable(record) is not None:
            continue
        if all(_step_done(record, step) for step in steps):
            continue
        yield media_id, record


def _count_pending(ctx: StageContext, *, steps: Sequence[str]) -> int:
    return sum(1 for _ in _pending(ctx, steps=steps))


def _library_digest(ctx: StageContext) -> tuple[str, int]:
    """A digest over the library's media ids, and how many there are.

    The digest goes into `params` rather than the ids going into
    `inputs.media_ids`, and that is a deliberate departure worth defending: the
    contract does say inputs are addressed by hash, but a 300,000-element array
    of hashes is ~20MB of JSON, and this runner rewrites the JobSpec on every
    checkpoint. Storing the ids would make checkpointing quadratic in library
    size -- the exact failure the resumable design exists to avoid. The digest
    preserves the property that actually matters: add one photo and the job
    identity changes, so the previous run's "completed" cannot be mistaken for
    this run's answer.
    """
    import blake3  # noqa: PLC0415

    hasher = blake3.blake3()
    count = 0
    for row in ctx.database.connection.execute(
        "SELECT media_id FROM media ORDER BY media_id"
    ):
        hasher.update(row[0].encode("ascii"))
        count += 1
    return hasher.hexdigest(), count


# -------------------------------------------------------------------- stage --


def run(ctx: StageContext) -> StageResult:
    upstream = ctx.require("ingest")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    library_digest, media_count = _library_digest(ctx)
    if media_count == 0:
        return StageResult(
            stage=STAGE, status=StageStatus.SKIPPED, detail="the library is empty"
        )

    settings = ctx.settings
    space = EMBEDDING_SPACES.get(settings.embedding_model)
    if space is None:
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=(
                f"no vector space is defined for {settings.embedding_model!r}; "
                "an embedding written into a space it was not produced in is "
                "worse than no embedding"
            ),
        )

    try:
        face_stack = _face_stack(ctx)
    except modelconfigs.ModelConfigError as error:
        return StageResult(
            stage=STAGE, status=StageStatus.FAILED, detail=str(error)
        )

    # Model pins, in `inputs.models` AND in the params digest. The digest is the
    # part that matters: naming the models by id alone meant that editing a
    # config -- a detection threshold, an NMS IoU -- produced the same job_id,
    # found the completed row and skipped every record already marked `done`.
    # The library kept the previous analysis and nothing anywhere said so.
    try:
        pins = [
            modelconfigs.registry_pin(ctx.repo_root, model_id)
            for model_id in sorted(
                {
                    settings.embedding_model,
                    settings.face_model,
                    settings.face_embedding_model,
                }
            )
        ]
    except modelconfigs.ModelConfigError as error:
        return StageResult(stage=STAGE, status=StageStatus.FAILED, detail=str(error))

    params = {
        "pipeline": "photo_analysis",
        "steps": list(ALL_STEPS),
        "classical_quality": {
            "executor": classical.EXECUTOR_ID,
            "version": classical.EXECUTOR_VERSION,
        },
        "embedding_model": settings.embedding_model,
        "embedding_space": space[0],
        "face_model": settings.face_model,
        "face_score_floor": settings.face_score_floor,
        "face_embedding_model": settings.face_embedding_model,
        "face_embedding_space": face_stack.space,
        "landmark_scheme": face_stack.landmark_scheme,
        "batch": settings.infer_batch,
        "library_digest": library_digest,
        "model_pins": pins,
    }
    job = build_job(
        job_type=JOB_TYPE,
        scope=settings.scope,
        params=params,
        models=pins,
        priority=400,
    )
    job = ctx.jobs.get(job["job_id"]) or ctx.jobs.ensure(job)

    if settings.reanalyze_faces:
        reset = _reset_face_steps(ctx)
        ctx.reporter.event(
            STAGE,
            "note",
            f"--reanalyze-faces cleared the face steps on {reset} record(s)",
        )

    outstanding = _count_pending(ctx, steps=ALL_STEPS)
    if job["state"]["status"] == "completed" and outstanding == 0:
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="every record in the library is already analysed",
            job_id=job["job_id"],
            counts={"media": media_count, "pending": 0},
        )

    job = ctx.jobs.begin(job)
    ctx.reporter.event(
        STAGE, "stage_start", f"{outstanding:,} of {media_count:,} records outstanding"
    )

    # 1. Classical quality. No host, no network, no model. Runs first because
    #    it is nearly free and because it is what makes the required contract
    #    fields exist at all.
    classical_counts = _run_classical(ctx, job, total=outstanding)

    # 2. The gate. The face embedder is required alongside the others: a host
    #    that can detect faces but not embed them produces a library whose
    #    every face is unidentifiable, and reporting that as a completed
    #    analysis is the "confident, empty result" this file exists to prevent.
    status = mlruntime.probe(
        endpoint=settings.ml_runtime_endpoint,
        required_models=[
            settings.embedding_model,
            settings.face_model,
            settings.face_embedding_model,
        ],
        timeout_s=settings.ml_runtime_timeout_s,
    )
    if not status.available:
        ctx.jobs.block(
            job,
            code="model_load_failed" if status.reason == "models_unavailable"
            else "dependency_failed",
            message="the model host could not serve the photo-analysis models",
        )
        detail = status.detail
        ctx.reporter.event(
            STAGE,
            "stage_blocked",
            detail,
            reason=status.reason,
            classical_done=classical_counts["done"],
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.BLOCKED,
            detail=detail,
            job_id=job["job_id"],
            counts={
                "media": media_count,
                "classical_quality": classical_counts,
                "model_steps": "not run",
                "runtime": status.to_dict(),
            },
        )

    ctx.reporter.event(STAGE, "note", status.detail, load_mode=status.load_mode)
    for warning in status.warnings:
        ctx.reporter.event(STAGE, "note", f"host warning: {warning}")

    # 3. The model steps.
    try:
        model_counts = _run_models(ctx, job, space=space, face_stack=face_stack)
    except mlruntime.MlRuntimeError as error:
        ctx.jobs.fail(
            job,
            code="model_inference_failed",
            message="inference failed against the model host",
            retryable=True,
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=str(error),
            job_id=job["job_id"],
            counts={"classical_quality": classical_counts},
        )

    remaining = _count_pending(ctx, steps=ALL_STEPS)
    counts = {
        "media": media_count,
        "classical_quality": classical_counts,
        **model_counts,
        "still_pending": remaining,
    }
    if remaining:
        # Every record that could be analysed was attempted, and some did not
        # finish. That is a failure of those records, not of the stage -- but
        # the stage must not claim completion while the library is short.
        ctx.jobs.fail(
            job,
            code="model_inference_failed",
            message="some records could not be analysed",
            retryable=True,
        )
        ctx.reporter.event(
            STAGE, "stage_failed", f"{remaining} records could not be analysed"
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.FAILED,
            detail=f"{remaining} records remain unanalysed after a full pass",
            job_id=job["job_id"],
            counts=counts,
        )

    ctx.jobs.complete(job)
    ctx.reporter.event(STAGE, "stage_done", "analysis complete", **{
        key: value for key, value in counts.items() if isinstance(value, int)
    })
    return StageResult(
        stage=STAGE, status=StageStatus.COMPLETED, detail="analysis complete",
        job_id=job["job_id"], counts=counts,
    )


def _reset_face_steps(ctx: StageContext) -> int:
    """Un-mark the face steps everywhere, so the next pass redoes them.

    Only the two face steps. Classical quality and the image embedding are
    expensive and unaffected by a detector change, and clearing them would turn
    "re-detect the faces" into "re-analyse the library", which is a different
    and much longer operation than the one the operator asked for.
    """
    reset = 0
    for row in ctx.database.connection.execute(
        "SELECT media_id FROM media ORDER BY media_id"
    ).fetchall():
        record = ctx.database.get_media(row[0])
        if record is None:  # pragma: no cover - a row cannot vanish mid-query
            continue
        stages = (record.get("processing") or {}).get("stages") or {}
        if not any(step in stages for step in (FACE_STEP, FACE_EMBEDDING_STEP)):
            continue
        for step in (FACE_STEP, FACE_EMBEDDING_STEP):
            stages.pop(step, None)
        _roll_up(record, required=ALL_STEPS)
        record["updated_at"] = utc_now()
        ctx.database.put_media(record)
        reset += 1
    return reset


# -------------------------------------------------------------- face stack --


class FaceStack:
    """The detector/embedder pairing, checked once, carried to the call sites.

    Constructed only when the two agree on a landmark ordering. There is no way
    to hold one of these with a mismatched pairing, which is the same trick
    `AutomatedFaceSet` uses in packages/face-identity: the unsafe state is
    unrepresentable rather than merely discouraged.
    """

    __slots__ = ("detector_id", "embedder_id", "landmark_scheme", "space", "dimensions")

    def __init__(
        self,
        *,
        detector_id: str,
        embedder_id: str,
        landmark_scheme: str,
        space: str,
        dimensions: int,
    ) -> None:
        self.detector_id = detector_id
        self.embedder_id = embedder_id
        self.landmark_scheme = landmark_scheme
        self.space = space
        self.dimensions = dimensions


def _face_stack(ctx: StageContext) -> FaceStack:
    """Read both configs and refuse a pairing that cannot produce a real embedding."""
    settings = ctx.settings
    detector = modelconfigs.read_model_config(ctx.repo_root, settings.face_model)
    embedder = modelconfigs.read_model_config(ctx.repo_root, settings.face_embedding_model)

    detector_scheme = modelconfigs.detector_landmark_scheme(detector)
    embedder_scheme = modelconfigs.embedder_alignment_scheme(embedder)
    if detector_scheme is None:
        raise modelconfigs.ModelConfigError(
            f"{settings.face_model} declares no landmark scheme, so it produces no "
            f"keypoints to align with. {settings.face_embedding_model} cannot embed "
            "faces without them, and an unaligned crop returns a confidently wrong "
            "embedding rather than an error"
        )
    if detector_scheme != embedder_scheme:
        raise modelconfigs.ModelConfigError(
            f"{settings.face_model} emits {detector_scheme} landmarks and "
            f"{settings.face_embedding_model} aligns with an {embedder_scheme} "
            "template. Both are five points and they are NOT interchangeable: the "
            "warp would succeed and every embedding would be wrong, with nothing "
            "downstream able to detect it (face-record.schema.json). Pair the "
            "embedder with a detector that emits its scheme"
        )
    space, dimensions = modelconfigs.embedder_vector_space(embedder)
    return FaceStack(
        detector_id=settings.face_model,
        embedder_id=settings.face_embedding_model,
        landmark_scheme=detector_scheme,
        space=space,
        dimensions=dimensions,
    )


# ------------------------------------------------------------------- steps --


def _run_classical(ctx: StageContext, job: dict[str, Any], *, total: int) -> dict[str, int]:
    done = 0
    failed = 0
    seen = 0
    for media_id, record in _pending(ctx, steps=[CLASSICAL_STEP]):
        seen += 1
        proxy = _thumbnail(record)
        assert proxy is not None  # _analysable already guaranteed it
        try:
            measured = classical.measure(proxy["path"], proxy_kind="thumbnail_512")
        except (classical.ClassicalQualityError, OSError) as error:
            _mark(record, CLASSICAL_STEP, "failed", last_error={
                "code": "file-unreadable",
                "message": "the proxy could not be measured; location redacted",
                "retryable": False,
                "occurred_at": utc_now(),
            })
            _roll_up(record, required=ALL_STEPS)
            ctx.database.put_media(record)
            failed += 1
            ctx.reporter.event(STAGE, "note", f"classical_quality failed: {error}")
            continue

        quality = dict(record.get("quality") or {})
        quality.update(measured.to_quality())
        record["quality"] = quality
        record["updated_at"] = utc_now()
        _record_classical_run(
            record, measured, proxy_id=proxy["proxy_id"], job_id=job["job_id"]
        )
        _mark(record, CLASSICAL_STEP, "done", job_id=job["job_id"])
        _apply_exclusions(record, measured)
        _roll_up(record, required=ALL_STEPS)
        ctx.database.put_media(record)
        done += 1
        if done % 50 == 0:
            ctx.jobs.checkpoint(
                job,
                cursor={"step": CLASSICAL_STEP, "last_media_id": media_id, "done": done},
                units_done=float(done),
                units_total=float(total) if total else None,
                unit="images",
                message="classical quality",
            )
        ctx.reporter.progress(
            STAGE,
            units_done=float(seen),
            units_total=float(total) if total else None,
            unit="images",
            message="classical quality",
        )
    if done or failed:
        ctx.jobs.checkpoint(
            job,
            cursor={"step": CLASSICAL_STEP, "done": done},
            units_done=float(done),
            units_total=float(total) if total else None,
            unit="images",
            message="classical quality complete",
        )
        ctx.reporter.progress(
            STAGE,
            units_done=float(done),
            units_total=float(total) if total else None,
            unit="images",
            message="classical quality complete",
            force=True,
        )
    return {"done": done, "failed": failed}


def _apply_exclusions(record: dict[str, Any], measured: classical.ClassicalQuality) -> None:
    """Black frames and pocket shots are excluded, not deleted.

    Exclusion is a reversible statement about automation ("do not put this in a
    book"), which is why the record keeps its `user_override` field and why
    nothing is removed from the library.
    """
    if not (measured.is_black_frame or measured.is_lens_obstructed):
        return
    exclusion = dict(record.get("exclusion") or {})
    reasons = list(exclusion.get("reasons") or [])
    for flag, reason in (
        (measured.is_black_frame, "black_frame"),
        (measured.is_lens_obstructed, "lens_obstructed"),
    ):
        if flag and reason not in reasons:
            reasons.append(reason)
    exclusion["reasons"] = reasons
    exclusion["excluded_from_automation"] = True
    exclusion.setdefault("user_override", None)
    record["exclusion"] = exclusion


def _batches(
    items: Iterator[tuple[str, dict[str, Any]]], size: int
) -> Iterator[list[tuple[str, dict[str, Any]]]]:
    batch: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _run_models(
    ctx: StageContext,
    job: dict[str, Any],
    *,
    space: tuple[str, int],
    face_stack: FaceStack,
) -> dict[str, Any]:
    settings = ctx.settings
    space_name, dimensions = space
    counts: dict[str, Any] = {
        "embedded": 0,
        "embedding_failed": 0,
        "faces_scanned": 0,
        "faces_failed": 0,
        "faces_found": 0,
        "faces_embedded": 0,
        "faces_embedding_failed": 0,
        "face_embedding_failures": {
            "retryable": 0,
            "non_retryable": 0,
            "by_reason": {},
        },
        "faces_without_landmarks": 0,
    }

    with mlruntime.MlRuntimeClient(settings.ml_runtime_endpoint) as client:
        total = _count_pending(ctx, steps=[EMBEDDING_STEP])
        seen = 0
        for batch in _batches(_pending(ctx, steps=[EMBEDDING_STEP]), settings.infer_batch):
            # Keyed by media_id, never by proxy_id: proxies are content
            # addressed, so two different photos with byte-identical
            # thumbnails share one, and keying the batch on it would drop a
            # record from the request without anything noticing.
            by_media = {media_id: record for media_id, record in batch}
            proxies = {
                media_id: _thumbnail(record)["proxy_id"]
                for media_id, record in batch
            }
            outcome = client.infer_proxies(
                model_id=settings.embedding_model,
                request_id=f"{job['job_id']}:{EMBEDDING_STEP}:{batch[0][0]}",
                items=proxies,
                alignment="none",
            )
            failures = {failure.item_id: failure for failure in outcome.failures}
            for media_id, record in by_media.items():
                proxy_id = proxies[media_id]
                failure = failures.get(media_id)
                if failure is not None:
                    _mark(record, EMBEDDING_STEP, "failed", last_error={
                        "code": "model-inference-failed",
                        "message": failure.message or failure.code,
                        "retryable": failure.retryable,
                        "occurred_at": utc_now(),
                    })
                    counts["embedding_failed"] += 1
                else:
                    values = outcome.tensors.get(media_id)
                    if values is None or len(values) != dimensions:
                        raise mlruntime.MlRuntimeError(
                            f"{settings.embedding_model} returned "
                            f"{0 if values is None else len(values)} dimensions for "
                            f"space {space_name} which is defined as {dimensions}; "
                            "storing it would corrupt every search and diversity "
                            "decision that reads the space"
                        )
                    # The vector write and the record write share one implicit
                    # transaction, committed by put_media, so a kill cannot
                    # leave a record claiming an embedding the index does not
                    # hold.
                    ctx.database.vectors.put("media", media_id, space_name, values)
                    content = dict(record.get("content") or {})
                    content["embedding"] = {
                        "space": space_name,
                        "dimensions": dimensions,
                        "storage": "index",
                        "index_key": media_id,
                        "quantization": "float32",
                        "normalized": _is_normalised(values),
                    }
                    record["content"] = content
                    _record_model_run(
                        record,
                        outcome.pin,
                        proxy_id=proxy_id,
                        job_id=job["job_id"],
                    )
                    _mark(record, EMBEDDING_STEP, "done", job_id=job["job_id"])
                    counts["embedded"] += 1
                record["updated_at"] = utc_now()
                _roll_up(record, required=ALL_STEPS)
                ctx.database.put_media(record)
                seen += 1
            ctx.jobs.checkpoint(
                job,
                cursor={"step": EMBEDDING_STEP, "last_media_id": batch[-1][0],
                        "done": counts["embedded"]},
                units_done=float(seen),
                units_total=float(total) if total else None,
                unit="images",
                message="image embedding",
            )
            ctx.reporter.progress(
                STAGE,
                units_done=float(seen),
                units_total=float(total) if total else None,
                unit="images",
                message="image embedding",
            )

        total = _count_pending(ctx, steps=[FACE_STEP])
        seen = 0
        for batch in _batches(_pending(ctx, steps=[FACE_STEP]), settings.infer_batch):
            by_media = {media_id: record for media_id, record in batch}
            proxies = {
                media_id: _thumbnail(record)["proxy_id"]
                for media_id, record in batch
            }
            outcome = client.infer_proxies(
                model_id=settings.face_model,
                request_id=f"{job['job_id']}:{FACE_STEP}:{batch[0][0]}",
                items=proxies,
                alignment="none",
            )
            failures = {failure.item_id: failure for failure in outcome.failures}
            for media_id, record in by_media.items():
                proxy_id = proxies[media_id]
                failure = failures.get(media_id)
                if failure is not None:
                    _mark(record, FACE_STEP, "failed", last_error={
                        "code": "model-inference-failed",
                        "message": failure.message or failure.code,
                        "retryable": failure.retryable,
                        "occurred_at": utc_now(),
                    })
                    counts["faces_failed"] += 1
                else:
                    detections = [
                        detection
                        for detection in outcome.detections.get(media_id, ())
                        if detection.score >= settings.face_score_floor
                    ]
                    written = _write_detected_faces(
                        ctx,
                        record,
                        detections,
                        pin=outcome.pin,
                        face_stack=face_stack,
                    )
                    eligible_identities = [
                        face["identity"]
                        for face in written
                        if face["identity"]["eligible_for_automated_output"]
                    ]
                    record["faces"] = {
                        # A count AND the ids now. The empty `face_ids` this
                        # field used to carry was honest at the time (nothing
                        # wrote FaceRecords) and it is what made album-engine's
                        # face-safe layout pass vacuously: no rectangles, no
                        # face can be in the trim zone, and the gate CLAUDE.md
                        # rule 5 rests on could not fail.
                        #
                        "face_count": len(written),
                        "face_ids": [face["face_id"] for face in written],
                        # Usually empty because no calibration exists today,
                        # but a human-confirmed identity is authoritative and
                        # survives re-detection. Replacing this summary with
                        # model defaults while preserving the FaceRecord would
                        # leave two answers to whether the person is confirmed.
                        "confirmed_person_ids": sorted(
                            {
                                identity["person_id"]
                                for identity in eligible_identities
                                if identity.get("person_id") is not None
                            }
                        ),
                        "pending_review_count": len(written)
                        - len(eligible_identities),
                        "largest_face_area_ratio": (
                            round(max(d.area_ratio for d in detections), 6)
                            if detections else None
                        ),
                    }
                    _record_model_run(
                        record,
                        outcome.pin,
                        proxy_id=proxy_id,
                        job_id=job["job_id"],
                    )
                    _mark(record, FACE_STEP, "done", job_id=job["job_id"])
                    counts["faces_scanned"] += 1
                    counts["faces_found"] += len(written)
                record["updated_at"] = utc_now()
                _roll_up(record, required=ALL_STEPS)
                ctx.database.put_media(record)
                seen += 1
            ctx.jobs.checkpoint(
                job,
                cursor={"step": FACE_STEP, "last_media_id": batch[-1][0],
                        "done": counts["faces_scanned"]},
                units_done=float(seen),
                units_total=float(total) if total else None,
                unit="images",
                message="face detection",
            )
            ctx.reporter.progress(
                STAGE,
                units_done=float(seen),
                units_total=float(total) if total else None,
                unit="images",
                message="face detection",
            )

        _run_face_embedding(ctx, job, client=client, face_stack=face_stack, counts=counts)

    return counts


# ------------------------------------------------------------------- faces --


def _write_detected_faces(
    ctx: StageContext,
    record: dict[str, Any],
    detections: Sequence[mlruntime.Detection],
    *,
    pin: Mapping[str, str],
    face_stack: FaceStack,
) -> list[dict[str, Any]]:
    """One FaceRecord per detection, with no embedding and no identity yet.

    Written before anything is embedded because a face BOX is useful on its
    own: it is what the print validator's trim-zone and gutter checks protect,
    and `records.face_boxes_for_layout` takes no assignment argument precisely
    so that safety never waits on identity.

    Faces that disappear between two runs of a changed detector are DELETED
    rather than left behind. `face_id` includes the detector's id and version,
    so an upgrade produces new ids for the same faces; keeping the old rows
    would leave the MediaRecord's `face_count` disagreeing with the number of
    stored rectangles, which is exactly the contradiction the album stage now
    refuses to plan through.
    """
    from memory_engine_face.identity import FaceContext  # noqa: PLC0415
    from memory_engine_face.records import (  # noqa: PLC0415
        Detection,
        DetectedFace,
        ModelRef,
        NormalizedBox,
    )

    from .. import faceidentity  # noqa: PLC0415

    media_id = record["media_id"]
    now = utc_now()
    detector = ModelRef(
        model_id=pin.get("model_id") or face_stack.detector_id,
        version=pin.get("version") or "0",
        weights_blake3=pin.get("weights_blake3") or None,
        config_blake3=pin.get("config_blake3") or None,
    )

    stored_before = {
        stored["face_id"]: stored for stored in ctx.database.faces_for_media(media_id)
    }

    faces: list[DetectedFace] = []
    seen: set[str] = set()
    for detection in detections:
        face_id = face_identity(
            media_id=media_id,
            bbox=(detection.x, detection.y, detection.w, detection.h),
            detector_model_id=detector.model_id,
            detector_version=detector.version,
        )
        if face_id in seen:
            # Two detections whose boxes round to the same 1e-4 grid are ONE
            # face_id, and a face_id is one row. Counting both would put the
            # same id in `face_ids` twice and leave `face_count` permanently
            # above the number of rectangles the library holds -- which the
            # album stage reads as missing face evidence and refuses to plan
            # through, forever, with a remediation that cannot help. Measured:
            # a detector returning one box twice produced face_count=2 against
            # 1 stored row and a book that could never be built.
            continue
        seen.add(face_id)
        landmarks = _landmarks_block(detection, face_stack)
        faces.append(
            DetectedFace(
                face_id=face_id,
                media_id=media_id,
                detection=Detection(
                    bbox=NormalizedBox(
                        x=detection.x, y=detection.y, w=detection.w, h=detection.h
                    ),
                    detection_score=min(1.0, max(0.0, detection.score)),
                    detector=detector,
                    detected_on="thumbnail_512",
                    # Never None: media-db's `face` table declares
                    # face_area_ratio NOT NULL, and it is the schema's "single
                    # best predictor of whether an embedding will be
                    # trustworthy".
                    face_area_ratio=min(1.0, max(0.0, detection.area_ratio)),
                ),
                landmarks=landmarks,
            )
        )

    assignments = {
        assignment.face_id: assignment
        for assignment in faceidentity.assign(
            [
                FaceContext(
                    face_id=face.face_id,
                    minor_status=faceidentity.assignable_minor_status(
                        (stored_before.get(face.face_id) or {}).get("sensitive")
                    ),
                )
                for face in faces
            ],
            now=now,
        )
    }

    written: list[dict[str, Any]] = []
    for face in faces:
        previous = stored_before.get(face.face_id)
        # A re-detection with the same detector rewrites the SAME face_id, so
        # anything a human answered about it has to survive the rewrite:
        # `created_at` is when this face was first seen, not when it was last
        # re-derived, and the minor-safety envelope is the answer itself.
        face_record = faceidentity.record_for(
            face,
            assignments[face.face_id],
            created_at=(previous or {}).get("created_at") or now,
            updated_at=now,
        )
        faceidentity.restore_sensitive(
            face_record, (previous or {}).get("sensitive")
        )
        faceidentity.restore_human_identity(
            face_record, (previous or {}).get("identity")
        )
        ctx.database.put_face(face_record)
        written.append(dict(face_record))

    _forget_stale_faces(ctx, media_id, keep={face.face_id for face in faces})
    return written


def _landmarks_block(
    detection: mlruntime.Detection, face_stack: FaceStack
) -> dict[str, Any] | None:
    """The contract Landmarks block, or None when there is nothing alignable.

    A detection whose keypoints fell outside the frame comes back with none at
    all (the host drops them and sets `landmarks_out_of_range`), and the schema
    requires at least five points, so the honest record has `landmarks: null`
    and the face is simply never embedded.

    A detection that carries points under a scheme the pipeline did not
    negotiate is a different thing entirely: the host and its config disagree,
    which means the pin does not describe what ran. That raises.
    """
    if not detection.landmarks:
        return None
    if detection.landmark_scheme != face_stack.landmark_scheme:
        raise mlruntime.MlRuntimeError(
            f"{face_stack.detector_id} returned {detection.landmark_scheme!r} "
            f"landmarks but its config declares {face_stack.landmark_scheme!r}. The "
            "host and the registry disagree about what ran, so no landmark from this "
            "run can be trusted onto an alignment template"
        )
    return {
        "scheme": detection.landmark_scheme,
        "points": [{"x": point.x, "y": point.y} for point in detection.landmarks],
        "score": None,
    }


def _forget_stale_faces(ctx: StageContext, media_id: str, *, keep: set[str]) -> None:
    for stored in ctx.database.faces_for_media(media_id):
        face_id = stored["face_id"]
        if face_id in keep:
            continue
        ctx.database.delete_face(face_id)
        ctx.database.vectors.delete("face", face_id)


def _run_face_embedding(
    ctx: StageContext,
    job: dict[str, Any],
    *,
    client: mlruntime.MlRuntimeClient,
    face_stack: FaceStack,
    counts: dict[str, Any],
) -> None:
    """Align and embed every stored face, one gRPC item per face.

    The host does the warp -- it holds the template, and ml_runtime.proto is
    explicit that converting normalised landmarks to pixels "is deliberately
    not the caller's job: a caller that gets it wrong produces an embedding
    that is confidently wrong rather than absent". This function's whole
    responsibility is to hand over the right five points with the right label
    on them, and to record what came back.

    A face with no embedding stays a face. `records.face_boxes_for_layout`
    still returns its rectangle, `assign_identities` gives it a `no_embedding`
    review reason, and it counts toward "how many people are in this photo".
    """
    settings = ctx.settings
    total = _count_pending(ctx, steps=[FACE_EMBEDDING_STEP])
    seen = 0
    for batch in _batches(
        _pending(ctx, steps=[FACE_EMBEDDING_STEP]), settings.infer_batch
    ):
        # Crops are gathered across the WHOLE batch of photos before anything
        # is sent. A per-photo request would be one gRPC round trip per photo
        # -- 300,000 of them on a real library, most carrying one or two faces
        # -- which is the shape the batching in this file exists to avoid.
        crops: list[mlruntime.FaceCrop] = []
        stored_by_face: dict[str, dict[str, Any]] = {}
        for media_id, record in batch:
            proxy = _thumbnail(record)
            assert proxy is not None  # _analysable already guaranteed it
            batch_crops, stored, skipped = _crops_for(
                ctx, media_id=media_id, proxy_id=proxy["proxy_id"]
            )
            crops.extend(batch_crops)
            stored_by_face.update(stored)
            counts["faces_without_landmarks"] += skipped

        # A transport or contract failure is the whole request's, not one
        # record's, and must not be recorded as "these photos have no faces".
        # It propagates out of the stage, which marks nothing.
        embedded, failures_by_media = _embed_crops(
            ctx,
            client=client,
            job=job,
            crops=crops,
            stored_by_face=stored_by_face,
            face_stack=face_stack,
        )
        counts["faces_embedded"] += embedded
        failure_counts = counts["face_embedding_failures"]
        for failures in failures_by_media.values():
            counts["faces_embedding_failed"] += len(failures)
            for failure in failures:
                retryability = (
                    "retryable" if failure.retryable else "non_retryable"
                )
                failure_counts[retryability] += 1
                by_reason = failure_counts["by_reason"]
                by_reason[failure.code] = by_reason.get(failure.code, 0) + 1

        for media_id, record in batch:
            failures = failures_by_media.get(media_id, ())
            if failures:
                by_reason: dict[str, int] = {}
                for failure in failures:
                    by_reason[failure.code] = by_reason.get(failure.code, 0) + 1
                reasons = ", ".join(
                    f"{code}: {count}" for code, count in sorted(by_reason.items())
                )
                _mark(
                    record,
                    FACE_EMBEDDING_STEP,
                    "failed",
                    last_error={
                        "code": "model-inference-failed",
                        "message": (
                            f"{len(failures)} face embedding(s) failed ({reasons})"
                        ),
                        "retryable": any(failure.retryable for failure in failures),
                        "occurred_at": utc_now(),
                    },
                )
            else:
                _mark(record, FACE_EMBEDDING_STEP, "done", job_id=job["job_id"])
            record["updated_at"] = utc_now()
            _roll_up(record, required=ALL_STEPS)
            ctx.database.put_media(record)
            seen += 1
        ctx.jobs.checkpoint(
            job,
            cursor={"step": FACE_EMBEDDING_STEP, "last_media_id": batch[-1][0],
                    "done": counts["faces_embedded"]},
            units_done=float(seen),
            units_total=float(total) if total else None,
            unit="images",
            message="face embedding",
        )
        ctx.reporter.progress(
            STAGE,
            units_done=float(seen),
            units_total=float(total) if total else None,
            unit="images",
            message="face embedding",
        )


def _crops_for(
    ctx: StageContext, *, media_id: str, proxy_id: str
) -> tuple[list[mlruntime.FaceCrop], dict[str, dict[str, Any]], int]:
    """One photo's alignable faces. Returns (crops, records by id, skipped).

    A stored face with no landmarks is skipped, not failed. The host dropped
    its keypoints because they fell outside the frame, there is nothing to warp
    onto the template, and an unaligned crop would come back as a confident
    wrong vector rather than as an error.
    """
    crops: list[mlruntime.FaceCrop] = []
    stored_by_face: dict[str, dict[str, Any]] = {}
    skipped = 0
    for record in ctx.database.faces_for_media(media_id):
        stored_by_face[record["face_id"]] = record
        landmarks = record.get("landmarks") or {}
        points = landmarks.get("points") or []
        if not points:
            skipped += 1
            continue
        crops.append(
            mlruntime.FaceCrop(
                item_id=record["face_id"],
                proxy_id=proxy_id,
                landmarks=tuple((point["x"], point["y"]) for point in points),
                landmark_scheme=landmarks["scheme"],
            )
        )
    return crops, stored_by_face, skipped


def _embed_crops(
    ctx: StageContext,
    *,
    client: mlruntime.MlRuntimeClient,
    job: dict[str, Any],
    crops: Sequence[mlruntime.FaceCrop],
    stored_by_face: Mapping[str, dict[str, Any]],
    face_stack: FaceStack,
) -> tuple[int, dict[str, list[mlruntime.ItemFailure]]]:
    """Send the crops in host-sized chunks and store what comes back."""
    embedded = 0
    failures_by_media: dict[str, list[mlruntime.ItemFailure]] = {}
    for start in range(0, len(crops), ctx.settings.infer_batch):
        chunk = list(crops[start : start + ctx.settings.infer_batch])
        outcome = client.infer_faces(
            model_id=face_stack.embedder_id,
            request_id=f"{job['job_id']}:{FACE_EMBEDDING_STEP}:{chunk[0].item_id}",
            crops=chunk,
            required_landmark_scheme=face_stack.landmark_scheme,
        )
        failures = {failure.item_id: failure for failure in outcome.failures}
        for crop in chunk:
            record = stored_by_face[crop.item_id]
            failure = failures.get(crop.item_id)
            if failure is not None:
                # The host declined this face -- too small, unreadable crop,
                # an alignment it refused. That is a real and expected answer
                # and it is NOT zero: the record keeps `embedding: null`, which
                # sends the face to review rather than into a cluster.
                ctx.reporter.event(
                    STAGE,
                    "note",
                    f"face {crop.item_id[:12]} was not embedded: "
                    f"{failure.message or failure.code}",
                )
                failures_by_media.setdefault(record["media_id"], []).append(failure)
                continue
            values = outcome.tensors.get(crop.item_id)
            if values is None or len(values) != face_stack.dimensions:
                raise mlruntime.MlRuntimeError(
                    f"{face_stack.embedder_id} returned "
                    f"{0 if values is None else len(values)} dimensions for space "
                    f"{face_stack.space}, which is defined as "
                    f"{face_stack.dimensions}. Storing it would put a vector in a "
                    "space it was not produced in, and every distance computed from "
                    "it afterwards is a plausible number with no meaning"
                )
            ctx.database.vectors.put("face", crop.item_id, face_stack.space, values)
            record["embedding"] = {
                "space": face_stack.space,
                "dimensions": face_stack.dimensions,
                "storage": "index",
                "index_key": crop.item_id,
                "quantization": "float32",
                "normalized": _is_normalised(values),
            }
            record["updated_at"] = utc_now()
            ctx.database.put_face(record)
            embedded += 1
    return embedded, failures_by_media


def _is_normalised(values: Sequence[float]) -> bool:
    magnitude = sum(value * value for value in values) ** 0.5
    return abs(magnitude - 1.0) < 1e-3


def _record_model_run(
    record: dict[str, Any],
    pin: Mapping[str, str],
    *,
    proxy_id: str,
    job_id: str,
) -> None:
    """Provenance, pinned to weights AND config.

    Weights alone do not determine behaviour: a detector's score threshold and
    a model's normalisation constants live in the config, and moving either
    changes every downstream decision while the weights hash stays identical.
    The contract's ModelRun has no field for the config digest, so it is not
    invented here; the host reported it and the gap is recorded in the README
    rather than papered over with a non-contract key.

    `run_id` is the model id, which is a Slug by construction, so the Score
    objects that reference a run can be resolved without a second lookup table.
    """
    model_id = pin.get("model_id") or "unknown"
    entry = {
        "run_id": model_id,
        "model": {
            "model_id": model_id,
            "version": pin.get("version") or "0",
            "weights_blake3": pin.get("weights_blake3") or None,
        },
        "ran_at": utc_now(),
        "input_proxy_id": proxy_id,
        "duration_ms": None,
        "job_id": job_id,
    }
    runs = [run for run in (record.get("model_runs") or [])
            if run.get("run_id") != model_id]
    runs.append(entry)
    record["model_runs"] = runs


def _record_classical_run(
    record: dict[str, Any], measured: classical.ClassicalQuality, *,
    proxy_id: str, job_id: str,
) -> None:
    """The ModelRun the classical scores point at.

    Every Score in the contract carries a `run_id` and every run_id must
    resolve to a ModelRun, so a "model-less" step still needs an entry. Its
    ModelRef has a null `weights_blake3` because there are no weights -- which
    is exactly what a null there means, and is why the field is nullable.
    """
    run_id = measured.run_id
    entry = {
        "run_id": run_id,
        "model": {
            "model_id": classical.EXECUTOR_ID,
            "version": classical.EXECUTOR_VERSION,
            "weights_blake3": None,
        },
        "ran_at": utc_now(),
        "input_proxy_id": proxy_id,
        "duration_ms": None,
        "job_id": job_id,
    }
    runs = [run for run in (record.get("model_runs") or []) if run.get("run_id") != run_id]
    runs.append(entry)
    record["model_runs"] = runs
