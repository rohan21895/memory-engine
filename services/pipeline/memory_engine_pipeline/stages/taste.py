"""The Tier 3 taste pass: the only stage in this pipeline that opens a socket.

Everything else here runs on the user's machine against local proxies. This
stage composes a contact sheet from the same shortlist the album stage lays
out, sends it to a frontier model, and turns the reply back into media ids.
`packages/prompt-engine/album_taste.py` owns every decision about what leaves
and whether it may; this file supplies inputs, writes the inspection bundle
where a person can find it, and reports.

OFF UNLESS THREE THINGS ARE TRUE, AND EACH ABSENCE IS NAMED SEPARATELY

    --tier3            the operator asked for it
    a key              ANTHROPIC_API_KEY in the environment or in .env
    a consent record   a ConsentRef on disk, pointing at a ledger entry

Any one missing SKIPS with the specific reason. Not one flag that means all
three, and not a single "tier3 unavailable": "you did not ask", "there is no
key" and "nobody consented" are three different situations for the person
reading the summary, and only the third is about privacy.

The default is off. A pipeline run with no flags does not touch the network,
which is why `--tier3` exists at all rather than the stage inferring intent
from the presence of a key -- a key in the environment for some other tool is
not a decision about this user's photographs.

WHAT `--tier3-dry-run` IS FOR, AND WHY IT IS NOT A TEST MODE

It composes the sheet, builds the exact request body, writes the whole
inspection bundle, and stops before `check_egress` -- so no consent is even
consulted, because nothing is going anywhere. That is the mode a person uses
to decide whether to trust this with real photographs: look at
`contact-sheet.png`, read `request-body.json`, then decide. The artefacts it
writes are byte-identical to the ones a live run writes, because both come
from the same `plan_taste` + `write_inspection_bundle` calls in this file.

THE SELECTION IS PRODUCED, AND DELIBERATELY NOT CONSUMED BY `album`

`outputs/taste/<digest>.json` holds the model's picks, the classical picks, and
the agreement between them. The album stage does NOT read it, and that is a
decision rather than an omission: `AlbumSpec.SelectionReport` has no field that
can record "a cloud model chose these photographs", so a book laid out from
this selection would be a book that does not say where its selection came from.
CLAUDE.md rule 7 is "no silent anything". Wiring the two together needs a
contract change, which needs both agents -- see contracts issue in the branch
notes. Until then the pass answers the question and the answer is on disk.
"""

from __future__ import annotations

import json
import os
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

STAGE = "taste"
PASS_NAME = "tier3-album-taste"
PASS_VERSION = "0.1.0"

#: Where the operator's consent record lives when they do not name one. A file
#: rather than a flag: a boolean on a command line is a claim by whoever typed
#: it, and `ConsentRef` wants a ledger entry id, a scope and a grant time that
#: something else issued.
DEFAULT_CONSENT_FILENAME = "tier3-consent.json"

_ENV_KEY = "ANTHROPIC_API_KEY"


def _skipped(detail: str, **counts: Any) -> StageResult:
    return StageResult(
        stage=STAGE, status=StageStatus.SKIPPED, detail=detail, counts=counts or {}
    )


def _failed(detail: str, job_id: str | None = None, **counts: Any) -> StageResult:
    return StageResult(
        stage=STAGE,
        status=StageStatus.FAILED,
        detail=detail,
        job_id=job_id,
        counts=counts or {},
    )


# --------------------------------------------------------------------- key --


def api_key_present(repo_root: Path) -> bool:
    """Whether a key can be found, WITHOUT returning or logging it.

    Returns a bool on purpose. A helper that handed back the key would put it
    in a local variable in a stage function, one f-string away from an event
    line, and this file writes events to a JSONL a user can send us. The SDK
    reads `ANTHROPIC_API_KEY` from the environment itself; this stage's only
    job is to know whether it is there.
    """
    if os.environ.get(_ENV_KEY, "").strip():
        return True
    return _key_in_dotenv(repo_root / ".env")


