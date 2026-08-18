"""The Tier 3 album-selection pass: the seam that joins the other three modules.

`contact_sheet.py` composes what leaves. `transport.py` decides whether it may
leave and carries it. `structured.py` decides whether the reply is an answer.
Each was built and tested alone, and each was written with an explicit note
that the join had not happened yet. This file is that join, and it is the only
file in the package that imports its siblings at module scope -- a seam that
pretended not to depend on the things it joins would be a lie with an import
statement.

WHAT THE PASS ACTUALLY DOES

    local selector picks a shortlist  ->  contact sheet of the shortlist
                                      ->  one frontier call
                                      ->  structured decision against labels
                                      ->  labels resolved to media ids locally

The frontier model NEVER free-picks from the library (CLAUDE.md rule 2). It is
shown a shortlist the local ranking engine already chose, drawn at 256px, and
it answers with grid labels. `album-engine` narrows; the model curates. That
ordering is what bounds the cost, bounds the egress, and makes the comparison
in `docs/tier3-album-taste.md` meaningful: both selections are drawn from the
same pool, so the difference is taste rather than reach.

THE LABEL INDIRECTION IS LOAD-BEARING AND IS RESOLVED HERE

`contact_sheet` draws `A1`..`H8` on the tiles and states that those labels are
the model's entire vocabulary. `transport` was written before that composer
existed and documented the reconciliation it needed: build the `Request` over
the sheet's LABELS, hand the same labels to `transport.ContactSheet.tile_ids`,
and map back through the manifest -- which never leaves the machine -- after
the parser has validated. `plan_taste` does exactly that, in one place, so the
two id spaces cannot drift. A 64-hex media id therefore never crosses the wire.

THE INSPECTION BUNDLE IS A DELIVERABLE, NOT A DEBUG AID

`write_inspection_bundle` runs BEFORE `check_egress`, and therefore before any
socket exists. It writes the composed PNG, the manifest, the consent
declaration, a human summary, and -- crucially -- `request-body.json`, which is
the EXACT bytes the SDK will serialise and send, base64 blob included. Not a
redaction of them, not a summary of them. A person deciding whether to trust
this with photographs of their family has to be able to read the thing that
would be transmitted, and a bundle that showed a tidied version of the payload
would be answering a different question. The egress test scans that same file,
so the artifact a human inspects is the artifact CI asserts on.

MODEL PINNING
`transport.MODEL_ID` is Opus-tier, which is the right default for a taste layer
in general. This pass pins its own id in `MODEL_ID` below, because "which model
answered" must be a code change with a diff (CLAUDE.md rule 7) and the two
decisions -- what the transport defaults to, what the album pass asks for --
are genuinely separate. `TasteDecision.served_model` records what actually
answered, and `model_mismatch` fires when the server served something else, so
a silent swap on the far side is visible on this side.

Python 3.12. Depends on Pillow and blake3 through `contact_sheet`, and on the
`anthropic` SDK only when a real sender is constructed.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from memory_engine_prompt import contact_sheet as sheets
from memory_engine_prompt import structured, transport

__all__ = [
    "ALBUM_PURPOSE",
    "DEFAULT_POOL_MULTIPLIER",
    "DEFAULT_SHEET_POLICY",
    "MODEL_ID",
    "PRICE_TABLE_USD_PER_MTOK",
    "InspectionBundle",
    "JsonlEgressLedger",
    "TasteCandidate",
    "TasteDecision",
    "TasteError",
    "TastePlan",
    "build_instruction",
    "estimate_cost_usd",
    "plan_taste",
    "run_album_taste",
    "write_inspection_bundle",
]


#: The model this pass asks for. Pinned here rather than inherited from
#: `transport.MODEL_ID` so that changing the album layer's model is a diff in
#: the album layer. Verified working on this account; `TasteDecision` records
#: what actually answered so a server-side substitution is not invisible.
MODEL_ID = "claude-sonnet-5"

#: Purpose slug. Feeds the derived request id and every retry hint, so it is
#: part of the request's identity and not decoration.
ALBUM_PURPOSE = "album-selection"

#: How many candidates to show for each one we want back. Three gives the model
#: something to reject -- a sheet of exactly N candidates for N slots is not a
#: judgement call, it is a formality that still costs an upload.
DEFAULT_POOL_MULTIPLIER = 3

#: Composition policy for an album sheet. 40 cells at 256px is the composer's
#: own default and sits well inside every ceiling; stated explicitly so a change
#: to the composer's default cannot silently change what an album sends.
DEFAULT_SHEET_POLICY = sheets.ContactSheetPolicy(
    cell_px=256, columns=6, max_cells=40, label_scale=3, gutter_px=16
)

#: USD per million tokens, by model. Recorded per run so a cost figure in a
#: report can be recomputed from the usage counters rather than believed.
#: `claude-sonnet-5` carries an introductory rate through 2026-08-31; both are
#: listed because a cost claim that silently used one or the other is not a
#: measurement.
PRICE_TABLE_USD_PER_MTOK: Mapping[str, Mapping[str, float]] = {
    "claude-sonnet-5": {
        "input": 3.00,
        "output": 15.00,
        "intro_input": 2.00,
        "intro_output": 10.00,
        "intro_until": 20260831.0,
    },
    "claude-opus-5": {"input": 5.00, "output": 25.00},
}

#: Sent as the caller's task text. Locally authored, every byte of it in this
#: file: `transport.build_request` refuses an `Untrusted` instruction, and this
#: constant is why that refusal has nothing to catch.
_INSTRUCTION = (
    "You are choosing photographs for a printed family album from one event.\n"
    "\n"
    "The contact sheet shows {pool} candidates that a local ranking engine "
    "already judged technically acceptable: they are in focus, correctly "
    "exposed, and none is a near-duplicate of another. Sharpness and exposure "
    "are therefore NOT what you are being asked about, and picking on those "
    "grounds adds nothing to what has already been decided.\n"
    "\n"
    "Choose the {target} that make the best album, and order them the way they "
    "should be printed. Judge on the things a fusion of quality scores cannot "
    "see:\n"
    "  - does the photograph carry a moment, or is it merely a competent record "
    "of a place;\n"
    "  - does the set cover the event rather than repeating its most "
    "photogenic minute;\n"
    "  - do people, place and detail balance across the sequence;\n"
    "  - does the order read as a story rather than as a ranking.\n"
    "\n"
    "Score each pick 0.0 to 1.0 for how strongly it belongs in the album, and "
    "give a short note saying what it contributes that the others do not. "
    "Return exactly {target} items."
)


class TasteError(RuntimeError):
    """This pass could not be planned or run. Not a model failure."""


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TasteCandidate:
    """One shortlisted photograph: its id, its proxy's bytes, and its local rank.

    `classical_rank` is 0-based and is the ORDER THE LOCAL SELECTOR PUT IT IN.
    It never leaves the device -- it is not drawn on the sheet, not listed in
    the prompt, and not in the manifest -- because telling the model what the
    local engine already preferred is how a comparison between the two stops
    being a comparison. It is kept here so the caller can measure agreement
    afterwards, which is the whole point of running both.

    `proxy_bytes` is the encoded proxy file. The composer checks the resolution
    ceiling against the header before a pixel is decoded, so handing over an
    original raises rather than being quietly downscaled.
    """

    media_id: str
    proxy_bytes: bytes
    classical_rank: int

    def __post_init__(self) -> None:
        if not isinstance(self.classical_rank, int) or isinstance(self.classical_rank, bool):
            raise TasteError("classical_rank must be an int")
        if self.classical_rank < 0:
            raise TasteError("classical_rank is 0-based and must not be negative")
        # media_id and proxy_bytes are validated by SheetCandidate, which owns
        # the id grammar and the filename-shaped-id refusal. Re-checking them
        # here would be a second implementation of a rule that already has one.


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


class JsonlEgressLedger(transport.EgressLedger):
    """A durable append-only ledger. One JSON object per line, fsynced.

    `transport.InMemoryLedger` exists for tests and says so in its name. This is
    the one a real run uses, and it is deliberately the dullest possible
    implementation: open, write, flush, fsync, close. No buffering across
    entries, because the failure being defended against is a crash between the
    journal line and the send, and a buffered writer turns that into a send with
    no record -- exactly the ordering `transport.send` is arranged to prevent.

    A write that fails RAISES, and `transport._record` turns that into
    `EgressBlocked`. That is the intended behaviour: a ledger that cannot record
    is a ledger that cannot authorise.
    """

    __slots__ = ("path", "entries")

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict[str, Any]] = []

    def record(self, entry: transport.LedgerEntry) -> None:
        payload = entry.to_mapping()
        line = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        # Opened per entry rather than held open: a long-lived handle across a
        # network call is a handle that can be inherited, and the cost of an
        # open() is irrelevant next to the HTTPS round trip it precedes.
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self.entries.append(payload)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def build_instruction(*, pool: int, target: int) -> str:
    """The task text. Assembled from this file's constant and two integers."""
    if target < 1:
        raise TasteError("target must be at least 1")
    if pool < target:
        raise TasteError(
            f"cannot ask for {target} of {pool}; the shortlist must be larger "
            "than the number wanted, or there is no judgement to make"
        )
    return _INSTRUCTION.format(pool=pool, target=target)


