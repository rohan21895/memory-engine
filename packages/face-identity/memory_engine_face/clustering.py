"""Face clustering: grouping embeddings into "probably the same person".

A cluster is a HYPOTHESIS. face-record.schema.json says so in the type itself --
`cluster` and `identity` are separate objects precisely because "a cluster is
allowed to be wrong, which is exactly why it is stored separately". Nothing in
this module ever produces a person_id, and nothing here can make a face eligible
for automated output. It groups; `identity.py` decides; a human arbitrates.

WHY COMPLETE LINKAGE, AND WHY THAT IS THE WHOLE DESIGN

The schema names HDBSCAN, and the build plan names HDBSCAN. HDBSCAN is not
installed in this repository and is not vendored here, so this module does not
claim to be it: clusters it produces report `method: "agglomerative_cosine"`,
which is a value the contract's enum already carries. Saying `hdbscan_cosine`
while running something else would be exactly the silent model swap hard rule 7
forbids, and `membership_strength` would then be read as an HDBSCAN membership
probability when it is not one.

What is implemented is agglomerative clustering under COMPLETE linkage, and the
choice is the safety argument for the whole package:

    single linkage merges A and C whenever some B is close to both.
    complete linkage merges two groups only when EVERY cross pair is close.

Single linkage is what every naive face grouper does, and it is how two people
become one cluster: one bad frame -- a profile, a blur, a half-occluded face --
sits between two identities and chains them. The person who then confirms that
cluster in the UI has just labelled two people with one name, and the mistake is
invisible because the montage they were shown is dominated by the majority.

Complete linkage buys a property that can be stated, tested, and depended on:

    INVARIANT: every cluster this module forms by merging has cosine diameter
    <= merge_threshold. No two faces in it are further apart than the threshold.

The price is over-splitting: one person becomes three clusters when their
appearance genuinely varies (sunglasses, five years, a beard). That is the
survivable failure. The user sees "is this the same person?" in the review queue
and taps yes; a merge question is cheap and reversible. The unsurvivable failure
is the other one -- a stranger inside a confirmed cluster -- and it is not
recoverable, because nothing downstream ever reconsiders membership.

Deliberately, the only thing that can produce a cluster wider than the threshold
is a HUMAN merge (`must_link`), and such a cluster reports `method:
"user_grouped"` so the invariant above stays honest and testable.

TRACKS: 120 CORRELATED VOTES ARE NOT 120 PIECES OF EVIDENCE

The schema's FaceTrack comment sets the rule and this module implements it: one
person walking through a four-second shot is ~120 FaceRecords, and clustering
them as 120 independent points would let a single video outvote an entire photo
library and would drag every centroid toward whoever was filmed longest. So a
track contributes exactly ONE point -- its representative -- and the resulting
cluster membership is then inherited by the rest of the track.

Inheritance is bounded on purpose:

    cluster membership CAN be inherited from a track.
    eligibility for automated output can NEVER be inherited (see identity.py).

A face with no embedding of its own has no evidence of its own. It may be
counted, grouped and laid out; it may not be named unattended.

WHAT THIS MODULE REFUSES TO DO

* It does not invent an embedding for a face that has none. Those faces come
  back in `unembedded_face_ids` and are the caller's problem, visibly.
* It does not choose the threshold. There is no defensible default distance for
  ArcFace without measuring one on real embeddings, and this repository has
  none, so `merge_threshold` is a required argument. A module-level constant
  here would be a number somebody would later cite as if it had been measured.

SCALE, STATED RATHER THAN DISCOVERED

The implementation is O(n^2) memory and O(n^3) worst-case time in the number of
representatives, with n = tracks + still faces. That is fine for the thousands
of representatives a family library produces and is NOT fine for 100k. The fix
is a blocking pass (pHash/coarse-centroid canopies) before this runs, and it is
not written yet. Recorded here so that the first person to point a real library
at it finds a stated limit rather than a hang.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .embeddings import (
    EmbeddingError,
    FaceEmbedding,
    centroid,
    cosine_distance,
)

__all__ = [
    "Cluster",
    "ClusterMembership",
    "ClusteringError",
    "ClusteringResult",
    "FaceObservation",
    "METHOD_AGGLOMERATIVE",
    "METHOD_SINGLETON",
    "METHOD_USER_GROUPED",
    "PairConstraints",
    "cluster_faces",
]


_BLAKE3_RE = re.compile(r"^[0-9a-f]{64}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Fixed namespace so a cluster id is a pure function of its membership: the same
# faces grouped the same way produce the same uuid on any machine, in any run,
# forever. Re-running clustering after nothing changed therefore renames nothing,
# and a cluster id appearing in two runs means the same thing in both.
CLUSTER_NAMESPACE = uuid.UUID("6f1f7b0e-6a5e-5f3d-9a5b-6a2b6f0d1c34")

METHOD_AGGLOMERATIVE = "agglomerative_cosine"
METHOD_SINGLETON = "singleton"
METHOD_USER_GROUPED = "user_grouped"


class ClusteringError(Exception):
    """Malformed input to clustering, or an impossible constraint set."""


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FaceObservation:
    """One detected face as clustering needs to see it.

    A projection of FaceRecord, not a duplicate of it: this package does not
    hand-write contract types (CLAUDE.md conventions), it consumes the fields it
    reasons about. `records.py` converts in both directions.
    """

    face_id: str
    embedding: FaceEmbedding | None = None
    track_id: str | None = None
    is_track_representative: bool = False
    quality: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.face_id, str) or not _BLAKE3_RE.match(self.face_id):
            raise ClusteringError(
                f"face_id must be a lowercase 64-hex BLAKE3 digest, got "
                f"{self.face_id!r}"
            )
        if self.embedding is not None and self.embedding.face_id != self.face_id:
            # An embedding carrying somebody else's face_id means two records got
            # crossed upstream. Every distance computed afterwards is correct
            # arithmetic on the wrong face, and no later check can see it.
            raise ClusteringError(
                f"{self.face_id}: embedding belongs to {self.embedding.face_id}"
            )
        if self.track_id is not None and not _is_uuid(self.track_id):
            raise ClusteringError(
                f"{self.face_id}: track_id must be a lowercase UUID, got "
                f"{self.track_id!r}"
            )
        if self.is_track_representative and self.track_id is None:
            raise ClusteringError(
                f"{self.face_id}: is_track_representative is set but there is no "
                "track to represent"
            )
        if self.quality is not None:
            if not isinstance(self.quality, (int, float)) or isinstance(
                self.quality, bool
            ):
                raise ClusteringError(f"{self.face_id}: quality must be a number")
            if not math.isfinite(self.quality) or not 0.0 <= self.quality <= 1.0:
                raise ClusteringError(
                    f"{self.face_id}: quality must be a Unit in [0,1], got "
                    f"{self.quality!r}"
                )


@dataclass(frozen=True)
class PairConstraints:
    """Human decisions that clustering is not allowed to overrule.

    `cannot_link` is what a "no, those are different people" answer leaves
    behind. Without it the next clustering run asks the same question again, and
    a user who has answered it twice reasonably concludes the product is not
    listening. It also does real work: it is the only mechanism that can keep
    twins apart when the embeddings say otherwise.

    `must_link` is a user merge. It is honoured unconditionally -- the human
    outranks the metric -- and it is the ONLY way a cluster wider than the merge
    threshold can exist. Such clusters are reported as `user_grouped` so that
    "this cluster satisfies the diameter invariant" stays a true statement about
    everything the algorithm produced on its own.
    """

    cannot_link: frozenset[frozenset[str]] = field(default_factory=frozenset)
    must_link: frozenset[frozenset[str]] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in ("cannot_link", "must_link"):
            pairs = getattr(self, name)
            normalised: set[frozenset[str]] = set()
            for pair in pairs:
                members = frozenset(pair)
                if len(members) != 2:
                    raise ClusteringError(
                        f"{name} entries must name exactly two distinct faces, got "
                        f"{sorted(pair)!r}"
                    )
                for face_id in members:
                    if not _BLAKE3_RE.match(face_id):
                        raise ClusteringError(
                            f"{name}: {face_id!r} is not a face_id digest"
                        )
                normalised.add(members)
            object.__setattr__(self, name, frozenset(normalised))
        contradiction = self.cannot_link & self.must_link
        if contradiction:
            # Silently preferring one over the other is how a user's "no" gets
            # overwritten by their earlier "yes" with no record of which won.
            pair = sorted(sorted(p) for p in contradiction)[0]
            raise ClusteringError(
                f"{pair[0]} and {pair[1]} are both must_link and cannot_link; "
                "resolve the contradiction where it was created, not here"
            )


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClusterMembership:
    """Per-face membership, shaped for FaceRecord.cluster."""

    face_id: str
    cluster_id: str
    method: str
    membership_strength: float | None
    is_noise: bool
    distance_to_centroid: float | None
    clustering_run_id: str
    inherited_from_track: bool


@dataclass(frozen=True, slots=True)
class Cluster:
    """One group. `diameter` is None only when the group has a single point."""

    cluster_id: str
    method: str
    representative_face_ids: tuple[str, ...]
    member_face_ids: tuple[str, ...]
    is_noise: bool
    diameter: float | None

    @property
    def size(self) -> int:
        return len(self.member_face_ids)


@dataclass(frozen=True, slots=True)
class ClusteringResult:
    run_id: str
    merge_threshold: float
    clusters: tuple[Cluster, ...]
    memberships: Mapping[str, ClusterMembership]
    unembedded_face_ids: tuple[str, ...]

    def membership_of(self, face_id: str) -> ClusterMembership | None:
        return self.memberships.get(face_id)

    @property
    def cluster_by_id(self) -> dict[str, Cluster]:
        return {cluster.cluster_id: cluster for cluster in self.clusters}


# ---------------------------------------------------------------------------
# The algorithm
# ---------------------------------------------------------------------------


def cluster_faces(
    observations: Iterable[FaceObservation],
    *,
    merge_threshold: float,
    run_id: str,
    constraints: PairConstraints | None = None,
) -> ClusteringResult:
    """Group faces into person hypotheses.

    `merge_threshold` is a cosine distance in (0, 2): two faces may share a
    cluster only if every pair in that cluster is at most this far apart. It is
    required rather than defaulted; see the module docstring.
    """
    if not _SLUG_RE.match(run_id or ""):
        raise ClusteringError(
            f"run_id must be a Slug (contract ClusterMembership.clustering_run_id), "
            f"got {run_id!r}"
        )
    if not isinstance(merge_threshold, (int, float)) or isinstance(
        merge_threshold, bool
    ):
        raise ClusteringError("merge_threshold must be a number")
    if not math.isfinite(merge_threshold) or not 0.0 < merge_threshold < 2.0:
        raise ClusteringError(
            f"merge_threshold must be a finite cosine distance in (0,2), got "
            f"{merge_threshold!r}"
        )
    constraints = constraints or PairConstraints()

    ordered = _ordered_observations(observations)
    tracks = _group_tracks(ordered)
    representatives = _representatives(ordered, tracks)

    unembedded = tuple(
        obs.face_id
        for obs in ordered
        if obs.embedding is None and _representative_for(obs, tracks) is None
    )

    vectors = {
        obs.face_id: obs.embedding
        for obs in representatives
        if obs.embedding is not None
    }
    groups = _agglomerate(vectors, merge_threshold, constraints)

    clusters: list[Cluster] = []
    memberships: dict[str, ClusterMembership] = {}
    for group in groups:
        clusters.append(
            _finalise_group(
                group=group,
                vectors=vectors,
                tracks=tracks,
                ordered=ordered,
                merge_threshold=merge_threshold,
                run_id=run_id,
                nearest_other=_nearest_other_distance(group, groups, vectors),
                memberships=memberships,
            )
        )
    clusters.sort(key=lambda c: c.cluster_id)
    return ClusteringResult(
        run_id=run_id,
        merge_threshold=float(merge_threshold),
        clusters=tuple(clusters),
        memberships=memberships,
        unembedded_face_ids=unembedded,
    )


def _ordered_observations(
    observations: Iterable[FaceObservation],
) -> tuple[FaceObservation, ...]:
    ordered = tuple(sorted(observations, key=lambda o: o.face_id))
    seen: set[str] = set()
    for obs in ordered:
        if obs.face_id in seen:
            raise ClusteringError(f"duplicate face_id in input: {obs.face_id}")
        seen.add(obs.face_id)
    spaces = {obs.embedding.space for obs in ordered if obs.embedding is not None}
    if len(spaces) > 1:
        raise ClusteringError(
            f"cannot cluster across embedding spaces: {sorted(spaces)}. A "
            "distance between two spaces is a number with no meaning"
        )
    return ordered


def _group_tracks(
    ordered: Sequence[FaceObservation],
) -> dict[str, tuple[FaceObservation, ...]]:
    tracks: dict[str, list[FaceObservation]] = {}
    for obs in ordered:
        if obs.track_id is not None:
            tracks.setdefault(obs.track_id, []).append(obs)
    return {track_id: tuple(members) for track_id, members in sorted(tracks.items())}


def _representative_for(
    obs: FaceObservation, tracks: Mapping[str, tuple[FaceObservation, ...]]
) -> FaceObservation | None:
    if obs.track_id is None:
        return None
    return _track_representative(tracks[obs.track_id])


def _track_representative(
    members: Sequence[FaceObservation],
) -> FaceObservation | None:
    """The one frame of a track that carries the track's vote.

    Declared representatives win. Two declared representatives is a producer bug
    and raises: picking one of them would decide, on a coin flip, which frame's
    embedding represents a person for the rest of the pipeline.
    """
    declared = [m for m in members if m.is_track_representative]
    if len(declared) > 1:
        raise ClusteringError(
            f"track {members[0].track_id} declares "
            f"{len(declared)} representatives ({', '.join(sorted(m.face_id for m in declared))}); "
            "exactly one frame carries a track's identity vote"
        )
    if len(declared) == 1:
        chosen = declared[0]
        if chosen.embedding is None:
            # A declared representative with no vector cannot vote. Falling back
            # silently would mean the track is represented by a frame the
            # producer explicitly did not choose, so this is a hard error at the
            # boundary rather than a quiet substitution.
            raise ClusteringError(
                f"{chosen.face_id} is the declared representative of track "
                f"{chosen.track_id} but carries no embedding; choose a "
                "representative that could be embedded"
            )
        return chosen
    embeddable = [m for m in members if m.embedding is not None]
    if not embeddable:
        return None
    # Highest face quality wins; face_id breaks ties so the choice does not
    # depend on iteration order. A track whose frames all lack a quality score
    # falls back to face_id alone, which is arbitrary but deterministic.
    return sorted(
        embeddable,
        key=lambda m: (-(m.quality if m.quality is not None else -1.0), m.face_id),
    )[0]


def _representatives(
    ordered: Sequence[FaceObservation],
    tracks: Mapping[str, tuple[FaceObservation, ...]],
) -> tuple[FaceObservation, ...]:
    """One point per track, plus every still face that has a vector."""
    chosen: list[FaceObservation] = []
    for obs in ordered:
        if obs.track_id is None:
            if obs.embedding is not None:
                chosen.append(obs)
            continue
        representative = _track_representative(tracks[obs.track_id])
        if representative is not None and representative.face_id == obs.face_id:
            chosen.append(obs)
    return tuple(chosen)


@dataclass
class _Group:
    """A working cluster during agglomeration."""

    members: list[str]
    user_grouped: bool

    @property
    def key(self) -> str:
        return min(self.members)


def _agglomerate(
    vectors: Mapping[str, FaceEmbedding],
    merge_threshold: float,
    constraints: PairConstraints,
) -> list[_Group]:
    face_ids = sorted(vectors)
    distances: dict[frozenset[str], float] = {}
    for i, a in enumerate(face_ids):
        for b in face_ids[i + 1 :]:
            distances[frozenset((a, b))] = cosine_distance(vectors[a], vectors[b])

    groups = _seed_groups(face_ids, constraints)

    while True:
        best: tuple[float, str, str] | None = None
        best_pair: tuple[int, int] | None = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if _forbidden(groups[i], groups[j], constraints):
                    continue
                linkage = _complete_linkage(groups[i], groups[j], distances)
                if linkage > merge_threshold:
                    continue
                candidate = (
                    linkage,
                    min(groups[i].key, groups[j].key),
                    max(groups[i].key, groups[j].key),
                )
                if best is None or candidate < best:
                    best = candidate
                    best_pair = (i, j)
        if best_pair is None:
            break
        i, j = best_pair
        groups[i].members.extend(groups[j].members)
        groups[i].user_grouped = groups[i].user_grouped or groups[j].user_grouped
        del groups[j]

    for group in groups:
        group.members.sort()
    groups.sort(key=lambda g: g.key)
    return groups


def _seed_groups(
    face_ids: Sequence[str], constraints: PairConstraints
) -> list[_Group]:
    """Start from singletons, with user merges already applied.

    A must_link pair whose faces are not both present is not an error: the user
    merged two clusters, then deleted a photo. The surviving face keeps its
    group and the missing one is simply absent.
    """
    parent = {face_id: face_id for face_id in face_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in sorted(sorted(p) for p in constraints.must_link):
        a, b = pair
        if a not in parent or b not in parent:
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            root, other = (ra, rb) if ra < rb else (rb, ra)
            parent[other] = root

    buckets: dict[str, list[str]] = {}
    for face_id in face_ids:
        buckets.setdefault(find(face_id), []).append(face_id)
    groups = [
        _Group(members=sorted(members), user_grouped=len(members) > 1)
        for _, members in sorted(buckets.items())
    ]
    return groups


def _forbidden(a: _Group, b: _Group, constraints: PairConstraints) -> bool:
    if not constraints.cannot_link:
        return False
    for x in a.members:
        for y in b.members:
            if frozenset((x, y)) in constraints.cannot_link:
                return True
    return False


def _complete_linkage(
    a: _Group, b: _Group, distances: Mapping[frozenset[str], float]
) -> float:
    """The FURTHEST cross-pair distance. This is the whole precision argument.

    `max` here rather than `min` (single linkage) or a mean (average linkage) is
    what makes the merged group's diameter bounded by the threshold. A mean would
    let one far pair hide behind nine near ones, which is the same chaining
    failure wearing a more respectable name.
    """
    return max(distances[frozenset((x, y))] for x in a.members for y in b.members)


def _finalise_group(
    *,
    group: _Group,
    vectors: Mapping[str, FaceEmbedding],
    tracks: Mapping[str, tuple[FaceObservation, ...]],
    ordered: Sequence[FaceObservation],
    merge_threshold: float,
    run_id: str,
    nearest_other: float | None,
    memberships: dict[str, ClusterMembership],
) -> Cluster:
    representatives = tuple(group.members)
    cluster_id = _cluster_id(representatives)

    by_face = {obs.face_id: obs for obs in ordered}
    members: list[str] = []
    for face_id in representatives:
        obs = by_face[face_id]
        if obs.track_id is None:
            members.append(face_id)
        else:
            members.extend(m.face_id for m in tracks[obs.track_id])
    members = sorted(set(members))

    diameter: float | None = None
    if len(representatives) > 1:
        diameter = max(
            cosine_distance(vectors[a], vectors[b])
            for index, a in enumerate(representatives)
            for b in representatives[index + 1 :]
        )

    if group.user_grouped:
        method = METHOD_USER_GROUPED
    elif len(representatives) == 1:
        method = METHOD_SINGLETON
    else:
        method = METHOD_AGGLOMERATIVE

    # `is_noise` means "too far from any cluster" (schema: ClusterMembership),
    # which is a statement about distance and not about group size. A singleton
    # that only stands alone because a human said "those two are different
    # people" is NOT noise: the nearest face is within the threshold, and
    # marking it noise would delete it from every automated surface as a reward
    # for having been correctly labelled. So noise is decided by measuring, not
    # by counting members.
    is_noise = (
        method == METHOD_SINGLETON
        and nearest_other is not None
        and nearest_other > merge_threshold
    )

    centre = centroid(vectors[face_id] for face_id in representatives)

    for face_id in members:
        obs = by_face[face_id]
        own = vectors.get(face_id) or obs.embedding
        distance = None
        if own is not None:
            try:
                distance = cosine_distance(own, centre)
            except EmbeddingError:  # pragma: no cover - space checked on input
                distance = None
        strength = _membership_strength(
            distance=distance,
            merge_threshold=merge_threshold,
            singleton=len(representatives) == 1,
        )
        memberships[face_id] = ClusterMembership(
            face_id=face_id,
            cluster_id=cluster_id,
            method=method,
            membership_strength=strength,
            is_noise=is_noise,
            distance_to_centroid=distance,
            clustering_run_id=run_id,
            inherited_from_track=face_id not in representatives,
        )

    return Cluster(
        cluster_id=cluster_id,
        method=method,
        representative_face_ids=representatives,
        member_face_ids=tuple(members),
        is_noise=is_noise,
        diameter=diameter,
    )


def _nearest_other_distance(
    group: _Group,
    groups: Sequence[_Group],
    vectors: Mapping[str, FaceEmbedding],
) -> float | None:
    """Distance from this group to the closest face outside it.

    None when there is nothing else in the library. A lone face in a library of
    one is not "too far from any cluster" -- there are no clusters -- and
    calling it noise would be an answer to a question nobody could have asked.
    """
    others = [
        face_id
        for other in groups
        if other is not group
        for face_id in other.members
    ]
    if not others:
        return None
    return min(
        cosine_distance(vectors[mine], vectors[theirs])
        for mine in group.members
        for theirs in others
    )


def _membership_strength(
    *, distance: float | None, merge_threshold: float, singleton: bool
) -> float | None:
    """Proximity to the cluster centre, scaled onto [0,1]. NOT a probability.

    HDBSCAN's `membership_strength` is a probability from a density model. This
    is not that, and reporting a number in the same field under a different
    method is only safe because `method` says `agglomerative_cosine`, which is
    what tells a reader which of the two they are holding.

    A singleton gets None rather than 1.0. Its distance to its own centre is
    exactly zero, so the formula would score the least-supported cluster in the
    library as maximally confident -- and `membership_strength` is precisely
    what the active-learning loop sorts on to decide what to ask a human about.
    """
    if singleton or distance is None:
        return None
    strength = 1.0 - (distance / merge_threshold)
    if strength < 0.0:
        return 0.0
    if strength > 1.0:
        return 1.0
    return strength


def _cluster_id(representative_face_ids: Sequence[str]) -> str:
    name = "face-cluster:v1:" + ",".join(sorted(representative_face_ids))
    return str(uuid.uuid5(CLUSTER_NAMESPACE, name))


def _is_uuid(value: object) -> bool:
    return isinstance(value, str) and bool(
        re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", value
        )
    )
