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
import struct
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