@dataclass(frozen=True)
class TastePlan:
    """Everything needed to make the call, and nothing that required one.

    Pure: composing a plan touches no clock, no socket and no ledger, so a
    caller can build it, write the inspection bundle, look at it, and only then
    decide whether to authorise a send.
    """

    sheet: Any  # contact_sheet.ContactSheet
    payload: transport.ContactSheet
    request: structured.Request
    instruction: str
    target: int
    #: label -> classical rank, local-only. Never serialised into the payload.
    classical_rank_by_label: Mapping[str, int]
    #: label -> media_id, from the manifest. Local-only, same rule.
    media_id_by_label: Mapping[str, str]

    @property
    def manifest(self) -> Any:
        return self.sheet.manifest

    @property
    def labels(self) -> tuple[str, ...]:
        return self.sheet.manifest.labels

    @property
    def classical_selection(self) -> tuple[str, ...]:
        """The media ids the LOCAL selector would have chosen, in its order.

        The comparison baseline. Computed from `classical_rank` rather than from
        the candidate list order so that a caller which shuffles the sheet (a
        legitimate thing to do -- position bias is real) still gets the local
        engine's actual answer here.
        """
        ranked = sorted(
            self.classical_rank_by_label.items(), key=lambda pair: pair[1]
        )
        return tuple(self.media_id_by_label[label] for label, _ in ranked[: self.target])


