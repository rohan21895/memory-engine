"""Near-duplicate detection and primary selection.

Phase 1 exit gate: "duplicates clustered". On a real library this is the
difference between an album of twelve near-identical burst frames and an album
of one good photo.

THE SHAPE OF THE PROBLEM

A 100k-item library has 5x10^9 pairs. Comparing every pair is not viable, and
neither is comparing every pair of embeddings -- 1152 floats each. So the
pipeline is cheapest-first, exactly as the build plan describes:

    exact content hash  ->  already collapsed by media_id, free
    pHash banding       ->  candidate pairs only, O(n) buckets
    embedding refine    ->  expensive, run on candidates only
    primary selection   ->  one winner per group

BANDED LSH, NOT BRUTE FORCE

Two near-identical photos have pHashes within a few bits of each other, but you
cannot find them by scanning. The standard trick: split the 64-bit hash into
bands, index by band, and only compare items sharing at least one band exactly.
Two hashes within `k` bits must agree exactly on at least one of `b` bands when
k < b -- pigeonhole. With 4 bands of 16 bits we can catch everything within 3
bits guaranteed, and most things within ~10 bits in practice.

This trades a little recall for making the problem linear. The recall lost is
recovered by the embedding pass, which is why the order matters.

DETERMINISM

Same library in, same groups and same primaries out, every time. Group
membership uses union-find over a candidate set built in sorted order, and every
tie -- in scoring, in ordering, in primary choice -- breaks on media_id. A
dedupe pass that reshuffled between runs would make "why did this photo
disappear from my album" unanswerable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# A pHash pair within this many bits is a duplicate CANDIDATE. Deliberately
# generous, because the embedding pass is what decides and a candidate that
# turns out to be different costs one comparison.
DEFAULT_HAMMING_THRESHOLD = 10

# ...but when no embedding exists, the pHash IS the decision, and generous
# becomes dangerous. Found by running the pipeline on a real library: two
# genuinely different scenes sat 6 bits apart and merged, which silently drops
# one of them from every automated output. Near-identical frames of the same
# moment are typically 0-3 bits apart, so 4 keeps real bursts together while
# refusing to guess across scenes.
#
# Asymmetric on purpose: a false merge deletes a photo and says nothing, a
# false split shows the user two similar photos. Only one of those is
# recoverable by the person looking at it.
DEFAULT_HAMMING_DECISIVE_THRESHOLD = 4

# Cosine distance below which two candidates are the same picture. Tight,
# because a false merge silently deletes a photo from every automated output
# while a false split merely shows the user two similar photos.
DEFAULT_EMBEDDING_THRESHOLD = 0.06

DEFAULT_BANDS = 4


@dataclass(frozen=True)
class Candidate:
    """One item entering dedupe. Deliberately not a MediaRecord: dedupe needs
    five fields, and taking the whole record would make it impossible to test
    without constructing one."""

    media_id: str
    phash_hex: str | None = None
    phash_bits: int = 64
    # `MediaRecord.perceptual.image_hash.algorithm`, carried because equal
    # length is NOT permission to compare. `phash-dct-64`, `dhash-64`,
    # `ahash-64` and `wavelet-64` are all sixteen hex characters and all mean
    # different things, and `phash-dct-64-v2` deliberately produces different
    # digits from `phash-dct-64` for the same picture (issue #14). A Hamming
    # distance across two of them is a number with no referent -- and this
    # module acts on that number by dropping a photo from every automated
    # output. Defaulted so existing callers keep working; a library that mixes
    # two algorithms simply never pairs across them.
    phash_algorithm: str = "phash-dct-64-v2"
    embedding: Sequence[float] | None = None
    # Fused quality in [0,1]. Drives primary selection.
    quality: float = 0.0
    # Capture instant, used only to detect bursts. None is common and fine.
    captured_utc: str | None = None
    # A user favourite outranks quality when choosing the primary: the system's
    # opinion about sharpness does not get to override a human's opinion about
    # which frame mattered.
    favorite: bool = False


@dataclass
class DuplicateGroup:
    group_id: str
    primary_media_id: str
    members: list[str]
    method: str
    similarity: dict[str, float] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.members)


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self._parent = {item: item for item in items}

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression, iterative to avoid recursion limits on long chains.
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Always attach the lexicographically larger root to the smaller one, so
        # the forest -- and therefore the grouping -- is independent of the order
        # unions were applied in.
        if ra > rb:
            ra, rb = rb, ra
        self._parent[rb] = ra

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in sorted(self._parent):
            out.setdefault(self.find(item), []).append(item)
        return out


def hamming_distance(
    a: str,
    b: str,
    a_algorithm: str | None = None,
    b_algorithm: str | None = None,
) -> int:
    """Bit distance between two hex digests of the same algorithm.

    The length guard was here from the start and is not enough: `phash-dct-64`,
    `phash-dct-64-v2`, `dhash-64`, `ahash-64` and `wavelet-64` are all sixteen
    hex characters, so every cross-algorithm pair passed it and returned a
    number that means nothing. It looks like a distance, dedupe treats it as
    one, and the outcome is a photo dropped from every automated output.

    The algorithms are optional so existing callers that pass two digests they
    already know are the same kind keep working; when both are given they must
    match.
    """
    if len(a) != len(b):
        raise ValueError(
            f"cannot compare hashes of different lengths ({len(a)} vs {len(b)}); "
            "a length mismatch means different algorithms, and comparing them "
            "would produce a meaningless number rather than an error"
        )
    if a_algorithm is not None and b_algorithm is not None and a_algorithm != b_algorithm:
        raise ValueError(
            f"cannot compare a {a_algorithm} digest against a {b_algorithm} one; "
            "equal length is not equal meaning, and the distance between them "
            "would be a number with no referent"
        )
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        raise ValueError("cannot compare a zero vector")
    return 1.0 - dot / (na * nb)


def band_keys(
    phash_hex: str,
    bands: int = DEFAULT_BANDS,
    algorithm: str = "phash-dct-64-v2",
) -> list[tuple[tuple[int, str], str]]:
    """Split a hash into `bands` equal slices, each keyed by position and algorithm.

    Position is part of the key so two hashes that happen to share a slice in
    *different* positions are not treated as candidates -- that would be a
    coincidence, not a similarity. The algorithm is part of the key for the
    stronger reason: two digests from different algorithms sharing a slice is
    not even a coincidence worth looking at, and a bucket that mixed them would
    hand `hamming_distance` a pair it must refuse.
    """
    if bands < 1 or len(phash_hex) % bands != 0:
        raise ValueError(f"cannot split {len(phash_hex)} hex chars into {bands} bands")
    width = len(phash_hex) // bands
    return [
        ((i, algorithm), phash_hex[i * width : (i + 1) * width]) for i in range(bands)
    ]


def candidate_pairs(
    items: Sequence[Candidate], *, bands: int = DEFAULT_BANDS
) -> list[tuple[str, str]]:
    """Pairs worth comparing properly, found in roughly linear time.

    Items with no pHash are excluded rather than compared against everything: a
    file we could not fingerprint is not evidence of similarity to anything.
    """
    buckets: dict[tuple[int, str], list[str]] = {}
    for item in sorted(items, key=lambda c: c.media_id):
        if not item.phash_hex:
            continue
        for key in band_keys(item.phash_hex, bands, item.phash_algorithm):
            buckets.setdefault(key, []).append(item.media_id)

    pairs: set[tuple[str, str]] = set()
    for members in buckets.values():
        # A band shared by a very large number of items is almost always a
        # degenerate hash -- all-black frames, blank scans -- and pairing them
        # all would be quadratic on exactly the junk we do not care about.
        if len(members) > 512:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                pairs.add((a, b) if a < b else (b, a))
    return sorted(pairs)


def find_duplicates(
    items: Sequence[Candidate],
    *,
    hamming_threshold: int = DEFAULT_HAMMING_THRESHOLD,
    hamming_decisive_threshold: int = DEFAULT_HAMMING_DECISIVE_THRESHOLD,
    embedding_threshold: float = DEFAULT_EMBEDDING_THRESHOLD,
    bands: int = DEFAULT_BANDS,
    group_id_for: Mapping[str, str] | None = None,
) -> list[DuplicateGroup]:
    """Cluster near-duplicates and pick one primary per group.

    `group_id_for` lets a caller supply stable group ids (keyed by the group's
    smallest media_id) so a re-run of an existing library keeps its identifiers.
    Without it, ids are derived deterministically from the membership rather
    than randomly, so two runs over the same library agree.
    """
    by_id = {item.media_id: item for item in items}
    if len(by_id) != len(items):
        raise ValueError("duplicate media_id in input; media_id is a primary key")

    union = _UnionFind(by_id)
    similarity: dict[tuple[str, str], float] = {}
    methods: dict[tuple[str, str], str] = {}

    for a_id, b_id in candidate_pairs(items, bands=bands):
        a, b = by_id[a_id], by_id[b_id]

        distance = hamming_distance(
            a.phash_hex, b.phash_hex, a.phash_algorithm, b.phash_algorithm
        )
        if distance > hamming_threshold:
            continue

        # The embedding is the arbiter when both sides have one. Two photos of
        # different subjects can land close in pHash -- two dark frames, two
        # sunsets -- and merging them would silently drop one from every album.
        if a.embedding is not None and b.embedding is not None:
            cosine = cosine_distance(a.embedding, b.embedding)
            if cosine > embedding_threshold:
                continue
            score = 1.0 - cosine
            method = "phash_bucket_embedding_refined"
        else:
            # No embedding to arbitrate, so the hash decides alone and the bar
            # is much higher. Anything between the two thresholds is a
            # plausible duplicate we decline to guess about.
            if distance > hamming_decisive_threshold:
                continue
            score = 1.0 - (distance / (a.phash_bits or 64))
            method = "phash_bucket"

        union.union(a_id, b_id)
        similarity[(a_id, b_id)] = score
        methods[(a_id, b_id)] = method

    groups: list[DuplicateGroup] = []
    for root, members in union.groups().items():
        if len(members) < 2:
            continue
        primary = select_primary([by_id[m] for m in members])
        group_method = (
            "phash_bucket_embedding_refined"
            if any(
                methods.get(pair) == "phash_bucket_embedding_refined"
                for pair in methods
                if pair[0] in members and pair[1] in members
            )
            else "phash_bucket"
        )
        anchor = min(members)
        group_id = (
            group_id_for.get(anchor)
            if group_id_for and anchor in group_id_for
            else _stable_group_id(members)
        )
        groups.append(
            DuplicateGroup(
                group_id=group_id,
                primary_media_id=primary.media_id,
                members=members,
                method=group_method,
                similarity={
                    member: similarity.get(
                        (min(member, primary.media_id), max(member, primary.media_id)),
                        1.0 if member == primary.media_id else 0.0,
                    )
                    for member in members
                },
            )
        )

    groups.sort(key=lambda g: g.members[0])
    return groups


def _stable_group_id(members: Sequence[str]) -> str:
    """A UUID derived from membership, so the same group gets the same id on
    every run without a database round-trip."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "memory-engine/dedupe/" + ",".join(sorted(members))))