def load_dotenv_key(repo_root: Path) -> bool:
    """Copy the key from .env into the environment if it is not already there.

    Returns whether a key is now available. The value never leaves `os.environ`
    -- it is not returned, not logged, and not stored on the context.
    """
    if os.environ.get(_ENV_KEY, "").strip():
        return True
    path = repo_root / ".env"
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() != _ENV_KEY:
            continue
        value = value.strip().strip("'\"")
        if value:
            os.environ[_ENV_KEY] = value
            return True
    return False


def _key_in_dotenv(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == _ENV_KEY and value.strip().strip("'\""):
            return True
    return False


# ----------------------------------------------------------------- consent --


def consent_path(ctx: StageContext) -> Path:
    configured = getattr(ctx.settings, "tier3_consent_path", None)
    if configured:
        return Path(configured).expanduser()
    return ctx.workdir / DEFAULT_CONSENT_FILENAME


def load_consent(path: Path) -> Any:
    """Read a ConsentRef from disk, or raise `EgressBlocked`.

    Parsing is `transport.ConsentRef.from_mapping`, which is the same reader
    the egress check uses -- so a malformed record is refused by the module
    that owns the refusal codes rather than by a second opinion in a service.
    """
    from memory_engine_prompt.transport import (  # noqa: PLC0415
        BLOCK_CONSENT_MALFORMED,
        ConsentRef,
        EgressBlocked,
    )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EgressBlocked(
            BLOCK_CONSENT_MALFORMED,
            f"consent record at {path.name} could not be read: {type(error).__name__}",
        ) from error
    if not isinstance(raw, Mapping):
        raise EgressBlocked(BLOCK_CONSENT_MALFORMED, "consent record is not an object")
    return ConsentRef.from_mapping(raw)


# --------------------------------------------------------------- clearance --


DEFAULT_CLEARANCE_FILENAME = "tier3-clearance.json"


def clearance_path(ctx: StageContext) -> Path:
    configured = getattr(ctx.settings, "tier3_clearance_path", None)
    if configured:
        return Path(configured).expanduser()
    return ctx.workdir / DEFAULT_CLEARANCE_FILENAME


def load_clearance(path: Path) -> Mapping[str, Any] | None:
    """The safety-clearance manifest for this send, or None.

    None is NOT a pass: the transport's safety gate rejects it out loud, which
    is the correct state of the world until the on-device safety classifier
    exists and writes real manifests. This loader deliberately does not
    validate the document -- `memory_engine_safety.verify` owns that, and it
    runs inside the gate against the exact media ids on the sheet. A malformed
    file is therefore handed through and refused THERE, with the gate's own
    reason, rather than being silently downgraded to "no clearance" here --
    the difference matters to an operator debugging why their manifest was
    rejected.
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        # Not JSON at all. Handing the parser's ruin through as a mapping is
        # impossible, and None ("there is no clearance") is the truthful
        # description of a file nothing can read.
        return None
    return raw if isinstance(raw, Mapping) else None


# --------------------------------------------------------------- the stage --


def run(ctx: StageContext) -> StageResult:
    settings = ctx.settings
    if not getattr(settings, "tier3_enabled", False):
        return _skipped(
            "not requested; Tier 3 sends a low-resolution contact sheet to a cloud "
            "model and is off unless --tier3 is passed"
        )

    upstream = ctx.require("ingest", "analysis", "faces", "ranking")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    dry_run = bool(getattr(settings, "tier3_dry_run", False))

    # The key check comes before any work: composing a sheet for a run that
    # cannot send it is a waste, EXCEPT in dry-run, whose entire purpose is to
    # compose without sending.
    if not dry_run and not load_dotenv_key(ctx.repo_root):
        return _skipped(
            f"no {_ENV_KEY} in the environment or in .env; Tier 3 cannot send and "
            "will not compose a sheet it has nowhere to send. Use --tier3-dry-run "
            "to inspect what would be transmitted without a key"
        )

    from . import album  # noqa: PLC0415

    planned = album.plan_selection(ctx, stage=STAGE)
    if isinstance(planned, StageResult):
        return planned

    from memory_engine_prompt import album_taste  # noqa: PLC0415
    from memory_engine_prompt.transport import EgressBlocked  # noqa: PLC0415

    target = planned.wanted
    pool_size = min(
        len(planned.candidates),
        target * max(1, int(getattr(settings, "tier3_pool_multiplier", 3))),
        album_taste.DEFAULT_SHEET_POLICY.max_cells,
    )
    if pool_size <= target:
        return _skipped(
            f"the event yields {len(planned.candidates)} scoreable candidates and the "
            f"album wants {target}; a shortlist that is already the answer is an "
            "upload that buys nothing",
            candidates=len(planned.candidates),
            target=target,
        )

    # The shortlist is the local selector's own top `pool_size`, in its order.
    # `Selection.by_standing` is the ranked view; `selected` is chronological,
    # and ranking the sheet by print order would hand the model the local
    # engine's sequence as a hint.
    standing = tuple(planned.selection.by_standing)
    ranked_pool, incomparable = _shortlist(planned, standing, pool_size)
    if len(ranked_pool) <= target:
        # Not a failure: the library genuinely does not hold enough comparably
        # measured candidates to make this a judgement. Reported with the
        # incomparable count so "we could not compare these" is visible rather
        # than looking like "the event was small".
        return _skipped(
            f"only {len(ranked_pool)} candidate(s) are measured comparably with the "
            f"selection and the album wants {target}; a sheet that is already the "
            "answer is an upload that buys nothing",
            candidates=len(planned.candidates),
            comparable=len(ranked_pool),
            incomparable=incomparable,
            target=target,
        )
    pool_size = len(ranked_pool)

    candidates, missing = _read_candidates(ctx, ranked_pool)
    if missing:
        return _failed(
            f"{len(missing)} shortlisted photo(s) have no readable thumbnail_512 "
            "proxy, so the sheet would misrepresent the shortlist: "
            + "; ".join(missing[:3]),
            proxies_missing=len(missing),
        )

    inputs_digest = digest_of(
        {
            "pass": PASS_NAME,
            "pass_version": PASS_VERSION,
            "model": settings.tier3_model,
            "target": target,
            "shortlist": list(ranked_pool),
            "policy": {
                "cell_px": album_taste.DEFAULT_SHEET_POLICY.cell_px,
                "columns": album_taste.DEFAULT_SHEET_POLICY.columns,
                "max_cells": album_taste.DEFAULT_SHEET_POLICY.max_cells,
            },
        }
    )

    bundle_dir = ctx.workdir / "outputs" / "taste" / inputs_digest
    output_path = ctx.workdir / "outputs" / "taste" / f"{inputs_digest}.json"

    try:
        plan = album_taste.plan_taste(
            candidates,
            target=target,
            policy=album_taste.DEFAULT_SHEET_POLICY,
            # The job identity IS the request id. Two concurrent runs over the
            # same shortlist would otherwise share the derived id and a crossed
            # reply would be undetectable -- `structured.Request` documents that
            # limitation and asks callers who fan out to pass one explicitly.
            request_id=inputs_digest,
        )
    except album_taste.TasteError as error:
        return _failed(f"the taste pass could not be planned: {error}")

    ledger = album_taste.JsonlEgressLedger(bundle_dir / "egress-ledger.jsonl")

    from memory_engine_prompt.transport import (  # noqa: PLC0415
        EgressGrant,
        FrontierTransport,
        TransportConfig,
    )

    config = TransportConfig(
        model=settings.tier3_model,
        allow_development_clearance=bool(
            getattr(settings, "tier3_allow_development_clearance", False)
        ),
    )

    consent_file = consent_path(ctx)
    consent = None
    consent_error: str | None = None
    if consent_file.is_file():
        try:
            consent = load_consent(consent_file)
        except EgressBlocked as blocked:
            consent_error = f"{blocked.code}: {blocked.detail}"
    elif not dry_run:
        return _skipped(
            f"no consent record at {consent_file}; Tier 3 uploads a contact sheet of "
            "the user's photographs and absence blocks rather than defaults. Write a "
            "ConsentRef there (scope tier3_contact_sheet) to authorise it",
            shortlist=len(candidates),
        )

    grant = EgressGrant(
        requires_egress=True,
        consent=consent,
        destination="tier3_inference",
        payload_kind="contact_sheet",
        estimated_bytes=len(plan.sheet.image_bytes),
    )

    # The bundle is written BEFORE the egress check and therefore before any
    # socket could exist. That ordering is the deliverable: a person can look
    # at exactly what would be transmitted whether or not it ever is.
    prepared = FrontierTransport(_never_sends, ledger, config=config).prepare(
        plan.payload, plan.request, plan.instruction
    )
    bundle = album_taste.write_inspection_bundle(
        plan, prepared, grant, bundle_dir, ledger_path=ledger.path
    )
    ctx.reporter.event(
        STAGE,
        "stage_progress",
        f"composed a {len(candidates)}-tile sheet; the exact request body is at "
        f"{bundle.request_body_json}",
        payload_bytes=prepared.payload_bytes,
        body_bytes=len(prepared.body_bytes),
    )

    comparison = {
        "schema_version": "v0",
        "pass": PASS_NAME,
        "pass_version": PASS_VERSION,
        "inputs_digest": inputs_digest,
        "generated_at": utc_now(),
        "event": {"size": planned.event.size, "media_ids": list(planned.event.media_ids)},
        "shortlist": list(ranked_pool),
        # Survivors excluded from the sheet because their fused score is not
        # comparable with the selection's. Recorded so a reader can tell a small
        # shortlist from a filtered one.
        "incomparable_candidates": incomparable,
        "target": target,
        "classical_selection": list(plan.classical_selection),
        "inspection_bundle": str(bundle.directory),
    }

    if dry_run:
        comparison["decision"] = None
        comparison["dry_run"] = True
        comparison["consent_error"] = consent_error
        write_json_atomically(output_path, comparison)
        # A consent record that exists and is unusable is reported HERE too,
        # even though a dry run never consults consent. The bundle's
        # consent.json shows `consent: null`, which is indistinguishable from
        # "no record on disk" -- and a person inspecting the bundle to decide
        # whether to authorise a real upload would read that as "I have not
        # written one yet" rather than "the one I wrote is broken".
        note = (
            "" if consent_error is None
            else f" NOTE: the consent record on disk is unusable ({consent_error})."
        )
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail=(
                f"dry run: composed and wrote the exact request body without sending. "
                f"Inspect {bundle.directory}.{note}"
            ),
            counts={
                "shortlist": len(candidates),
                "incomparable": incomparable,
                "target": target,
                "payload_bytes": prepared.payload_bytes,
            },
            outputs=(str(bundle.request_body_json), str(output_path)),
        )

    if consent_error is not None:
        return _failed(
            f"the consent record at {consent_file.name} is not usable ({consent_error}); "
            "a malformed consent is refused rather than treated as absent",
            shortlist=len(candidates),
        )

    job = build_job(
        job_type="tier3_request",
        scope=settings.scope,
        params={
            "pass": PASS_NAME,
            "pass_version": PASS_VERSION,
            "model": settings.tier3_model,
            "inputs_digest": inputs_digest,
        },
        media_ids=list(ranked_pool),
        priority=200,
        egress={
            "requires_egress": True,
            "consent": _consent_mapping(consent),
            "destination": "tier3_inference",
            "payload_kind": "contact_sheet",
            "estimated_bytes": len(plan.sheet.image_bytes),
        },
    )
    job = ctx.jobs.get(job["job_id"]) or ctx.jobs.ensure(job)
    if job["state"]["status"] == "completed" and output_path.is_file():
        stored = json.loads(output_path.read_text(encoding="utf-8"))
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="the same shortlist was already reviewed; the reply is on disk",
            job_id=job["job_id"],
            counts={"selected": len((stored.get("decision") or {}).get("selected", []))},
            outputs=(str(output_path),),
        )
    job = ctx.jobs.begin(job)

    from memory_engine_prompt.transport import AnthropicSender  # noqa: PLC0415
    from memory_engine_safety.verify import PublicationBlocked  # noqa: PLC0415

    try:
        decision = album_taste.run_album_taste(
            plan,
            grant=grant,
            ledger=ledger,
            sender=AnthropicSender(),
            clearance=load_clearance(clearance_path(ctx)),
            model=settings.tier3_model,
            config=config,
        )
    except EgressBlocked as blocked:
        ctx.jobs.fail(
            job, code="permission_denied", message=blocked.code, retryable=False
        )
        return _failed(
            f"egress refused before anything was sent: {blocked.code} ({blocked.detail})",
            job_id=job["job_id"],
        )
    except PublicationBlocked as blocked:
        # Deliberately NOT folded into the EgressBlocked arm: consent covers
        # whether a sheet may be sent, clearance covers what is on it. Until
        # the on-device safety classifier writes real manifests, this is the
        # expected terminal state of a Tier 3 run on real photographs.
        ctx.jobs.fail(
            job, code="permission_denied", message="safety_clearance", retryable=False
        )
        return _failed(
            "the safety gate refused the send before any bytes moved: "
            f"{blocked}. Provide a clearance manifest at "
            f"{clearance_path(ctx)} covering every photograph on the sheet",
            job_id=job["job_id"],
        )

    comparison["decision"] = decision.to_dict()
    comparison["dry_run"] = False
    write_json_atomically(output_path, comparison)

    counts = {
        "shortlist": len(candidates),
        "incomparable": incomparable,
        "target": target,
        "selected": len(decision.selected),
        "input_tokens": decision.usage.get("input_tokens"),
        "output_tokens": decision.usage.get("output_tokens"),
        "cost_usd": decision.cost_usd,
        "agreement_with_classical": decision.agreement,
    }

    if decision.model_mismatch:
        # A server that served a different model than we asked for is a silent
        # model swap on the far side, which CLAUDE.md rule 7 forbids on this
        # side. Reported as a failure rather than a note: the decision came
        # from something nobody pinned.
        ctx.jobs.fail(job, code="validation_failed", message="model_mismatch",
                      retryable=False)
        return _failed(
            f"asked for {decision.requested_model} and {decision.served_model} "
            "answered; refusing to record a decision from an unpinned model",
            job_id=job["job_id"],
            **counts,
        )

    if not decision.selected:
        ctx.jobs.fail(
            job, code="validation_failed", message=decision.outcome, retryable=False
        )
        return _failed(
            f"the reply was not usable ({decision.outcome}/{decision.parse_status}): "
            + decision.detail,
            job_id=job["job_id"],
            **counts,
        )

    # Completed with NO declared outputs. `JobOutput.kind` enumerates contract
    # artefacts and there is no value for a Tier 3 decision, so inventing one
    # here would be a schema violation dressed as provenance. The file's path
    # is on the StageResult instead, and the missing enum value is the same
    # contract gap that keeps `album` from consuming this selection.
    ctx.jobs.complete(job)
    ctx.reporter.event(STAGE, "stage_done", "frontier selection recorded", **counts)
    return StageResult(
        stage=STAGE,
        status=StageStatus.COMPLETED,
        detail=(
            f"{len(decision.selected)} of {len(candidates)} chosen by "
            f"{decision.served_model}; {decision.parse_status}"
        ),
        job_id=job["job_id"],
        counts=counts,
        outputs=(str(output_path), str(bundle.request_body_json)),
    )


def _never_sends(prepared: Any) -> Any:  # pragma: no cover - constructor argument
    """A sender for the transport built only to call `prepare`.

    `FrontierTransport` requires a sender at construction; this one raises so
    that a future edit which accidentally calls `send` on that instance fails
    loudly instead of reaching the network from the bundle-writing path.
    """
    raise AssertionError(
        "this transport exists only to build a request body for inspection"
    )


def _shortlist(
    planned: Any, standing: tuple[str, ...], size: int
) -> tuple[tuple[str, ...], int]:
    """The local engine's top `size` media ids, in its own ranked order.

    WHY THIS DOES NOT JUST SORT BY SCORE

    `by_standing` covers only the SELECTED set, so the rest of the shortlist has
    to come from the survivors -- and the obvious way to pick them, sorting
    every candidate by `score.value`, is wrong here for the reason
    `album-engine/selection.py` states at length: a fused score is only
    meaningful against another score measured the same way. `FusedScore.
    comparable_with` and `SelectionCandidate.measurement_key` exist because a
    landscape scored on seven signals and a portrait scored on eight produce two
    numbers that look like they can be ordered and cannot. `select` refuses that
    comparison; a shortlist builder that quietly performs it would put a photo
    on the sheet because its score was measured on fewer signals, and the
    resulting "the model disagreed with the local engine" comparison would be
    measuring our own bug.

    So the fill is restricted to the measurement class of the selection itself,
    ranked within that class. Candidates measured differently are COUNTED and
    returned, not silently dropped: the caller reports the number, because a
    shortlist that is smaller than asked for is a fact about the library and
    "we could not compare these" must not read as "these were not good enough".

    Returns (shortlist, incomparable_count).
    """
    ordered = list(standing)
    seen = set(ordered)
    by_id = {candidate.media_id: candidate for candidate in planned.candidates}

    reference_keys = {
        by_id[media_id].measurement_key()
        for media_id in standing
        if media_id in by_id
    }
    if not reference_keys:  # pragma: no cover - standing comes from candidates
        return tuple(ordered[:size]), 0

    comparable = []
    incomparable = 0
    for candidate in planned.candidates:
        if candidate.media_id in seen:
            continue
        if candidate.measurement_key() in reference_keys:
            comparable.append(candidate)
        else:
            incomparable += 1

    # Explicit tiebreak on media_id, matching selection.py: two equally scored
    # photos must not have their order decided by dict iteration.
    comparable.sort(key=lambda c: (-float(c.score.value), c.media_id))
    ordered.extend(candidate.media_id for candidate in comparable)
    return tuple(ordered[:size]), incomparable


def _read_candidates(
    ctx: StageContext, media_ids: tuple[str, ...]
) -> tuple[list[Any], list[str]]:
    """Shortlisted ids -> TasteCandidates, plus the ids whose proxy is unreadable.

    Reads the thumbnail_512 proxy from disk here, with the path in hand, which
    is where a missing file produces a useful message. `SheetCandidate` has no
    path field at all, so a path cannot leak into the sheet because it never
    enters the composer.
    """
    from memory_engine_prompt.album_taste import TasteCandidate  # noqa: PLC0415

    candidates: list[Any] = []
    missing: list[str] = []
    for rank, media_id in enumerate(media_ids):
        record = ctx.database.get_media(media_id)
        proxy = None
        for entry in (record or {}).get("proxies") or []:
            if entry.get("kind") == "thumbnail_512":
                proxy = entry
                break
        if proxy is None:
            missing.append(f"{media_id[:12]} has no thumbnail_512")
            continue
        path = Path(proxy["path"])
        if not path.is_absolute():
            path = ctx.workdir / path
        try:
            payload = path.read_bytes()
        except OSError as error:
            missing.append(f"{media_id[:12]}: {type(error).__name__}")
            continue
        candidates.append(
            TasteCandidate(
                media_id=media_id, proxy_bytes=payload, classical_rank=rank
            )
        )
    return candidates, missing


def _consent_mapping(consent: Any) -> dict[str, Any] | None:
    if consent is None:
        return None
    return {
        "ledger_entry_id": consent.ledger_entry_id,
        "scope": consent.scope,
        "granted_at": consent.granted_at,
        "expires_at": consent.expires_at,
        "revoked_at": consent.revoked_at,
    }