def plan_taste(
    candidates: Sequence[TasteCandidate],
    *,
    target: int,
    policy: Any = DEFAULT_SHEET_POLICY,
    request_id: str | None = None,
) -> TastePlan:
    """Shortlist -> composed sheet + the request that matches it exactly.

    The `Request` is built by `SheetManifest.request()`, so the object that
    knows what was drawn is the object that decides what a legal reply is.
    Building a second allow-list here from the candidate list would be a second
    thing to keep correct, and `transport.build_request` would only catch the
    disagreement after the divergence already existed.
    """
    items = tuple(candidates)
    if not items:
        raise TasteError("no candidates; there is nothing to choose between")
    for index, candidate in enumerate(items):
        if not isinstance(candidate, TasteCandidate):
            raise TasteError(
                f"candidate {index} is a {type(candidate).__name__}, not a TasteCandidate"
            )
    if not isinstance(target, int) or isinstance(target, bool):
        raise TasteError("target must be an int")
    if target >= len(items):
        raise TasteError(
            f"asked for {target} of {len(items)} candidates; a sheet that is "
            "already the answer is an upload that buys nothing"
        )

    ranks = [candidate.classical_rank for candidate in items]
    if len(set(ranks)) != len(ranks):
        raise TasteError(
            "two candidates share a classical_rank; the local order is the "
            "comparison baseline and a tie makes it unstateable"
        )

    composed = sheets.build_contact_sheet(
        [
            sheets.SheetCandidate(
                media_id=candidate.media_id, proxy_bytes=candidate.proxy_bytes
            )
            for candidate in items
        ],
        policy=policy,
    )
    manifest = composed.manifest
    labels = manifest.labels
    if len(labels) != len(items):
        # Unreachable while the composer refuses to truncate; asserted because
        # a silently short sheet would make every count below a lie.
        raise TasteError(
            f"composer drew {len(labels)} tiles for {len(items)} candidates"
        )

    media_id_by_label = {label: manifest.media_id_for(label) for label in labels}
    rank_by_media_id = {c.media_id: c.classical_rank for c in items}
    classical_rank_by_label = {
        label: rank_by_media_id[media_id_by_label[label]] for label in labels
    }

    instruction = build_instruction(pool=len(labels), target=target)

    request = manifest.request(
        purpose=ALBUM_PURPOSE,
        min_items=target,
        max_items=target,
        require_score=True,
        score_range=(0.0, 1.0),
        request_id=request_id,
    )

    payload = transport.ContactSheet(
        image_bytes=composed.image_bytes,
        media_type="image/png",
        tile_ids=labels,
        # Deliberately empty. `context` is free text the transport will carry,
        # and everything the local pipeline knows about these photographs --
        # capture time, place, who is in them -- is exactly the metadata this
        # boundary exists to keep at home. v0 sends pixels and grid labels and
        # nothing else, which is a claim the egress test can check by scanning
        # the body for markers rather than by reading this comment.
        context="",
    )

    return TastePlan(
        sheet=composed,
        payload=payload,
        request=request,
        instruction=instruction,
        target=target,
        classical_rank_by_label=classical_rank_by_label,
        media_id_by_label=media_id_by_label,
    )


