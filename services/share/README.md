# services/share

The share boundary for issue #21, and nothing else yet.

## What this is

`authorise_share(clearance, request)` is the only way to produce a
`ShareAuthorization`, and it cannot produce one without a complete, valid
`SafetyClearance` for the `share` sink over the exact ordered media ids being
shared. A missing manifest, an indeterminate item, a print clearance presented
for a share, a transposed class axis, an override nobody signed — all refuse.

## What this is not

There is no upload, no share-token store, no public URL and no server. Share
flows are Phase 4 work in `docs/memory-engine-build-plan.md` and belong to
Codex. This exists now so that when that lands, the gate is already inside the
only function that produces the thing an upload needs — rather than being a line
somebody has to remember to add later, in a file nobody has written, while they
are thinking about object storage and not about photographs.

Saying "the share boundary is protected" would be a larger claim than the code
supports. What is true is narrower and worth having: **the share boundary has a
choke point, and the gate is in it.**

## For Codex

When the upload lands, take a `ShareAuthorization` rather than a boolean and a
list. A function that takes a boolean is a function somebody passes `True` to.
