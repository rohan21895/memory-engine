"""Building a `SafetyClearance` -- the thing a publication has to present.

WHAT A MANIFEST IS FOR

Not a per-record `is_safe` flag. A flag is checked at one moment and acted on at
another, and the gap between them is where the failure lives: the selection
changes, a photograph is swapped in, and the check that passed was about a
different set. So clearance is bound to an EXACT publication -- this sink, these
media ids, IN THIS ORDER, under this classifier and this config digest -- and
hashed. The renderer verifies the hash against the inputs it is actually about
to publish, inside the same operation that creates the export.

THE DECISION IS COMPUTED, NEVER ASSERTED

`decision` is derived from `items` here and recomputed from `items` again by
every verifier. It is stored so a rejected publication can be explained without
re-running anything -- not so a reader can skip checking the items. This module
never accepts a decision from a caller, which is why `build_manifest` has no
parameter for one.

`cleared_for_publication` is true only when every item is `cleared`, or is
`blocked` with a valid override for THIS sink. One indeterminate item denies the
whole publication: a book is printed as a unit and a share is published as a
unit, so partial clearance is not a state either can be in.

OVERRIDES GO IN AT BUILD TIME AND ARE CHECKED AT VERIFY TIME

A human may override a POSITIVE result -- a parent decides a breastfeeding
photograph belongs in the family album, and the classifier does not get a veto
over that. A human may not override a MISSING result, and this module refuses to
attach an override to an `indeterminate` item rather than letting the schema
catch it later, because by the time the schema catches it the caller has already
convinced themselves it should work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from memory_engine_safety.canonical import manifest_id as compute_manifest_id
from memory_engine_safety.classes import CLASS_ORDER
from memory_engine_safety.classify import Classification, Thresholds

__all__ = [
    "MANIFEST_VERSION",
    "SCHEMA_VERSION",
    "SINKS",
    "build_manifest",
    "compute_decision",
]

SCHEMA_VERSION = "v0"
MANIFEST_VERSION = 1

#: `SafetyClearance.sink`. Clearance is NOT transferable between sinks: a
#: photograph cleared for a private printed book has not thereby been cleared
#: for a public share link.
SINKS = ("print", "share", "frontier_egress", "local_export")

_VERDICTS = ("cleared", "blocked", "indeterminate")


def _valid_override(item: Mapping[str, Any], sink: str) -> bool:
    """Whether this item carries an override that actually permits publication.

    Three conditions, all of them load-bearing:

    * the item is `blocked` -- an override on `indeterminate` is the single rule
      the contract calls the most important in the file;
    * `scope` is `item_and_sink`, the only value there is;
    * and it was recorded FOR THIS SINK. The scope name says the decision is
      bound to a sink, but nothing in the override object records which one --
      the manifest's own `sink` is what supplies it, which is exactly why an
      override cannot be carried across manifests.
    """
    override = item.get("override")
    if not isinstance(override, Mapping):
        return False
    if item.get("verdict") != "blocked":
        return False
    if override.get("scope") != "item_and_sink":
        return False
    decided_by = override.get("decided_by")
    if not isinstance(decided_by, str) or not decided_by.strip():
        return False
    del sink  # bound by the manifest this override lives in; see docstring
    return True


def compute_decision(
    items: Sequence[Mapping[str, Any]], sink: str, *, detail: str | None = None
) -> dict[str, Any]:
    """`ClearanceDecision` derived from the items. The only way one is made.

    Unknown verdict strings count as indeterminate rather than raising, because
    this function is also used by the verifier on a document it did not write:
    an unrecognised verdict from a newer producer must DENY, and denying by
    counting it as indeterminate keeps the arithmetic honest at the same time.
    """
    cleared = blocked = indeterminate = 0
    unknown: list[str] = []
    for item in items:
        verdict = item.get("verdict")
        if verdict == "cleared":
            cleared += 1
        elif verdict == "blocked":
            blocked += 1
        elif verdict == "indeterminate":
            indeterminate += 1
        else:
            indeterminate += 1
            unknown.append(str(verdict))

    unoverridden_blocks = sum(
        1
        for item in items
        if item.get("verdict") == "blocked" and not _valid_override(item, sink)
    )
    cleared_for_publication = indeterminate == 0 and unoverridden_blocks == 0

    reasons: list[str] = []
    if unknown:
        reasons.append(
            f"{len(unknown)} item(s) carry a verdict this build does not recognise "
            f"({sorted(set(unknown))}); an unknown verdict is denied, not guessed"
        )
    if indeterminate:
        seen = sorted(
            {
                str(item.get("indeterminate_reason"))
                for item in items
                if item.get("verdict") == "indeterminate"
            }
        )
        reasons.append(
            f"{indeterminate} of {len(items)} item(s) have no verdict ({', '.join(seen)}); "
            "absence is indeterminate and indeterminate blocks"
        )
    if unoverridden_blocks:
        reasons.append(
            f"{unoverridden_blocks} item(s) are blocked with no recorded human "
            f"override for the {sink} sink"
        )
    if detail:
        reasons.append(detail)

    return {
        "cleared_for_publication": cleared_for_publication,
        "item_count": len(items),
        "cleared_count": cleared,
        "blocked_count": blocked,
        "indeterminate_count": indeterminate,
        "denied_reason": ". ".join(reasons)[:1024] if reasons else None,
    }


def build_manifest(
    classification: Classification,
    *,
    sink: str,
    created_at: str,
    ran_at: str,
    model: Mapping[str, Any],
    thresholds: Thresholds,
    load_mode: str,
    sink_detail: str | None = None,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """A complete, hashed `SafetyClearance` document.

    `overrides` is keyed by media id. Applied here rather than by the caller
    mutating items, so the refusal below is unavoidable.
    """
    if sink not in SINKS:
        raise ValueError(f"{sink!r} is not one of {SINKS}")
    if load_mode not in ("release", "development"):
        raise ValueError(
            f"{load_mode!r} is not a load mode; it is recorded rather than assumed "
            "because 'we were only testing' is how unverified weights reach "
            "production"
        )

    items: list[dict[str, Any]] = [dict(item) for item in classification.verdicts]
    for media_id, override in (overrides or {}).items():
        matches = [item for item in items if item["media_id"] == media_id]
        if not matches:
            raise ValueError(
                f"an override was recorded for {media_id[:12]}..., which is not in "
                "this publication. An override that names nothing is either a stale "
                "decision or a typo, and both are worth failing on."
            )
        for item in matches:
            if item["verdict"] != "blocked":
                raise ValueError(
                    f"{media_id[:12]}... is {item['verdict']!r} and cannot be "
                    "overridden. A missing result may not be overridden by a flag, "
                    "a default, a global bypass or an empty override list: 'nobody "
                    "checked' and 'somebody checked and disagreed' are different "
                    "states, and only the second is a decision."
                )
            item["override"] = dict(override)

    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_version": MANIFEST_VERSION,
        "created_at": created_at,
        "sink": sink,
        "sink_detail": sink_detail,
        "classifier": {
            "model": dict(model),
            "ran_at": ran_at,
            "class_order": list(CLASS_ORDER),
            "load_mode": load_mode,
        },
        "thresholds": thresholds.as_mapping(),
        "items": items,
        "decision": compute_decision(items, sink, detail=classification.detail),
    }
    document["manifest_id"] = compute_manifest_id(document)
    return document
