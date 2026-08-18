"""The share boundary (issue #21).

One function, `authorise_share`, and the sensitive-content gate is inside it.
There is no upload, no token store and no public URL in this service; see
`publication.py` for what that means and why the choke point exists before the
thing it chokes.
"""

from memory_engine_share.publication import (
    PublicationBlocked,
    ShareAuthorization,
    ShareRequest,
    authorise_share,
)

__all__ = [
    "PublicationBlocked",
    "ShareAuthorization",
    "ShareRequest",
    "authorise_share",
]
