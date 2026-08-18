"""dedupe_cluster and rank_media, both delegated to `packages/ranking-engine`.

Nothing in this file decides anything. Near-duplicate grouping, primary
selection, score fusion and elimination all live in the ranking engine, and
re-implementing any of them here -- even "just the easy part" -- would create a
second answer to a question the system is supposed to have one answer to.

WHAT THIS FILE DOES DO IS FETCH ECONOMICALLY.

`find_duplicates` refines ambiguous perceptual-hash matches with embedding
distance, so it wants embeddings. Loading every embedding in a 300k library is
1.4GB of floats to resolve a few thousand ambiguous pairs. So the candidate
pairs are computed from perceptual hashes first (that is exactly what
`candidate_pairs` is exported for), and only the media appearing in a pair have
their embeddings read out of the index. The refinement is identical; the memory
is proportional to the ambiguity rather than to the library.

ELIMINATION IS RECORDED ON THE RECORD, SCORES ARE NOT.

`fuse` is deterministic and costs microseconds, so a fused score is cheaper to
recompute than to cache, and caching it would create a stale-score problem the
moment a model finishes and changes an input. What IS persisted is the
irreversible-looking half: a record the engine ELIMINATES gets
`exclusion.reasons += below_quality_floor`, because that decision has to be
visible in the library and overridable by the user, not recomputed silently
inside whichever stage happens to ask next.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from ..jobstore import build_job
from .base import StageContext, StageResult, StageStatus, blocked_by

STAGE = "ranking"

_ENGINE_PARAMS = {"engine": "ranking-engine", "version": "1"}


def _iter_records(ctx: StageContext) -> Iterator[dict[str, Any]]:
    for row in ctx.database.connection.execute(
        "SELECT media_id FROM media ORDER BY media_id"
    ).fetchall():
        record = ctx.database.get_media(row[0])
        if record is not None:
            yield record


def run(ctx: StageContext) -> StageResult:
    upstream = ctx.require("ingest")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    from memory_engine_ranking.dedupe import (  # noqa: PLC0415
        Candidate,
        assignments,
        candidate_pairs,
        find_duplicates,
    )
    from memory_engine_ranking.fusion import (  # noqa: PLC0415
        FaceState,
        SignalError,
        eliminate,
        fuse,
        signals_from_media_record,
    )

    records = list(_iter_records(ctx))
    if not records:
        return StageResult(
            stage=STAGE, status=StageStatus.SKIPPED, detail="the library is empty"
        )

    from .analysis import EMBEDDING_SPACES, FACE_STEP, _step_done  # noqa: PLC0415

    space = EMBEDDING_SPACES.get(ctx.settings.embedding_model, (None, 0))[0]

    # -- fusion ----------------------------------------------------------
    fused: dict[str, Any] = {}
    unmeasured: list[str] = []
    eliminated: list[str] = []
    for record in records:
        face_state = (
            FaceState.HAS_FACES
            if (record.get("faces") or {}).get("face_count", 0) > 0
            else FaceState.NO_FACES
        ) if _step_done(record, FACE_STEP) else FaceState.NOT_RUN
        try:
            signals = signals_from_media_record(record, face_state=face_state)
        except SignalError:
            # The classical measures are required by the contract, so their
            # absence means this record was never analysed -- not that it
            # scores badly. It is reported, not defaulted.
            unmeasured.append(record["media_id"])
            continue
        score = fuse(signals)
        fused[record["media_id"]] = score
        if eliminate(signals) is not None:
            eliminated.append(record["media_id"])

    # -- dedupe ----------------------------------------------------------
    base = [
        Candidate(
            media_id=record["media_id"],
            phash_hex=((record.get("perceptual") or {}).get("image_hash") or {}).get("hex"),
            phash_bits=((record.get("perceptual") or {}).get("image_hash") or {}).get(
                "bits", 64
            ),
            # Carried, not defaulted. The record states which algorithm produced
            # its digest and dedupe refuses to compare two that disagree; a
            # library holding records from before and after the phash-dct-64-v2
            # change (issue #14) would otherwise merge unrelated photographs
            # whose hex happened to match.
            phash_algorithm=(
                (record.get("perceptual") or {}).get("image_hash") or {}
            )["algorithm"],
            embedding=None,
            quality=(
                fused[record["media_id"]].value if record["media_id"] in fused else 0.0
            ),
            captured_utc=((record.get("capture") or {}).get("captured_at") or {}).get("utc"),
            favorite=(record.get("user") or {}).get("favorite", False),
        )
        for record in records
        if (record.get("perceptual") or {}).get("image_hash")
    ]

    ambiguous: set[str] = set()
    if space:
        for left, right in candidate_pairs(base):
            ambiguous.add(left)
            ambiguous.add(right)

    candidates = [
        Candidate(
            media_id=candidate.media_id,
            phash_hex=candidate.phash_hex,
            phash_bits=candidate.phash_bits,
            phash_algorithm=candidate.phash_algorithm,
            embedding=(
                ctx.database.vectors.get("media", candidate.media_id, space)
                if candidate.media_id in ambiguous
                else None
            ),
            quality=candidate.quality,
            captured_utc=candidate.captured_utc,
            favorite=candidate.favorite,
        )
        for candidate in base
    ]

    job = build_job(
        job_type="dedupe_cluster",
        scope=ctx.settings.scope,
        params={
            **_ENGINE_PARAMS,
            # The digest covers everything the grouping and the primary choice
            # depend on, not just the id list. Hashing ids alone would give the
            # same job_id after analysis finished and every quality score
            # changed -- so the stage would report "unchanged" and leave the
            # old primaries in place, which is precisely the kind of silent
            # staleness this pipeline is supposed to make impossible.
            "inputs_digest": _digest_of_candidates(candidates),
            "embedding_space": space,
            "embeddings_loaded": len(ambiguous),
        },
        priority=350,
    )
    job = ctx.jobs.get(job["job_id"]) or ctx.jobs.ensure(job)
    if job["state"]["status"] == "completed":
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="the library and its scores are unchanged since the last ranking pass",
            job_id=job["job_id"],
            counts={"media": len(records), "scored": len(fused)},
        )

    job = ctx.jobs.begin(job)
    ctx.reporter.event(
        STAGE,
        "stage_start",
        f"{len(candidates):,} hashable records, {len(ambiguous):,} needing embedding refinement",
    )

    groups = find_duplicates(candidates)
    updates = assignments(groups)
    written = 0
    for media_id, update in updates.items():
        record = ctx.database.get_media(media_id)
        if record is None:  # pragma: no cover
            continue
        record["dedupe"] = update
        ctx.database.put_media(record)
        written += 1

    for media_id in eliminated:
        record = ctx.database.get_media(media_id)
        if record is None:  # pragma: no cover
            continue
        _exclude(record, "below_quality_floor")
        ctx.database.put_media(record)

    counts = {
        "media": len(records),
        "scored": len(fused),
        "unmeasured": len(unmeasured),
        "duplicate_groups": len(groups),
        "duplicates": sum(group.size for group in groups),
        "dedupe_rows_written": written,
        "eliminated": len(eliminated),
        "embeddings_loaded": len(ambiguous),
    }
    ctx.jobs.complete(job)
    ctx.reporter.event(STAGE, "stage_done", "ranking complete", **counts)
    if unmeasured:
        ctx.reporter.event(
            STAGE,
            "note",
            f"{len(unmeasured)} records carry no classical quality and were not scored",
        )
    return StageResult(
        stage=STAGE, status=StageStatus.COMPLETED, detail="ranking complete",
        job_id=job["job_id"], counts=counts,
    )


def _exclude(record: dict[str, Any], reason: str) -> None:
    exclusion = dict(record.get("exclusion") or {})
    reasons = list(exclusion.get("reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    exclusion["reasons"] = reasons
    exclusion["excluded_from_automation"] = True
    exclusion.setdefault("user_override", None)
    record["exclusion"] = exclusion


def _digest_of_candidates(candidates: Any) -> str:
    from ..ids import canonical_json  # noqa: PLC0415
    import blake3  # noqa: PLC0415

    hasher = blake3.blake3()
    for candidate in sorted(candidates, key=lambda item: item.media_id):
        hasher.update(
            canonical_json(
                [
                    candidate.media_id,
                    candidate.phash_hex,
                    candidate.phash_bits,
                    candidate.quality,
                    candidate.captured_utc,
                    candidate.favorite,
                    candidate.embedding is not None,
                ]
            )
        )
    return hasher.hexdigest()


def scores_for(ctx: StageContext, records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Fused scores for a set of records. Shared with the album stage.

    Recomputed rather than cached, deliberately: `fuse` is deterministic and
    microseconds fast, and a cached score goes stale the instant a model
    finishes and changes one of its inputs. A stale score is worse than a
    recomputed one because nothing in the record says it is stale.
    """
    from memory_engine_ranking.fusion import (  # noqa: PLC0415
        FaceState,
        SignalError,
        fuse,
        signals_from_media_record,
    )

    from .analysis import FACE_STEP, _step_done  # noqa: PLC0415

    out: dict[str, Any] = {}
    for record in records:
        face_state = (
            FaceState.HAS_FACES
            if (record.get("faces") or {}).get("face_count", 0) > 0
            else FaceState.NO_FACES
        ) if _step_done(record, FACE_STEP) else FaceState.NOT_RUN
        try:
            out[record["media_id"]] = fuse(
                signals_from_media_record(record, face_state=face_state)
            )
        except SignalError:
            continue
    return out
