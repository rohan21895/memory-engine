"""The three irreversible boundaries, each with its sink welded shut.

WHY THREE FUNCTIONS AND NOT ONE WITH A `sink` ARGUMENT

`verify_clearance` already takes a sink, and these are one line each. They exist
because a sink passed as an argument is a sink a caller can pass wrongly, and
the wrong value here is not a crash -- it is a photograph cleared for a private
printed book being accepted for a public share link. So the print worker imports
`guard_print` and there is no argument it can supply that would make it check
something else.

THE THREE, AND WHAT MAKES EACH IRREVERSIBLE

* `guard_print` -- a book cannot be patched once it is in the post. Called
  immediately before the PDF is written, inside the same operation, so there is
  no window in which the checked set and the exported set can differ.
* `guard_share` -- shared to people the user did not approve photo by photo.
  Called before a share token is minted or a public URL is activated.
* `guard_frontier_egress` -- the sharpest of the three and the only path in the
  system where a user's photograph reaches a third party. Irreversible the
  moment it leaves the device. Consent covers WHETHER a contact sheet may be
  sent; it does not cover WHAT IS ON IT, and this is the check that covers the
  second.

`local_export` is deliberately absent. The contract has the sink, but exporting
to the user's own disk publishes nothing on their behalf, and a gate there would
be the kind of check people learn to disable.

ON `allow_development_load_mode`

Present, off by default, and never set by any of the three wrappers. It exists
so `packages/eval-harness` can verify manifests produced by a development host
without the verifier growing a special case for tests -- a special case being a
thing that eventually gets used in production.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from memory_engine_safety.classify import Thresholds
from memory_engine_safety.verify import Clearance, PublicationBlocked, verify_clearance

__all__ = [
    "PublicationBlocked",
    "guard_frontier_egress",
    "guard_print",
    "guard_share",
]


def _guard(
    sink: str,
    manifest: Mapping[str, Any] | None,
    media_ids: Sequence[str],
    evidence_ids: Mapping[str, str] | None,
    expected_model: Mapping[str, Any] | None,
    expected_thresholds: Thresholds | None,
) -> Clearance:
    return verify_clearance(
        manifest,
        sink=sink,
        media_ids=media_ids,
        evidence_ids=evidence_ids,
        expected_model=expected_model,
        expected_thresholds=expected_thresholds,
        allow_development_load_mode=False,
    )


def guard_print(
    manifest: Mapping[str, Any] | None,
    *,
    media_ids: Sequence[str],
    evidence_ids: Mapping[str, str] | None = None,
    expected_model: Mapping[str, Any] | None = None,
    expected_thresholds: Thresholds | None = None,
) -> Clearance:
    """Refuse to emit a print export without a complete valid clearance."""
    return _guard(
        "print", manifest, media_ids, evidence_ids, expected_model, expected_thresholds
    )


def guard_share(
    manifest: Mapping[str, Any] | None,
    *,
    media_ids: Sequence[str],
    evidence_ids: Mapping[str, str] | None = None,
    expected_model: Mapping[str, Any] | None = None,
    expected_thresholds: Thresholds | None = None,
) -> Clearance:
    """Refuse to mint a share token or activate a public URL without one."""
    return _guard(
        "share", manifest, media_ids, evidence_ids, expected_model, expected_thresholds
    )


def guard_frontier_egress(
    manifest: Mapping[str, Any] | None,
    *,
    media_ids: Sequence[str],
    evidence_ids: Mapping[str, str] | None = None,
    expected_model: Mapping[str, Any] | None = None,
    expected_thresholds: Thresholds | None = None,
) -> Clearance:
    """Refuse to open the outbound request without one.

    Ordered against consent rather than merged with it: consent decides whether
    a contact sheet may be sent at all, this decides whether these particular
    photographs may be on it, and neither substitutes for the other. Both run
    before the journal entry, because the journal entry is written before the
    network call and a journalled send that was never permitted is a record of
    something that should not have been about to happen.
    """
    return _guard(
        "frontier_egress",
        manifest,
        media_ids,
        evidence_ids,
        expected_model,
        expected_thresholds,
    )