# --------------------------------------------------------------------------
# The inspection bundle
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class InspectionBundle:
    """Where each artefact landed. Every path is inside the caller's directory."""

    directory: Path
    sheet_png: Path
    manifest_json: Path
    request_body_json: Path
    request_summary_json: Path
    consent_json: Path
    ledger_jsonl: Path

    def paths(self) -> tuple[Path, ...]:
        return (
            self.sheet_png,
            self.manifest_json,
            self.request_body_json,
            self.request_summary_json,
            self.consent_json,
            self.ledger_jsonl,
        )


def _grant_to_mapping(grant: transport.EgressGrant) -> dict[str, Any]:
    consent = grant.consent
    return {
        "requires_egress": grant.requires_egress,
        "destination": grant.destination,
        "payload_kind": grant.payload_kind,
        "estimated_bytes": grant.estimated_bytes,
        "consent": None
        if consent is None
        else {
            "ledger_entry_id": consent.ledger_entry_id,
            "scope": consent.scope,
            "granted_at": consent.granted_at,
            "expires_at": consent.expires_at,
            "revoked_at": consent.revoked_at,
        },
    }


def write_inspection_bundle(
    plan: TastePlan,
    prepared: transport.PreparedRequest,
    grant: transport.EgressGrant,
    directory: str | os.PathLike[str],
    *,
    ledger_path: str | os.PathLike[str] | None = None,
) -> InspectionBundle:
    """Write what would be transmitted, before anything is.

    Call this BEFORE `run_album_taste`. It opens no socket and consults no
    clock, so it can be run on a machine with the network unplugged and the
    result is the same bytes that a connected machine would send.

    `request-body.json` is `prepared.body_bytes` VERBATIM -- the base64 image
    included. It is large and it is meant to be: a bundle that showed a tidied
    payload would be evidence about a payload nobody sends. `contact-sheet.png`
    is asserted to be byte-identical to the image embedded in that body, so a
    person can look at the PNG and know they have looked at the upload.
    """
    if not isinstance(plan, TastePlan):
        raise TasteError("plan must be a TastePlan")
    if not isinstance(prepared, transport.PreparedRequest):
        raise TasteError("prepared must be a transport.PreparedRequest")
    if not isinstance(grant, transport.EgressGrant):
        raise TasteError("grant must be a transport.EgressGrant")

    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    sheet_png = target_dir / "contact-sheet.png"
    manifest_json = target_dir / "contact-sheet-manifest.json"
    request_body_json = target_dir / "request-body.json"
    request_summary_json = target_dir / "request-summary.json"
    consent_json = target_dir / "consent.json"
    ledger_jsonl = Path(ledger_path) if ledger_path else target_dir / "egress-ledger.jsonl"

    image_bytes = plan.sheet.image_bytes
    embedded = _embedded_image_bytes(prepared.body)
    if embedded != image_bytes:
        # The one assertion that makes the bundle mean anything: if these two
        # ever diverge, the PNG a human inspected is not the picture that would
        # be uploaded, and every other guarantee in this file is decoration.
        raise TasteError(
            "the image embedded in the request body is not the composed sheet; "
            "refusing to write an inspection bundle that would mislead"
        )

    sheet_png.write_bytes(image_bytes)
    manifest_json.write_text(plan.manifest.to_json(), encoding="utf-8")
    request_body_json.write_bytes(prepared.body_bytes)
    consent_json.write_text(
        json.dumps(_grant_to_mapping(grant), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = {
        "purpose": prepared.purpose,
        "request_id": prepared.request_id,
        "model": prepared.model,
        "cache_key": prepared.cache_key,
        "sheet_image_digest": plan.manifest.image_digest,
        "sheet_pixel_digest": plan.manifest.pixel_digest,
        "sheet_digest_transport": prepared.sheet_digest,
        "payload_bytes": prepared.payload_bytes,
        "request_body_bytes": len(prepared.body_bytes),
        "tile_count": prepared.tile_count,
        "labels_sent": list(plan.labels),
        "items_wanted": plan.target,
        "instruction": plan.instruction,
        "system_prompt": transport.SYSTEM_PROMPT,
        "context_sent": plan.payload.context,
        "sheet_size_px": [plan.manifest.image_width, plan.manifest.image_height],
        "cell_px": plan.manifest.cell_px,
        "metadata_fields_removed_per_tile": [
            cell.metadata_fields_removed for cell in plan.manifest.cells
        ],
        "png_chunk_types": list(sheets.png_chunk_types(image_bytes)),
        # The local half. Written into the bundle because the person inspecting
        # it needs to know which photograph a label refers to; NOT sent, which
        # is what request-body.json is there to let them verify.
        "label_to_media_id_LOCAL_ONLY": dict(plan.media_id_by_label),
        "label_to_classical_rank_LOCAL_ONLY": dict(plan.classical_rank_by_label),
    }
    request_summary_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Create the ledger file even when empty, so "no entry was written" is a
    # visible empty file rather than an absent one a reader might not miss.
    ledger_jsonl.parent.mkdir(parents=True, exist_ok=True)
    ledger_jsonl.touch(exist_ok=True)

    return InspectionBundle(
        directory=target_dir,
        sheet_png=sheet_png,
        manifest_json=manifest_json,
        request_body_json=request_body_json,
        request_summary_json=request_summary_json,
        consent_json=consent_json,
        ledger_jsonl=ledger_jsonl,
    )


def _embedded_image_bytes(body: Mapping[str, Any]) -> bytes:
    """Pull the base64 image back out of the built body, or raise.

    Walks the structure rather than trusting an index: the body shape is built
    a few lines away in `transport.build_request`, and an assertion that
    silently found nothing would pass on a body with no image at all.
    """
    messages = body.get("messages")
    if not isinstance(messages, Sequence) or not messages:
        raise TasteError("request body carries no messages")
    found: list[bytes] = []
    for message in messages:
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, Sequence):
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "image":
                continue
            source = block.get("source")
            if not isinstance(source, Mapping):
                raise TasteError("image block has no source")
            data = source.get("data")
            if not isinstance(data, str):
                raise TasteError("image source carries no base64 data")
            found.append(base64.standard_b64decode(data))
    if len(found) != 1:
        raise TasteError(f"expected exactly one image in the body, found {len(found)}")
    return found[0]


# --------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TasteDecision:
    """What came back, what it cost, and what it means locally.

    `selected` is empty whenever the reply was not usable. It is never a
    truncated or repaired version of a bad answer: `structured.parse_reply`
    refuses those, and a caller that wants the salvage has `parse_status` and
    `rejections` to make that choice explicitly.
    """

    outcome: str
    ok: bool
    selected: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    scores: tuple[float | None, ...] = ()
    notes: tuple[str, ...] = ()
    reply_notes: str = ""
    parse_status: str | None = None
    rejections: tuple[str, ...] = ()
    retry_hint: str = ""
    requested_model: str = ""
    served_model: str | None = None
    model_mismatch: bool = False
    request_id: str = ""
    cache_key: str = ""
    sheet_digest: str = ""
    message_id: str | None = None
    stop_reason: str | None = None
    refusal_category: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    attempts: int = 0
    detail: str = ""
    classical_selection: tuple[str, ...] = ()

    @property
    def agreement(self) -> float | None:
        """Fraction of the model's picks the local selector also chose.

        `None` when there is no usable selection, rather than 0.0: "the model
        agreed with nothing" and "the model returned nothing" are different
        results, and a 0.0 in a report would read as the first.
        """
        if not self.selected or not self.classical_selection:
            return None
        overlap = set(self.selected) & set(self.classical_selection)
        return len(overlap) / len(self.selected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "ok": self.ok,
            "selected": list(self.selected),
            "labels": list(self.labels),
            "scores": list(self.scores),
            "notes": list(self.notes),
            "reply_notes": self.reply_notes,
            "parse_status": self.parse_status,
            "rejections": list(self.rejections),
            "retry_hint": self.retry_hint,
            "requested_model": self.requested_model,
            "served_model": self.served_model,
            "model_mismatch": self.model_mismatch,
            "request_id": self.request_id,
            "cache_key": self.cache_key,
            "sheet_digest": self.sheet_digest,
            "message_id": self.message_id,
            "stop_reason": self.stop_reason,
            "refusal_category": self.refusal_category,
            "usage": dict(self.usage),
            "cost_usd": self.cost_usd,
            "attempts": self.attempts,
            "detail": self.detail,
            "classical_selection": list(self.classical_selection),
            "agreement": self.agreement,
        }


def estimate_cost_usd(
    model: str, usage: Mapping[str, Any], *, on: datetime | None = None
) -> float | None:
    """USD for one call from its own usage counters, or None if unpriced.

    Returns None rather than 0.0 for an unknown model or absent counters. A
    zero in a cost report is a claim that the call was free; None is the truth,
    which is that nobody knows.
    """
    prices = PRICE_TABLE_USD_PER_MTOK.get(model)
    if prices is None:
        return None
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    if not isinstance(inp, int) or not isinstance(out, int):
        return None
    in_rate, out_rate = prices["input"], prices["output"]
    intro_until = prices.get("intro_until")
    if intro_until is not None:
        moment = on or transport.utc_now()
        stamp = float(moment.strftime("%Y%m%d"))
        if stamp <= intro_until:
            in_rate = prices["intro_input"]
            out_rate = prices["intro_output"]
    # Cache tokens are billed differently; they are reported in `usage` and
    # deliberately not folded in here, because a blended number nobody can
    # decompose is worse than a number that states what it covers.
    return (inp / 1_000_000.0) * in_rate + (out / 1_000_000.0) * out_rate


def run_album_taste(
    plan: TastePlan,
    *,
    grant: transport.EgressGrant,
    ledger: transport.EgressLedger,
    sender: Any,
    model: str = MODEL_ID,
    config: transport.TransportConfig | None = None,
    clock: Any = transport.utc_now,
    sleep: Any = None,
) -> TasteDecision:
    """Send one planned request and turn the reply into media ids.

    `transport.EgressBlocked` is NOT caught. A caller that confused "no bytes
    left the device" with "the model said no" would have a privacy bug wearing
    a reliability bug's clothes, so the two arrive by different mechanisms: one
    as an exception, one as a `TasteDecision` whose `ok` is False.
    """
    if not isinstance(plan, TastePlan):
        raise TasteError("plan must be a TastePlan")
    if config is None:
        config = transport.TransportConfig(model=model)
    elif config.model != model:
        raise TasteError(
            f"config names model {config.model!r} but the call asked for {model!r}; "
            "one of the two is wrong and guessing which would be a silent swap"
        )

    kwargs: dict[str, Any] = {"config": config, "clock": clock}
    if sleep is not None:
        kwargs["sleep"] = sleep
    carrier = transport.FrontierTransport(sender, ledger, **kwargs)

    # Prepared BEFORE the send, not recomputed after it. `build_request` is
    # pure, so a second call would return the same bytes -- but "would" is a
    # claim about a function that could stop being pure, and the digest this
    # decision reports has to be the digest of the thing that was sent.
    prepared = carrier.prepare(plan.payload, plan.request, plan.instruction)

    result = carrier.send(
        plan.payload,
        plan.request,
        instruction=plan.instruction,
        grant=grant,
    )

    usage = dict(result.usage)
    common: dict[str, Any] = {
        "requested_model": config.model,
        "served_model": result.model,
        "model_mismatch": bool(result.model) and result.model != config.model,
        "request_id": result.request_id,
        "cache_key": result.cache_key,
        "sheet_digest": prepared.sheet_digest,
        "message_id": result.message_id,
        "stop_reason": result.stop_reason,
        "refusal_category": result.refusal_category,
        "usage": usage,
        "cost_usd": estimate_cost_usd(config.model, usage),
        "attempts": len(result.attempts),
        "classical_selection": plan.classical_selection,
    }

    if not result.completed:
        # TRUNCATED, REFUSED, RATE_LIMITED, ... all land here with no selection.
        # `text_for_parser` would refuse the body anyway; not calling it keeps
        # the reason in the outcome rather than turning every one of them into
        # `malformed_json`, which is true and useless.
        return TasteDecision(
            outcome=result.outcome.value,
            ok=False,
            detail=result.report(),
            **common,
        )

    parsed = structured.parse_reply(result.text_for_parser(), plan.request)

    rejections = tuple(rejection.describe() for rejection in parsed.rejections)
    hint = parsed.retry_hint(plan.request) if rejections else ""

    if parsed.status is structured.Status.REJECTED:
        # Refused whole. Not partially applied, not repaired: an unknown id or a
        # miscount means the model answered a different question, and the ORDER
        # of whatever else came back -- which is the album's sequence -- cannot
        # be trusted either.
        return TasteDecision(
            outcome=result.outcome.value,
            ok=False,
            parse_status=parsed.status.value,
            rejections=rejections,
            retry_hint=hint,
            detail=parsed.report(),
            **common,
        )

    items = parsed.items
    labels = tuple(item.id for item in items)
    media_ids = plan.manifest.resolve(parsed)
    if len(media_ids) != len(labels):  # pragma: no cover - manifest invariant
        raise TasteError("manifest resolved a different number of ids than labels")

    return TasteDecision(
        outcome=result.outcome.value,
        ok=parsed.status is structured.Status.OK,
        selected=media_ids,
        labels=labels,
        scores=tuple(item.score for item in items),
        notes=tuple(
            "" if item.note is None else item.note.for_display() for item in items
        ),
        reply_notes="" if parsed.notes is None else parsed.notes.for_display(2000),
        parse_status=parsed.status.value,
        rejections=rejections,
        retry_hint=hint,
        detail=parsed.report(),
        **common,
    )