def select_primary(members: Sequence[Candidate]) -> Candidate:
    """Choose the one photo from a group that represents it.

    Order of precedence, and the reasoning behind it:

    1. **A user favourite wins.** The system's opinion about sharpness does not
       get to overrule a human's opinion about which frame mattered.
    2. **Then quality.** The fused score, which is what the ranking engine is for.
    3. **Then media_id.** Not a tiebreak anyone would defend on aesthetics, but
       it is deterministic, and a dedupe pass that reshuffled between runs would
       make "why did this photo disappear" unanswerable.
    """
    if not members:
        raise ValueError("cannot select a primary from an empty group")
    return sorted(
        members,
        key=lambda c: (not c.favorite, -c.quality, c.media_id),
    )[0]


def assignments(groups: Sequence[DuplicateGroup]) -> dict[str, dict]:
    """Flatten groups into per-media updates media-db can apply directly.

    Shaped to match MediaRecord.dedupe so the caller does no translation --
    translation between a planner's output and a record's shape is where fields
    get quietly dropped.
    """
    out: dict[str, dict] = {}
    for group in groups:
        for member in group.members:
            out[member] = {
                "group_id": group.group_id,
                "is_primary": member == group.primary_media_id,
                "primary_media_id": group.primary_media_id,
                "similarity_to_primary": round(group.similarity.get(member, 0.0), 6),
                "group_size": group.size,
                "method": group.method,
            }
    return out
