"""The verifier. One small function, deny-by-default, run at every boundary.

WHY THE SAME CODE RUNS AT ALL THREE BOUNDARIES

Print, share and frontier egress are three different irreversible acts with
three different owners, and the tempting shape is three checks. Three checks
become three slightly different checks, and the one that is slightly weaker is
the one an attacker or an accident finds. So there is one verifier, it takes the
sink as an argument, and each boundary calls it with its own sink pinned by a
wrapper it cannot pass a different value to (see gate.py).

DENY BY DEFAULT, AND WHAT THAT ACTUALLY MEANS

Every one of these denies:

  * no manifest at all, or one that will not parse;
  * a `manifest_version` or `schema_version` this build does not recognise;
  * a manifest for a different sink;
  * an item set that is not exactly the publication's, IN ORDER;
  * a duplicate item, or an item whose evidence id is not the proxy the
    publication is built from;
  * a verdict string this build does not know;
  * any `indeterminate` item, with no override permitted;
  * a `blocked` item with no valid human override for this sink;
  * a stored verdict that disagrees with its own scores and thresholds;
  * a classifier pin that is not the one the caller expected;
  * a `development` load mode against a real publication;
  * a `class_order` that is not the contract's;
  * a `manifest_id` that does not recompute;
  * and the verifier raising at all.

That last one is not decoration. A verifier that throws and is caught by a
`try/except: pass` two frames up is a gate that passes everything, so the
exception is converted into a denial INSIDE the verifier, and the only thing
that leaves this module is a denial or a clearance.

WHY IT RAISES INSTEAD OF RETURNING FALSE

A boolean is something a caller can forget to branch on, and what they do next
if they forget is publish. `PublicationBlocked` is an exception because
forgetting to handle it must look like a crash, not like a pass. The three
boundary wrappers in gate.py do not catch it.

WHY THE DECISION BLOCK IS RECOMPUTED

`ClearanceDecision` is stored so a refusal can be explained without re-running
inference. It is not evidence. A producer that writes `cleared_for_publication:
true` over an indeterminate item is exactly the failure this contract exists to
prevent, so the verifier derives the decision from `items` and compares; a
disagreement denies, and it denies as a disagreement rather than being silently
overwritten, because the two mean different things about who is broken.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from memory_engine_safety.canonical import manifest_id as compute_manifest_id
from memory_engine_safety.classes import CLASS_ORDER
from memory_engine_safety.classify import Thresholds
from memory_engine_safety.manifest import (
    MANIFEST_VERSION,
    SCHEMA_VERSION,
    SINKS,
    compute_decision,
)

__all__ = [
    "KNOWN_VERDICTS",
    "Clearance",
    "PublicationBlocked",
    "verify_clearance",
]

KNOWN_VERDICTS = ("cleared", "blocked", "indeterminate")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Fields of a `ModelRef` that must match when the caller pins one. `runtime`
#: and `precision` are excluded: they describe WHERE it ran, and CoreML and CPU
#: legitimately differ in the last fp32 digit without being different models.
_PINNED_MODEL_FIELDS = ("model_id", "version", "weights_blake3", "config_blake3")


class PublicationBlocked(Exception):
    """No bytes leave. `code` is machine-readable; `detail` is for a human."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Clearance:
    """What a boundary gets back when, and only when, it may proceed."""

    manifest_id: str
    sink: str
    media_ids: tuple[str, ...]
    item_count: int
    overridden_media_ids: tuple[str, ...]


def _deny(code: str, detail: str) -> "PublicationBlocked":
    return PublicationBlocked(code, detail)


