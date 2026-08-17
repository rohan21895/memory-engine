"""Contact-sheet composition — the privacy boundary of the whole product.

This is the only path in the system where a user's imagery leaves their machine.
Everything else is local. So this module is written on the assumption that it
will eventually be wrong, and tries to make the ways it can be wrong *loud* and
*small* rather than trusting itself to be right.

WHAT THIS MODULE PRODUCES, AND WHAT IT DOES NOT

It produces a PLAN: which candidates, at which grid position, at what maximum
tile resolution, under which opaque handle. It never touches a pixel. Rendering
the JPEG belongs to a worker (AGENTS.md territory), and that split is what lets
this module be pure, deterministic and exhaustively testable.

THE FIVE STRUCTURAL SAFEGUARDS

1. A RESOLUTION CEILING A CALLER CANNOT RAISE. `ContactSheetPolicy` refuses a
   `tile_px` above `MAX_TILE_PX` at construction, and `plan_contact_sheet`
   re-checks the effective value against `_TILE_PX_HARD_CEILING`, a *separate*
   private constant. Two constants rather than one because the public name is
   patchable (`contact_sheet.MAX_TILE_PX = 4096` from a test or a plugin) and a
   ceiling you can raise by assignment is not a ceiling. The alternative --
   silently clamping an over-large request down to the ceiling -- was rejected:
   hard rule 7 is "no silent anything", and a caller who asked for 1024px has a
   belief about what is being uploaded that must be corrected, not quietly
   satisfied with something else.

2. OPAQUE PER-REQUEST HANDLES. The frontier model sees `s01`..`sNN`, which are
   grid slots and nothing else. `media_id`, `moment_id`, `proxy_id` and every
   filesystem path stay on this side of the boundary; `SheetCandidate` has no
   path field at all, so a path cannot leak from here because it never enters.
   A leaked sheet is therefore a page of thumbnails, not an index into the
   library. `resolve()` maps a reply's handle back, and does it by exact
   dictionary lookup rather than by parsing, because a lenient parser
   ("s7" -> slot 7, " S07 " -> slot 7) is precisely how the model's reply about
   one photograph gets applied to a different one.

3. AN ITEM COUNT CAP, with the same two-constant treatment. Candidates dropped
   for exceeding it are recorded in `plan.excluded` with reason `over_item_cap`
   -- a cap that silently discards is data loss.

4. DENY-BY-DEFAULT SAFETY, WHERE ABSENCE IS A DENIAL. See below.

5. A LEAK DETECTOR ON THE WAY OUT. `payload()` walks the structure it is about
   to hand over and raises `LeakError` if any string contains any local
   identifier belonging to this plan. This does nothing today. It exists for the
   edit six months from now that adds a helpful `"source": media_id` field to a
   payload item, which would otherwise be caught only by a reviewer noticing.

ABSENCE IS INDETERMINATE, AND INDETERMINATE BLOCKS (issue #21)

The obvious way to write the safety gate is `if safety.nsfw_score > threshold:
exclude`. On a library mid-scan that is a catastrophe, because the overwhelming
majority of records do not have a `safety` block yet -- the classifier stage has
not run -- and `None > threshold` either raises or, worse, an incautious
`(safety or {}).get("nsfw_score", 0.0)` evaluates to a confident zero. Every one
of those unclassified files then sails onto the sheet and off the device.

So the gate is inverted: a candidate must PROVE it is safe. Concretely it must
carry a `SafetyAssessment` *and* a `safety` processing stage whose status is
`done`. A missing assessment, a stage that is `pending`/`running`/`failed`/
`skipped`, an assessment present without a completed stage (a stale verdict left
behind by a re-run that then failed) -- all of these are `False`, not `unknown`.
What happens when the safety classifier has not run: nothing is sent.

The same principle governs the sheet as a whole. If nothing survives the gate,
`plan_contact_sheet` does not raise and does not send an empty sheet; it returns
a plan with `sendable == False`, an `EgressDeclaration` of `requires_egress:
false`, and one `Exclusion` per candidate explaining itself. No consent is
consumed, because nothing left. Consent is validated only when there is in fact
something to send -- which is why a bad `ConsentRef` raises for a set of
publishable candidates and does not raise for a set that was all going to be
blocked anyway.

WHY A USER OVERRIDE DOES NOT LIFT THE GATE

`ExclusionState.user_override == true` means "the user forced this photo back
into their album". It does not mean "the user agreed to upload this photo to a
third party". Those are different consents and this module refuses to treat one
as the other. Upload consent lives in exactly one place: the `ConsentRef` with
scope `tier3_contact_sheet`.

DETERMINISM

Same candidates + same policy + same consent = byte-identical payload, identical
handles, identical digest. That is a product requirement (CLAUDE.md hard rule 3)
and here it is also a privacy requirement: the ledger entry written from a plan
has to describe the bytes that actually went out. So every sort key is total,
every tie is broken explicitly on `candidate_id`, no iteration depends on dict
or set ordering, and `now` is a required parameter rather than a call to
`datetime.now()` -- a wall-clock read inside a planner makes the plan
unreproducible and the ledger unverifiable.

Chronological order for the grid, not score order: the frontier model is being
asked for a story arc, and a sheet in shooting order is the only sheet on which
"this happened before that" is a readable fact. Items with no usable capture
time are NOT interleaved at a guessed position -- `TimeAssertion.precision`
documents that consumers must not sort an unknown time to the epoch -- so they
form a trailing block and are marked `undated: true` in the payload, so the
model cannot infer a time from a position that means nothing.

Python 3.12, stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ALLOWED_PROXY_KINDS",
    "CONSENT_SCOPE",
    "DEFAULT_POLICY",
    "EGRESS_DESTINATION",
    "EGRESS_PAYLOAD_KIND",
    "EXCLUSION_REASONS",
    "MAX_COLUMNS",
    "MAX_ITEMS",
    "MAX_NSFW_THRESHOLD",
    "MAX_TILE_PX",
    "MIN_TILE_PX",
    "PAYLOAD_SCHEMA",
    "ConsentError",
    "ContactSheetError",
    "ContactSheetPlan",
    "ContactSheetPolicy",
    "Exclusion",
    "LeakError",
    "SheetCandidate",
    "SheetItem",
    "candidate_from_media_record",
    "plan_contact_sheet",
]


# --------------------------------------------------------------------------
# Contract-facing constants. These strings are enum members in
# contracts/schemas/*.json; they are spelled out here rather than imported so
# this package has no import-time dependency on generated bindings, and the
# tests validate the emitted declaration against the real schema.
# --------------------------------------------------------------------------

PAYLOAD_SCHEMA = "contact_sheet_payload/v0"
CONSENT_SCOPE = "tier3_contact_sheet"
EGRESS_DESTINATION = "tier3_inference"
EGRESS_PAYLOAD_KIND = "contact_sheet"

# Which ProxyRef.kind values may become a tile. `preview_2048` and
# `video_proxy_480p` are deliberately absent: the renderer should be handed the
# smallest artifact that can satisfy the tile, so that the failure mode of a
# renderer bug which forwards its input unscaled is a 512px thumbnail leaving
# the device rather than a 2048px preview. `video_proxy_480p` is a clip, not a
# frame; a moment's tile is its `key_frame`, which is a contact_sheet_tile.
ALLOWED_PROXY_KINDS = frozenset({"contact_sheet_tile", "thumbnail_512"})

# The safety processing stage must have reached exactly this status. Note what
# is NOT here: `skipped` and `not_applicable`. A pipeline that decided safety
# did not apply to a file has not established that the file is safe, and this
# module is not the place to second-guess why it was skipped.
_SAFETY_STAGE_DONE = "done"

EXCLUSION_REASONS = (
    "no_proxy",
    "proxy_kind_not_permitted",
    "safety_not_assessed",
    "safety_stage_not_done",
    "safety_auto_excluded",
    "safety_category_flagged",
    "nsfw_above_threshold",
    "sensitive_flagged",
    "user_hidden",
    "excluded_from_automation",
    "over_item_cap",
)

# --------------------------------------------------------------------------
# Ceilings.
#
# Each appears twice on purpose. The public constant is documentation and is
# what `ContactSheetPolicy` validates against; the private one is what
# `plan_contact_sheet` re-checks immediately before emitting a plan. A caller
# who rebinds the public name, or who reaches past the frozen dataclass with
# `object.__setattr__`, still hits the private wall. Keeping one constant and
# trusting the policy would make the ceiling a convention rather than a
# structure.
# --------------------------------------------------------------------------

MAX_TILE_PX = 256
MIN_TILE_PX = 64
MAX_ITEMS = 64
MAX_COLUMNS = 8
# A *threshold* ceiling, not a score ceiling: raising the NSFW threshold is how
# you let more through, so the threshold itself is capped. 0.15 is deliberately
# far below 0.5 -- this gate governs egress, not album inclusion, and the cost
# of a false negative here is unbounded.
MAX_NSFW_THRESHOLD = 0.15

_TILE_PX_HARD_CEILING = 256
_ITEM_HARD_CEILING = 64
_COLUMN_HARD_CEILING = 8
_NSFW_HARD_CEILING = 0.15

# Payload size estimate for the ledger and for egress budgeting. A tile is a
# quality-70 JPEG; ~1.2 bits per pixel is a conservative (over-)estimate at that
# quality for photographic content. Over-estimating is the safe direction: a
# byte budget that under-counts lets more leave than was authorised.
_ESTIMATED_BITS_PER_PIXEL = 1.2

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Sorting sentinel for undated items. It is never used as a *value*, only to
# keep the sort key type-homogeneous; the `sort_time is None` component of the
# key has already segregated the undated block, so this instant is unreachable
# as a discriminator. Using it as a real fallback date would be the exact bug
# TimeAssertion.precision warns about.
_SORT_SENTINEL = datetime(1, 1, 1, tzinfo=timezone.utc)


class ContactSheetError(Exception):
    """A contact sheet could not be composed, or a reply could not be mapped back."""


class ConsentError(ContactSheetError):
    """There is imagery to send and no valid consent to send it under."""


class LeakError(ContactSheetError):
    """A local identifier was found in a payload about to leave the device."""


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContactSheetPolicy:
    """Composition limits. Every field is bounded above by a module ceiling.

    `columns` is a maximum: a sheet of five items is laid out five-across rather
    than six-across-with-a-hole, because a grid that is mostly empty wastes the
    model's attention and makes the row/col coordinates the reply refers to
    harder to read off.
    """

    tile_px: int = 256
    columns: int = 6
    max_items: int = 40
    nsfw_threshold: float = 0.10
    include_transcript: bool = False
    max_transcript_chars: int = 240

    def __post_init__(self) -> None:
        if not isinstance(self.tile_px, int) or isinstance(self.tile_px, bool):
            raise ContactSheetError("tile_px must be an int")
        if self.tile_px > MAX_TILE_PX:
            raise ContactSheetError(
                f"tile_px {self.tile_px} exceeds the ceiling of {MAX_TILE_PX}; "
                "the ceiling is not raisable by a caller"
            )
        if self.tile_px < MIN_TILE_PX:
            # A floor as well as a ceiling: below this the model cannot read the
            # tile, so the upload achieves nothing and is pure privacy cost.
            raise ContactSheetError(f"tile_px {self.tile_px} is below the floor of {MIN_TILE_PX}")

        if not isinstance(self.columns, int) or isinstance(self.columns, bool):
            raise ContactSheetError("columns must be an int")
        if self.columns < 1 or self.columns > MAX_COLUMNS:
            raise ContactSheetError(f"columns {self.columns} outside 1..{MAX_COLUMNS}")

        if not isinstance(self.max_items, int) or isinstance(self.max_items, bool):
            raise ContactSheetError("max_items must be an int")
        if self.max_items < 1 or self.max_items > MAX_ITEMS:
            raise ContactSheetError(f"max_items {self.max_items} outside 1..{MAX_ITEMS}")

        if not isinstance(self.nsfw_threshold, (int, float)) or isinstance(
            self.nsfw_threshold, bool
        ):
            raise ContactSheetError("nsfw_threshold must be a number")
        if math.isnan(self.nsfw_threshold):
            # NaN compares false against everything, so a NaN threshold would
            # disable the NSFW gate entirely while looking like a number.
            raise ContactSheetError("nsfw_threshold must not be NaN")
        if self.nsfw_threshold < 0.0 or self.nsfw_threshold > MAX_NSFW_THRESHOLD:
            raise ContactSheetError(
                f"nsfw_threshold {self.nsfw_threshold} outside 0..{MAX_NSFW_THRESHOLD}"
            )

        if not isinstance(self.max_transcript_chars, int) or isinstance(
            self.max_transcript_chars, bool
        ):
            raise ContactSheetError("max_transcript_chars must be an int")
        if self.max_transcript_chars < 0:
            raise ContactSheetError("max_transcript_chars must not be negative")

        if not isinstance(self.include_transcript, bool):
            raise ContactSheetError("include_transcript must be a bool")


DEFAULT_POLICY = ContactSheetPolicy()


# --------------------------------------------------------------------------
# Candidate
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SheetCandidate:
    """One thing that might go on the sheet, with everything the gate needs.

    There is no `path` field, and there will not be one. The tile is addressed
    by `proxy_id`; the worker resolves that to a file. A path never enters this
    module, so no future edit can leak one out of it.

    `safety` and `exclusion` are contract-shaped mappings (`SafetyAssessment`
    and `ExclusionState` from media-record.schema.json) rather than parsed
    objects, so that a schema field this module does not yet understand travels
    through unchanged instead of being silently dropped.
    """

    candidate_id: str
    media_id: str
    score: float
    moment_id: str | None = None
    proxy_id: str | None = None
    proxy_kind: str | None = None
    sort_time: datetime | None = None
    duration_s: float | None = None
    transcript: str | None = None
    safety: Mapping[str, Any] | None = None
    safety_stage_status: str | None = None
    exclusion: Mapping[str, Any] | None = None
    user_hidden: bool = False
    sensitive_flags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for name in ("candidate_id", "media_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ContactSheetError(f"{name} must be a non-empty string")
        for name in ("moment_id", "proxy_id", "proxy_kind"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ContactSheetError(f"{name} must be a non-empty string or None")

        if not isinstance(self.score, (int, float)) or isinstance(self.score, bool):
            raise ContactSheetError("score must be a number")
        if not math.isfinite(self.score):
            # A NaN score sorts unpredictably (it compares false against
            # everything), so a single NaN candidate makes the whole selection
            # order depend on input order. That is a determinism break that
            # raises nothing, which is the failure mode this codebase keeps
            # finding.
            raise ContactSheetError(f"score must be finite, got {self.score!r}")

        if self.sort_time is not None:
            if not isinstance(self.sort_time, datetime):
                raise ContactSheetError("sort_time must be a datetime or None")
            if self.sort_time.tzinfo is None or self.sort_time.utcoffset() is None:
                # Naive and aware datetimes cannot be compared; mixing them
                # raises inside sorted() with a message that points at the sort,
                # not at the record that caused it.
                raise ContactSheetError("sort_time must be timezone-aware")

        if self.duration_s is not None:
            if not isinstance(self.duration_s, (int, float)) or isinstance(self.duration_s, bool):
                raise ContactSheetError("duration_s must be a number or None")
            if not math.isfinite(self.duration_s) or self.duration_s < 0:
                raise ContactSheetError("duration_s must be finite and non-negative")

        if self.transcript is not None and not isinstance(self.transcript, str):
            raise ContactSheetError("transcript must be a string or None")

        if not isinstance(self.user_hidden, bool):
            raise ContactSheetError("user_hidden must be a bool")

        if not isinstance(self.sensitive_flags, (frozenset, set)):
            raise ContactSheetError("sensitive_flags must be a set")
        object.__setattr__(self, "sensitive_flags", frozenset(self.sensitive_flags))


def candidate_from_media_record(
    record: Mapping[str, Any],
    *,
    score: float,
    candidate_id: str | None = None,
    moment_id: str | None = None,
    transcript: str | None = None,
    duration_s: float | None = None,
    sensitive_flags: Iterable[str] = (),
) -> SheetCandidate:
    """Adapt a contract MediaRecord into a candidate.

    Reads only what the gate and the layout need. Everything it cannot find is
    left as `None`, which the gate reads as a denial -- so a truncated or
    partially analysed record produces a candidate that will be excluded, never
    one that is accidentally waved through.
    """
    if not isinstance(record, Mapping):
        raise ContactSheetError("record must be a mapping")

    media_id = record.get("media_id")
    if not isinstance(media_id, str) or not media_id:
        raise ContactSheetError("record has no media_id")

    proxy_id, proxy_kind = _pick_tile_proxy(record.get("proxies"))

    content = record.get("content")
    safety = content.get("safety") if isinstance(content, Mapping) else None
    if not isinstance(safety, Mapping):
        safety = None

    stage_status = None
    processing = record.get("processing")
    if isinstance(processing, Mapping):
        stages = processing.get("stages")
        if isinstance(stages, Mapping):
            stage = stages.get("safety")
            if isinstance(stage, Mapping):
                raw = stage.get("status")
                stage_status = raw if isinstance(raw, str) else None

    exclusion = record.get("exclusion")
    if not isinstance(exclusion, Mapping):
        exclusion = None

    user = record.get("user")
    hidden = bool(user.get("hidden")) if isinstance(user, Mapping) else False

    return SheetCandidate(
        candidate_id=candidate_id or moment_id or media_id,
        media_id=media_id,
        score=score,
        moment_id=moment_id,
        proxy_id=proxy_id,
        proxy_kind=proxy_kind,
        sort_time=_capture_instant(record.get("capture")),
        duration_s=duration_s,
        transcript=transcript,
        safety=safety,
        safety_stage_status=stage_status,
        exclusion=exclusion,
        user_hidden=hidden,
        sensitive_flags=frozenset(sensitive_flags),
    )


def _pick_tile_proxy(proxies: Any) -> tuple[str | None, str | None]:
    """Choose the tile source, preferring the purpose-built one.

    Iterates ALLOWED_PROXY_KINDS in the fixed order of the module tuple rather
    than in whatever order the record lists its proxies, so two records with the
    same proxies in different orders produce the same plan.
    """
    if not isinstance(proxies, Sequence) or isinstance(proxies, (str, bytes)):
        return (None, None)
    by_kind: dict[str, str] = {}
    for proxy in proxies:
        if not isinstance(proxy, Mapping):
            continue
        kind = proxy.get("kind")
        pid = proxy.get("proxy_id")
        if isinstance(kind, str) and isinstance(pid, str) and pid and kind not in by_kind:
            by_kind[kind] = pid
    for kind in ("contact_sheet_tile", "thumbnail_512"):
        if kind in by_kind:
            return (by_kind[kind], kind)
    return (None, None)


def _capture_instant(capture: Any) -> datetime | None:
    """Resolved capture instant, or None when the record does not have one.

    Returns None whenever precision is `unknown`, even if a `utc` value is
    somehow present: the schema says an unknown-precision assertion has no
    usable time and must not be sorted into a chronology. Returns None when
    `utc` is absent, because a local wall-clock reading with no zone is not an
    instant and guessing the machine's zone shifts an entire holiday by hours.
    """
    if not isinstance(capture, Mapping):
        return None
    assertion = capture.get("captured_at")
    if not isinstance(assertion, Mapping):
        return None
    if assertion.get("precision") == "unknown":
        return None
    raw = assertion.get("utc")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SheetItem:
    """A candidate that survived, and the slot it occupies.

    Holds both sides of the boundary: `handle`/`row`/`col` are sent, the ids are
    not. `payload_entry()` is the only method that produces something for the
    wire, and it is deliberately the only place in the class that decides what
    is safe to include.
    """

    handle: str
    index: int
    row: int
    col: int
    candidate_id: str
    media_id: str
    moment_id: str | None
    proxy_id: str
    proxy_kind: str
    tile_px: int
    sort_time: datetime | None
    duration_s: float | None
    transcript: str | None

    def payload_entry(self, *, include_transcript: bool, max_transcript_chars: int) -> dict:
        entry: dict[str, Any] = {"handle": self.handle, "row": self.row, "col": self.col}
        if self.duration_s is not None:
            entry["duration_s"] = round(float(self.duration_s), 3)
        if self.sort_time is None:
            # Stated rather than inferred. Without this the model reads the
            # trailing block as "these happened last", which is a claim nobody
            # made.
            entry["undated"] = True
        if include_transcript and self.transcript:
            entry["transcript"] = self.transcript[:max_transcript_chars]
        return entry


@dataclass(frozen=True, slots=True)
class Exclusion:
    """A candidate that did not make the sheet, and every reason it did not."""

    candidate_id: str
    media_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContactSheetPlan:
    """Exactly what would leave the device, and exactly what would not."""

    sheet_token: str
    policy: ContactSheetPolicy
    scope: str
    items: tuple[SheetItem, ...]
    excluded: tuple[Exclusion, ...]
    columns: int
    rows: int
    consent: Mapping[str, Any] | None
    planned_at: datetime

    @property
    def sendable(self) -> bool:
        """False means nothing goes out. An unsendable plan is a normal result."""
        return bool(self.items) and self.consent is not None

    @property
    def handles(self) -> tuple[str, ...]:
        return tuple(item.handle for item in self.items)

    @property
    def sheet_px(self) -> tuple[int, int]:
        return (self.columns * self.policy.tile_px, self.rows * self.policy.tile_px)

    @property
    def estimated_bytes(self) -> int:
        if not self.sendable:
            return 0
        per_tile = math.ceil(self.policy.tile_px**2 * _ESTIMATED_BITS_PER_PIXEL / 8)
        return per_tile * len(self.items)

    # -- mapping the reply back ------------------------------------------

    def resolve(self, handle: str) -> SheetItem:
        """Map a handle from the model's reply back to a local item.

        Exact match only. No stripping, no case folding, no zero-pad repair: the
        model returning `S7` is the model returning something this plan did not
        issue, and the correct response is to refuse, not to guess which
        photograph it meant.
        """
        if not isinstance(handle, str):
            raise ContactSheetError("handle must be a string")
        for item in self.items:
            if item.handle == handle:
                return item
        raise ContactSheetError(f"handle {handle!r} was not issued by this plan")

    def resolve_many(self, handles: Iterable[str]) -> tuple[SheetItem, ...]:
        """Resolve every handle or none of them.

        All-or-nothing because a partially applied reply is worse than a
        rejected one: half a story arc built against real moments and half
        against dropped handles is a plausible-looking plan that nobody
        authored.
        """
        return tuple(self.resolve(h) for h in handles)

    # -- the wire ---------------------------------------------------------

    def payload(self) -> dict:
        """The structure that leaves the device. Raises unless `sendable`."""
        if not self.items:
            raise ContactSheetError(
                "refusing to compose a payload: no candidate passed the safety gate"
            )
        if self.consent is None:
            raise ConsentError("refusing to compose a payload without a valid consent record")

        body = {
            "schema": PAYLOAD_SCHEMA,
            "sheet_token": self.sheet_token,
            "grid": {
                "columns": self.columns,
                "rows": self.rows,
                "tile_px": self.policy.tile_px,
                "item_count": len(self.items),
            },
            "items": [
                item.payload_entry(
                    include_transcript=self.policy.include_transcript,
                    max_transcript_chars=self.policy.max_transcript_chars,
                )
                for item in self.items
            ],
        }
        _assert_no_local_identifiers(body, self._local_identifiers())
        return body

    def payload_digest(self) -> str:
        """Digest of the exact bytes that would be sent.

        Canonical JSON (sorted keys, no whitespace, ASCII-escaped) so the digest
        is a property of the content and not of this interpreter's dict ordering
        or the caller's locale. blake2b rather than BLAKE3 because BLAKE3 is not
        in the standard library and this is a local audit handle, not a
        content-addressed contract id -- calling it a BLAKE3 hash where the
        contract means something specific by that would be worse than using a
        different function openly.
        """
        canonical = json.dumps(
            self.payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.blake2b(canonical.encode("utf-8"), digest_size=32).hexdigest()

    def egress_declaration(self) -> dict:
        """A JobSpec `EgressDeclaration`, ready to drop into a tier3 JobSpec."""
        if not self.sendable:
            # An explicit negative declaration, not an absent one. The schema
            # comment is emphatic that these must not look the same.
            return {
                "requires_egress": False,
                "consent": None,
                "destination": None,
                "payload_kind": None,
                "estimated_bytes": 0,
            }
        return {
            "requires_egress": True,
            "consent": dict(self.consent or {}),
            "destination": EGRESS_DESTINATION,
            "payload_kind": EGRESS_PAYLOAD_KIND,
            "estimated_bytes": self.estimated_bytes,
        }

    def ledger_entry(self) -> dict:
        """Everything services/api needs to write the consent-ledger row.

        Includes the media_ids. That is the point: the ledger is local and its
        job is to answer "which of my photographs have ever left this machine",
        which it cannot do from handles alone. The ledger entry and the payload
        are different artifacts with opposite requirements -- the payload must
        not name the library, the ledger must.
        """
        sent = [
            {
                "handle": item.handle,
                "media_id": item.media_id,
                "moment_id": item.moment_id,
                "proxy_id": item.proxy_id,
                "proxy_kind": item.proxy_kind,
            }
            for item in self.items
        ]
        entry: dict[str, Any] = {
            "scope": self.scope,
            "sheet_token": self.sheet_token,
            "planned_at": self.planned_at.isoformat(),
            "egress": self.egress_declaration(),
            "sheet": {
                "columns": self.columns,
                "rows": self.rows,
                "tile_px": self.policy.tile_px,
                "item_count": len(self.items),
                "include_transcript": self.policy.include_transcript,
            },
            "sent_media": sent,
            "withheld": [
                {
                    "candidate_id": exc.candidate_id,
                    "media_id": exc.media_id,
                    "reasons": list(exc.reasons),
                }
                for exc in self.excluded
            ],
        }
        # A digest only exists when something is actually sent; computing one
        # for an unsendable plan would put a hash of nothing in the ledger and
        # make "nothing was sent" indistinguishable from "something was".
        entry["payload_digest"] = self.payload_digest() if self.sendable else None
        return entry

    def _local_identifiers(self) -> frozenset[str]:
        ids: set[str] = set()
        for item in self.items:
            ids.add(item.candidate_id)
            ids.add(item.media_id)
            ids.add(item.proxy_id)
            if item.moment_id:
                ids.add(item.moment_id)
        return frozenset(ids)


def _assert_no_local_identifiers(node: Any, forbidden: frozenset[str]) -> None:
    """Walk a payload and refuse it if any local id appears anywhere in it.

    Substring rather than equality, because the realistic regression is not
    `{"media_id": ...}` -- a reviewer catches that -- but a caption, a debug
    string or a filename-derived label that happens to embed the id.
    """
    if not forbidden:
        return
    if isinstance(node, str):
        for identifier in sorted(forbidden):
            if identifier and identifier in node:
                raise LeakError(f"payload string contains local identifier {identifier!r}")
        return
    if isinstance(node, Mapping):
        for key in sorted(node.keys(), key=str):
            _assert_no_local_identifiers(key, forbidden)
            _assert_no_local_identifiers(node[key], forbidden)
        return
    if isinstance(node, (list, tuple)):
        for element in node:
            _assert_no_local_identifiers(element, forbidden)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def _gate(candidate: SheetCandidate, policy: ContactSheetPolicy) -> tuple[str, ...]:
    """Every reason this candidate may not leave the device.

    Returns all reasons rather than the first, in a fixed order, because the
    ledger's `withheld` list is the only record anyone will ever read of why a
    photograph was held back, and "nsfw_above_threshold" alone hides the fact
    that the user had also hidden it.
    """
    reasons: list[str] = []

    if candidate.proxy_id is None or candidate.proxy_kind is None:
        # No proxy means no low-res rendition exists. There is no fallback to
        # the original file; that fallback is the rule this module exists to
        # make impossible.
        reasons.append("no_proxy")
    elif candidate.proxy_kind not in ALLOWED_PROXY_KINDS:
        reasons.append("proxy_kind_not_permitted")

    safety = candidate.safety
    if not isinstance(safety, Mapping):
        reasons.append("safety_not_assessed")
    if candidate.safety_stage_status != _SAFETY_STAGE_DONE:
        reasons.append("safety_stage_not_done")

    if isinstance(safety, Mapping):
        if safety.get("auto_excluded") is not False:
            # `is not False` rather than truthiness: the field is required by
            # the schema, so a missing or non-boolean value is a malformed
            # record, and a malformed safety verdict is an absent one.
            reasons.append("safety_auto_excluded")

        categories = safety.get("categories")
        if categories is None:
            categories = ()
        if not isinstance(categories, (list, tuple)):
            # Malformed where the schema says array. Unreadable is unknown.
            reasons.append("safety_category_flagged")
        elif categories:
            # Any non-empty list blocks. The classifier lists a category only
            # when that category fired, and there is no benign member of the
            # schema enum -- `unknown` is in it precisely to carry "something
            # tripped that does not map to a named class". A value outside the
            # enum is unrecognised, which is indeterminate, which also blocks.
            # If a benign category is ever added to the contract, this is the
            # line that needs an allow-list; until then an allow-list would be
            # an empty set pretending to be a policy.
            reasons.append("safety_category_flagged")

        score = safety.get("nsfw_score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            reasons.append("nsfw_above_threshold")
        elif math.isnan(score):
            # NaN fails every comparison, so `score > threshold` would let it
            # through. An unusable score is an unknown score, and unknown blocks.
            reasons.append("nsfw_above_threshold")
        elif score > policy.nsfw_threshold:
            reasons.append("nsfw_above_threshold")

    if candidate.sensitive_flags:
        reasons.append("sensitive_flagged")

    if candidate.user_hidden:
        reasons.append("user_hidden")

    exclusion = candidate.exclusion
    if isinstance(exclusion, Mapping) and exclusion.get("excluded_from_automation") is True:
        # `user_override` is not consulted. Forcing a photo back into an album
        # is not agreeing to upload it to a third party; conflating the two is
        # the single most tempting mistake available in this file.
        reasons.append("excluded_from_automation")

    return tuple(reasons)


# --------------------------------------------------------------------------
# Consent
# --------------------------------------------------------------------------

_CONSENT_REQUIRED = ("ledger_entry_id", "scope", "granted_at")
_CONSENT_ALLOWED = frozenset(
    {"ledger_entry_id", "scope", "granted_at", "expires_at", "revoked_at"}
)


def _validate_consent(consent: Any, now: datetime) -> dict:
    """Check a ConsentRef actually authorises this upload, right now.

    Raises rather than returning a flag. There is no partial upload and no
    degraded mode: either the user agreed or nothing leaves.
    """
    if not isinstance(consent, Mapping):
        raise ConsentError("consent must be a ConsentRef mapping")

    unknown = sorted(set(consent) - _CONSENT_ALLOWED)
    if unknown:
        # additionalProperties:false in the contract. A field this code does not
        # understand may be the very field that revokes the consent.
        raise ConsentError(f"consent has fields not in ConsentRef: {unknown}")

    for key in _CONSENT_REQUIRED:
        if consent.get(key) in (None, ""):
            raise ConsentError(f"consent is missing required field {key!r}")

    ledger_id = consent["ledger_entry_id"]
    if not isinstance(ledger_id, str) or not _UUID_RE.match(ledger_id):
        raise ConsentError("consent.ledger_entry_id is not a lowercase RFC 4122 UUID")

    if consent["scope"] != CONSENT_SCOPE:
        # A cloud_render or print_order consent is a real, granted consent -- for
        # something else. Scope is the whole point of scoping.
        raise ConsentError(
            f"consent scope {consent['scope']!r} does not authorise a contact sheet; "
            f"scope must be {CONSENT_SCOPE!r}"
        )

    if consent.get("revoked_at") is not None:
        raise ConsentError("consent has been revoked")

    granted_at = _parse_instant(consent["granted_at"], "consent.granted_at")
    if granted_at > now:
        # A consent granted in the future is a clock or data bug, and treating
        # it as valid would let a bad clock authorise an upload.
        raise ConsentError("consent.granted_at is in the future")

    expires_at = consent.get("expires_at")
    if expires_at is not None:
        expiry = _parse_instant(expires_at, "consent.expires_at")
        if expiry <= now:
            raise ConsentError("consent has expired")

    return dict(consent)


def _parse_instant(raw: Any, label: str) -> datetime:
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ConsentError(f"{label} is not an ISO 8601 timestamp: {raw!r}") from exc
    else:
        raise ConsentError(f"{label} must be a timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConsentError(f"{label} must carry a UTC offset")
    return parsed


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def plan_contact_sheet(
    candidates: Iterable[SheetCandidate],
    *,
    consent: Mapping[str, Any] | None,
    now: datetime,
    scope: str,
    policy: ContactSheetPolicy = DEFAULT_POLICY,
) -> ContactSheetPlan:
    """Compose the plan for one contact sheet.

    `now` is a parameter, not a `datetime.now()` call: the plan has to be
    reproducible from its inputs for the ledger entry to be verifiable, and a
    wall-clock read inside a planner makes it not.

    Consent is checked only if something survives the gate. That ordering is
    deliberate -- a sheet with nothing on it is not an upload, so it needs no
    authorisation -- and it is why passing an expired consent with a set of
    fully-blocked candidates returns quietly instead of raising.
    """
    if not isinstance(policy, ContactSheetPolicy):
        raise ContactSheetError("policy must be a ContactSheetPolicy")
    if not isinstance(scope, str) or not scope:
        raise ContactSheetError("scope must be a non-empty string")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ContactSheetError("now must be a timezone-aware datetime")

    # Second line of defence, against the private ceilings. A caller who rebound
    # the public constants or reached past the frozen policy with
    # object.__setattr__ gets stopped here.
    if policy.tile_px > _TILE_PX_HARD_CEILING:
        raise ContactSheetError(
            f"effective tile_px {policy.tile_px} exceeds the hard ceiling "
            f"{_TILE_PX_HARD_CEILING}"
        )
    if policy.max_items > _ITEM_HARD_CEILING:
        raise ContactSheetError(
            f"effective max_items {policy.max_items} exceeds the hard ceiling {_ITEM_HARD_CEILING}"
        )
    if policy.columns > _COLUMN_HARD_CEILING:
        raise ContactSheetError(
            f"effective columns {policy.columns} exceeds the hard ceiling {_COLUMN_HARD_CEILING}"
        )
    if policy.nsfw_threshold > _NSFW_HARD_CEILING:
        raise ContactSheetError(
            f"effective nsfw_threshold {policy.nsfw_threshold} exceeds the hard ceiling "
            f"{_NSFW_HARD_CEILING}"
        )

    materialised: list[SheetCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, SheetCandidate):
            raise ContactSheetError("candidates must be SheetCandidate instances")
        if candidate.candidate_id in seen:
            # Two slots showing the same thing, and two handles resolving to it.
            # Always a caller bug; silently de-duplicating would hide it.
            raise ContactSheetError(f"duplicate candidate_id {candidate.candidate_id!r}")
        seen.add(candidate.candidate_id)
        materialised.append(candidate)

    survivors: list[SheetCandidate] = []
    excluded: list[Exclusion] = []
    for candidate in materialised:
        reasons = _gate(candidate, policy)
        if reasons:
            excluded.append(Exclusion(candidate.candidate_id, candidate.media_id, reasons))
        else:
            survivors.append(candidate)

    # Cap by score. Ties break on candidate_id ascending so the cut is a
    # function of the inputs and not of the order they were iterated in.
    survivors.sort(key=lambda c: (-float(c.score), c.candidate_id))
    if len(survivors) > policy.max_items:
        for dropped in survivors[policy.max_items :]:
            excluded.append(Exclusion(dropped.candidate_id, dropped.media_id, ("over_item_cap",)))
        survivors = survivors[: policy.max_items]

    # Lay the sheet out chronologically; undated items form a trailing block
    # rather than being interleaved at an invented position.
    survivors.sort(
        key=lambda c: (c.sort_time is None, c.sort_time or _SORT_SENTINEL, c.candidate_id)
    )

    columns = min(policy.columns, len(survivors)) if survivors else 0
    rows = math.ceil(len(survivors) / columns) if columns else 0

    sheet_token = _sheet_token(survivors, policy, scope)

    items: list[SheetItem] = []
    for index, candidate in enumerate(survivors):
        if candidate.proxy_id is None or candidate.proxy_kind is None:
            # The gate guarantees this; checked anyway with a raise rather than
            # an assert, because asserts vanish under -O and this one is the
            # difference between a tile and a None handed to a renderer.
            raise ContactSheetError(
                f"candidate {candidate.candidate_id!r} reached layout without a proxy"
            )
        items.append(
            SheetItem(
                handle=f"s{index + 1:02d}",
                index=index,
                row=index // columns,
                col=index % columns,
                candidate_id=candidate.candidate_id,
                media_id=candidate.media_id,
                moment_id=candidate.moment_id,
                proxy_id=candidate.proxy_id,
                proxy_kind=candidate.proxy_kind,
                tile_px=policy.tile_px,
                sort_time=candidate.sort_time,
                duration_s=candidate.duration_s,
                transcript=candidate.transcript,
            )
        )

    validated_consent = _validate_consent(consent, now) if items else None

    excluded.sort(key=lambda e: (e.candidate_id, e.reasons))

    return ContactSheetPlan(
        sheet_token=sheet_token,
        policy=policy,
        scope=scope,
        items=tuple(items),
        excluded=tuple(excluded),
        columns=columns,
        rows=rows,
        consent=validated_consent,
        planned_at=now,
    )


def _sheet_token(
    survivors: Sequence[SheetCandidate], policy: ContactSheetPolicy, scope: str
) -> str:
    """A short opaque token identifying this sheet, echoed by the reply.

    Derived from the request material so the plan is idempotent: replanning the
    same sheet produces the same token and the same job identity. A random nonce
    would be strictly more opaque and would destroy that idempotency, which the
    resumable-JobSpec design depends on.

    It is a preimage-resistant digest, so it does not disclose membership: an
    attacker holding a leaked sheet would need to already know the exact ordered
    candidate set to confirm anything, in which case they already have the
    library. That trade is worth stating explicitly rather than leaving implied.
    """
    material = json.dumps(
        {
            "scope": scope,
            "tile_px": policy.tile_px,
            "columns": policy.columns,
            "max_items": policy.max_items,
            "include_transcript": policy.include_transcript,
            "candidates": [c.candidate_id for c in survivors],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.blake2b(material.encode("utf-8"), digest_size=8).hexdigest()
