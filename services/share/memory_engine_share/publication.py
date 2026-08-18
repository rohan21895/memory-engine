"""The share boundary: the one place a share link can come into existence.

WHAT EXISTS HERE AND WHAT DOES NOT -- READ THIS FIRST

There is no share service in this repository. No upload, no token store, no
public URL, no server. Share flows are Phase 4 Codex work
(`docs/memory-engine-build-plan.md`) and none of it is built.

So this module is deliberately ONE THING: the choke point that a share flow has
to go through, with the sensitive-content gate already welded into it. It mints
an authorisation and nothing else. Calling `authorise_share` does not upload
anything, because there is nothing here that could.

That is a smaller claim than "the share boundary is protected", and it is the
true one. What it buys is that when the upload lands, the gate is already in the
only function that can produce the thing the upload needs -- rather than being a
line somebody has to remember to add, in a file nobody has written yet, at the
moment they are thinking about S3 and not about photographs.

WHY A SHARE IS A DIFFERENT DECISION FROM A PRINT

`SafetyClearance.sink` is not decoration. A photograph cleared for a private
printed book has not been cleared for a public share link: the book goes to the
family, the link goes to whoever the link goes to, and the user approved neither
photo by photo. So a print clearance presented here is refused, by
`guard_share`, on the sink -- and there is no argument to this function that
would make it accept one.

THE AUTHORISATION IS NOT A TOKEN

`ShareAuthorization` carries no secret and grants no access. It is a record that
a specific ordered set of media ids was cleared for the share sink under a
specific manifest, and it exists so the eventual upload code can be written as
"take an authorisation" rather than "take a boolean and a list". A function that
takes a boolean is a function somebody passes `True` to.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PACKAGES = Path(__file__).resolve().parents[3] / "packages"
for _sibling in ("safety-gate",):
    _candidate = _PACKAGES / _sibling
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.append(str(_candidate))

from memory_engine_safety.gate import guard_share  # noqa: E402
from memory_engine_safety.verify import Clearance, PublicationBlocked  # noqa: E402

__all__ = [
    "ShareAuthorization",
    "ShareRequest",
    "authorise_share",
]


@dataclass(frozen=True)
class ShareRequest:
    """What is about to be shared, and with what.

    `media_ids` is ordered because the clearance is, and it must be read from
    the thing actually being shared -- the reel's EDL, the album's pages -- not
    copied from the manifest. Reading it from the manifest would make the check
    compare the manifest to itself.
    """

    media_ids: tuple[str, ...]
    #: media id -> the proxy digest the shared artifact was built from. Optional
    #: for the same reason it is optional in the verifier, and supplying it is
    #: what turns "a verdict exists" into "a verdict about these bytes".
    evidence_ids: Mapping[str, str] | None = None
    recipient_scope: str = ""

    @classmethod
    def over(
        cls,
        media_ids: Sequence[str],
        *,
        evidence_ids: Mapping[str, str] | None = None,
        recipient_scope: str = "",
    ) -> "ShareRequest":
        return cls(
            media_ids=tuple(media_ids),
            evidence_ids=dict(evidence_ids) if evidence_ids is not None else None,
            recipient_scope=recipient_scope,
        )


@dataclass(frozen=True)
class ShareAuthorization:
    """Proof that these photographs, in this order, cleared the share gate.

    Frozen, and carries the manifest id rather than the manifest: the upload
    that consumes this should be able to say WHICH clearance permitted it in a
    log line, and should not be able to edit one.
    """

    manifest_id: str
    media_ids: tuple[str, ...]
    overridden_media_ids: tuple[str, ...]
    recipient_scope: str

    @property
    def sink(self) -> str:
        return "share"


def authorise_share(
    clearance: Mapping[str, Any] | None,
    request: ShareRequest,
    *,
    expected_model: Mapping[str, Any] | None = None,
) -> ShareAuthorization:
    """Clear a share, or raise `PublicationBlocked`. There is no third outcome.

    Raises rather than returning `None` because the thing a caller does with a
    `None` is carry on. There is deliberately no `force`, no `skip_safety`, and
    no way to construct a `ShareAuthorization` from this module without going
    through the gate -- the dataclass is exported so callers can type against
    it, and a caller that builds one by hand is not being tricked by anything,
    they are writing a bypass, and that is a code review problem rather than a
    design one.
    """
    verified: Clearance = guard_share(
        clearance,
        media_ids=request.media_ids,
        evidence_ids=request.evidence_ids,
        expected_model=expected_model,
    )
    return ShareAuthorization(
        manifest_id=verified.manifest_id,
        media_ids=verified.media_ids,
        overridden_media_ids=verified.overridden_media_ids,
        recipient_scope=request.recipient_scope,
    )


# Re-exported so a caller can `except PublicationBlocked` without importing the
# safety package directly -- and so that this module's users find the exception
# on the same import line as the function that raises it.
PublicationBlocked = PublicationBlocked