def _unit(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or not (0.0 <= number <= 1.0):
        return None
    return number


def verify_clearance(
    manifest: Mapping[str, Any] | None,
    *,
    sink: str,
    media_ids: Sequence[str],
    evidence_ids: Mapping[str, str] | None = None,
    expected_model: Mapping[str, Any] | None = None,
    expected_thresholds: Thresholds | None = None,
    allow_development_load_mode: bool = False,
) -> Clearance:
    """Verify a clearance against the exact publication about to happen.

    `media_ids` must be the ids the caller is ACTUALLY about to publish, in the
    order it is about to publish them, read from the same structure it is about
    to render -- not from the manifest. Passing `manifest["items"]` here would
    make the check tautological, which is the one way to get this wrong that
    still looks like a check.

    `evidence_ids` maps media id -> the proxy digest the publication is built
    from. Supplying it is what turns "a verdict exists" into "a verdict about
    these bytes"; omitting it is permitted (some sinks do not carry proxies) and
    is recorded in the docstring rather than defaulted silently, because it is
    the weaker check.
    """
    if sink not in SINKS:
        raise ValueError(f"{sink!r} is not a sink; this is a programming error")
    try:
        return _verify(
            manifest,
            sink=sink,
            media_ids=media_ids,
            evidence_ids=evidence_ids,
            expected_model=expected_model,
            expected_thresholds=expected_thresholds,
            allow_development_load_mode=allow_development_load_mode,
        )
    except PublicationBlocked:
        raise
    except Exception as failure:  # noqa: BLE001 - see the module docstring
        raise _deny(
            "verifier_exception",
            f"the clearance verifier raised {type(failure).__name__}: {failure}. A "
            "verifier that throws is a verifier that decided nothing, and nothing "
            "is indeterminate.",
        ) from failure


def _verify(
    manifest: Mapping[str, Any] | None,
    *,
    sink: str,
    media_ids: Sequence[str],
    evidence_ids: Mapping[str, str] | None,
    expected_model: Mapping[str, Any] | None,
    expected_thresholds: Thresholds | None,
    allow_development_load_mode: bool,
) -> Clearance:
    if manifest is None:
        raise _deny(
            "clearance_missing",
            f"no safety clearance was presented for the {sink} sink. Absence is "
            "indeterminate and indeterminate blocks; there is no flag, default or "
            "bypass that turns a missing manifest into a pass.",
        )
    if not isinstance(manifest, Mapping):
        raise _deny(
            "clearance_unparseable",
            f"the clearance for {sink} is a {type(manifest).__name__}, not an object",
        )

    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise _deny(
            "unknown_manifest_version",
            f"manifest_version {manifest.get('manifest_version')!r} is not "
            f"{MANIFEST_VERSION}. An unrecognised version is denied rather than "
            "parsed best-effort: the field a stale reader ignores is the one that "
            "was added because something went wrong.",
        )
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise _deny(
            "unknown_schema_version",
            f"schema_version {manifest.get('schema_version')!r} is not "
            f"{SCHEMA_VERSION!r}",
        )
    if manifest.get("sink") != sink:
        raise _deny(
            "sink_mismatch",
            f"this clearance is for the {manifest.get('sink')!r} sink and the "
            f"publication is {sink!r}. Clearance is not transferable between sinks: "
            "a photograph cleared for a private printed book has not thereby been "
            "cleared for a public share link.",
        )

    # -- the classifier pin ------------------------------------------------
    classifier = manifest.get("classifier")
    if not isinstance(classifier, Mapping):
        raise _deny("classifier_missing", "the clearance names no classifier")
    declared_order = classifier.get("class_order")
    if list(declared_order or ()) != list(CLASS_ORDER):
        raise _deny(
            "class_order_mismatch",
            f"the clearance declares class_order {declared_order!r}, not "
            f"{list(CLASS_ORDER)}. Two of those names transposed turns every "
            "breastfeeding photograph into `explicit` with every score still in "
            "range and every threshold still firing.",
        )
    load_mode = classifier.get("load_mode")
    if load_mode not in ("release", "development"):
        raise _deny(
            "unknown_load_mode",
            f"load_mode {load_mode!r} is not a mode this build recognises",
        )
    if load_mode == "development" and not allow_development_load_mode:
        raise _deny(
            "development_load_mode",
            "these verdicts were produced by a development-mode host -- unpinned "
            "weights, unverified licence -- and must not clear a real publication. "
            "Recorded rather than assumed, because 'we were only testing' is how "
            "unverified weights reach production.",
        )
    model = classifier.get("model")
    if not isinstance(model, Mapping):
        raise _deny("classifier_missing", "the classifier pin carries no model")
    if expected_model is not None:
        for field in _PINNED_MODEL_FIELDS:
            if model.get(field) != expected_model.get(field):
                raise _deny(
                    "classifier_mismatch",
                    f"the clearance was produced by a model whose {field} is "
                    f"{model.get(field)!r}; this publication expects "
                    f"{expected_model.get(field)!r}. A verdict produced under a "
                    "different config is a verdict about a different decision "
                    "boundary.",
                )

    # -- thresholds --------------------------------------------------------
    thresholds_raw = manifest.get("thresholds")
    if not isinstance(thresholds_raw, Mapping):
        raise _deny("thresholds_missing", "the clearance records no thresholds")
    applied: dict[str, float] = {}
    for name in CLASS_ORDER:
        value = _unit(thresholds_raw.get(name))
        if value is None:
            raise _deny(
                "thresholds_missing",
                f"threshold {name!r} is {thresholds_raw.get(name)!r}, not a number "
                "in [0, 1]; a verdict whose threshold cannot be reconstructed "
                "cannot be re-audited",
            )
        applied[name] = value
    if expected_thresholds is not None and applied != expected_thresholds.as_mapping():
        raise _deny(
            "threshold_mismatch",
            f"the clearance applied {applied} and this sink's policy is "
            f"{expected_thresholds.as_mapping()}. score_threshold 0.3 and 0.5 are "
            "different classifiers to every consumer.",
        )

    # -- the items, against the publication --------------------------------
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise _deny("items_missing", "the clearance covers no items")
    published = list(media_ids)
    if not published:
        raise _deny(
            "publication_empty",
            "the caller presented no media ids to check, so nothing was verified. "
            "An empty publication cannot be cleared vacuously.",
        )
    manifest_ids = [item.get("media_id") for item in items]
    if len(set(manifest_ids)) != len(manifest_ids):
        raise _deny(
            "duplicate_item",
            "the clearance lists a media id twice, giving one photograph two "
            "verdicts and leaving a verifier to choose between them",
        )
    if manifest_ids != published:
        missing = [i for i in published if i not in set(manifest_ids)]
        extra = [i for i in manifest_ids if i not in set(published)]
        if missing or extra:
            raise _deny(
                "item_set_mismatch",
                f"the clearance covers a different set of items: {len(missing)} "
                f"published item(s) have no verdict and {len(extra)} verdict(s) are "
                "for items not being published. A missing verdict is indeterminate.",
            )
        raise _deny(
            "item_order_mismatch",
            "the clearance lists the same items in a different order. Order is part "
            "of the identity: a verifier comparing sets rather than sequences would "
            "accept a reordered book.",
        )

    overridden: list[str] = []
    for position, item in enumerate(items):
        media_id = item.get("media_id")
        verdict = item.get("verdict")
        if verdict not in KNOWN_VERDICTS:
            raise _deny(
                "unknown_verdict",
                f"item {position} carries verdict {verdict!r}, which this build does "
                "not recognise; an unknown verdict is denied, not guessed",
            )
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not _HEX64.match(evidence_id):
            raise _deny(
                "evidence_missing",
                f"item {position} names no proxy digest, so the verdict is not bound "
                "to any particular bytes",
            )
        if evidence_ids is not None:
            expected_evidence = evidence_ids.get(str(media_id))
            if expected_evidence is None:
                raise _deny(
                    "evidence_missing",
                    f"the publication supplies no proxy digest for item {position}, "
                    "so its verdict cannot be shown to be about the bytes being "
                    "published",
                )
            if expected_evidence != evidence_id:
                raise _deny(
                    "evidence_stale",
                    f"item {position} was classified from proxy "
                    f"{evidence_id[:12]}... and the publication is built from "
                    f"{expected_evidence[:12]}.... A proxy can be regenerated by a "
                    "better decoder or a corrected orientation, and a verdict about "
                    "the old one is not evidence about the new one.",
                )

        if verdict == "indeterminate":
            if item.get("override") is not None:
                raise _deny(
                    "override_on_indeterminate",
                    f"item {position} is indeterminate and carries an override. "
                    "'Nobody checked' and 'somebody checked and disagreed' are "
                    "different states, and only the second is a decision.",
                )
            raise _deny(
                "indeterminate_item",
                f"item {position} has no verdict "
                f"({item.get('indeterminate_reason')!r}). Absence is indeterminate "
                "and indeterminate blocks -- it may not be overridden by a flag, a "
                "default, a global bypass or an empty override list. One "
                "indeterminate item denies the whole publication.",
            )

        scores_raw = item.get("scores")
        if not isinstance(scores_raw, Mapping):
            raise _deny(
                "scores_missing",
                f"item {position} is {verdict!r} but records no scores; a "
                "determinate verdict was produced by a model that returned numbers, "
                "and without them it cannot be re-audited against a changed "
                "threshold",
            )
        scores: dict[str, float] = {}
        for name in CLASS_ORDER:
            value = _unit(scores_raw.get(name))
            if value is None:
                raise _deny(
                    "scores_missing",
                    f"item {position} score {name!r} is {scores_raw.get(name)!r}, "
                    "not a number in [0, 1]",
                )
            scores[name] = value

        # The stored verdict is re-derived, not trusted. A producer that wrote
        # `cleared` over a score above the threshold is the failure this whole
        # file exists to catch, and it would otherwise be invisible.
        fired = tuple(name for name in CLASS_ORDER if scores[name] >= applied[name])
        recomputed = "blocked" if fired else "cleared"
        if recomputed != verdict:
            raise _deny(
                "verdict_disagrees_with_scores",
                f"item {position} is recorded as {verdict!r} but its own scores "
                f"against its own thresholds say {recomputed!r} "
                f"(fired: {list(fired) or 'none'}). The producer applied a rule this "
                "verifier does not know.",
            )

        if verdict == "blocked":
            override = item.get("override")
            if not isinstance(override, Mapping):
                raise _deny(
                    "blocked_without_override",
                    f"item {position} scored above a threshold "
                    f"({', '.join(fired)}) and no human has decided to publish it "
                    "anyway",
                )
            if override.get("scope") != "item_and_sink":
                raise _deny(
                    "override_scope_invalid",
                    f"item {position} carries an override scoped "
                    f"{override.get('scope')!r}. `item_and_sink` is the only value: "
                    "a decision to print a photograph in a private family book is "
                    "not a decision to publish it.",
                )
            decided_by = override.get("decided_by")
            if not isinstance(decided_by, str) or not decided_by.strip():
                raise _deny(
                    "override_unattributed",
                    f"item {position} carries an override nobody owns. An override "
                    "that nobody owns is a bypass.",
                )
            if not isinstance(override.get("decided_at"), str):
                raise _deny(
                    "override_unattributed",
                    f"item {position} carries an override with no decision time",
                )
            overridden.append(str(media_id))
        elif item.get("override") is not None:
            raise _deny(
                "override_on_cleared",
                f"item {position} is cleared and carries an override, which would be "
                "noise that looks like a decision",
            )

    # -- the aggregate, recomputed ------------------------------------------
    recomputed_decision = compute_decision(items, sink)
    stored_decision = manifest.get("decision")
    if not isinstance(stored_decision, Mapping):
        raise _deny("decision_missing", "the clearance records no decision")
    for field, value in recomputed_decision.items():
        if field == "denied_reason":
            continue
        if stored_decision.get(field) != value:
            raise _deny(
                "decision_disagrees_with_items",
                f"the stored decision says {field}={stored_decision.get(field)!r} and "
                f"the items say {value!r}. The aggregate is recomputed by every "
                "verifier rather than trusted; a disagreement means the producer and "
                "this verifier are applying different rules.",
            )
    if not recomputed_decision["cleared_for_publication"]:
        raise _deny(
            "not_cleared",
            str(recomputed_decision.get("denied_reason") or "the items do not clear"),
        )

    # -- identity, last, because it is the cheapest thing to fake -----------
    stated_id = manifest.get("manifest_id")
    recomputed_id = compute_manifest_id(manifest)
    if not isinstance(stated_id, str) or stated_id != recomputed_id:
        raise _deny(
            "manifest_id_mismatch",
            f"the clearance states manifest_id {str(stated_id)[:12]}... and its own "
            f"body hashes to {recomputed_id[:12]}.... Either it was edited after it "
            "was signed, or two implementations disagree about the canonical form -- "
            "contracts/vectors/safety-clearance-manifest-id.json says which.",
        )

    return Clearance(
        manifest_id=recomputed_id,
        sink=sink,
        media_ids=tuple(published),
        item_count=len(items),
        overridden_media_ids=tuple(overridden),
    )
