"""Clustering tests.

The properties that get the most attention are the ones whose violation produces
a plausible answer rather than an exception:

* **The diameter invariant.** Every algorithm-formed cluster has cosine diameter
  <= merge_threshold. This is the whole precision argument; a switch to single or
  average linkage breaks it and breaks nothing else visible.
* **Chaining.** A bridge face between two identities is the specific mechanism
  that merges two people, so it gets its own test with hand-placed vectors.
* **Nothing is lost and nothing is invented.** Every input face appears exactly
  once, in a cluster or in `unembedded_face_ids`.
* **Tracks vote once.** A long track must not outweigh a library.
* **Determinism.** Shuffled input, identical clusters and identical ids.
"""

from __future__ import annotations

import itertools
import math
import random
import unittest

from support import axis_vector, embedding, fid, unit  # noqa: E402

from memory_engine_face.clustering import (  # noqa: E402
    METHOD_AGGLOMERATIVE,
    METHOD_SINGLETON,
    METHOD_USER_GROUPED,
    ClusteringError,
    FaceObservation,
    PairConstraints,
    cluster_faces,
)
from memory_engine_face.embeddings import FaceEmbedding, cosine_distance  # noqa: E402


def obs(name: str, values: list[float], **kwargs) -> FaceObservation:
    face_id = fid(name)
    return FaceObservation(
        face_id=face_id, embedding=embedding(face_id, values), **kwargs
    )


def _mix(main: int, other: int, weight: float) -> list[float]:
    values = axis_vector(main)
    values[other % 512] += weight
    return unit(values)


def _rotate(base: list[float], distance: float) -> list[float]:
    """A unit vector at exactly `distance` cosine from `base` (an axis vector)."""
    cos = 1.0 - distance
    sin = math.sqrt(max(0.0, 1.0 - cos * cos))
    values = [cos * v for v in base]
    values[300] += sin
    return values


def two_people() -> list[FaceObservation]:
    """Two tight groups of three, a long way apart."""
    return [
        obs("a0", axis_vector(0)),
        obs("a1", _mix(0, 1, 0.02)),
        obs("a2", _mix(0, 2, 0.02)),
        obs("b0", axis_vector(10)),
        obs("b1", _mix(10, 11, 0.02)),
        obs("b2", _mix(10, 12, 0.02)),
    ]


class BasicTests(unittest.TestCase):
    def test_separates_two_people(self) -> None:
        result = cluster_faces(two_people(), merge_threshold=0.5, run_id="run-1")
        self.assertEqual(len(result.clusters), 2)
        self.assertEqual(sorted(c.size for c in result.clusters), [3, 3])

    def test_every_face_appears_exactly_once(self) -> None:
        faces = two_people()
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        placed = [f for c in result.clusters for f in c.member_face_ids]
        self.assertEqual(len(placed), len(set(placed)))
        self.assertEqual(
            set(placed) | set(result.unembedded_face_ids),
            {face.face_id for face in faces},
        )

    def test_memberships_cover_every_clustered_face(self) -> None:
        result = cluster_faces(two_people(), merge_threshold=0.5, run_id="run-1")
        for cluster in result.clusters:
            for face_id in cluster.member_face_ids:
                membership = result.membership_of(face_id)
                self.assertIsNotNone(membership)
                self.assertEqual(membership.cluster_id, cluster.cluster_id)
                self.assertEqual(membership.clustering_run_id, "run-1")

    def test_run_id_must_be_a_slug(self) -> None:
        for bad in ("", "Run 1", "run/1", "-run", "x" * 65):
            with self.subTest(bad=bad):
                with self.assertRaises(ClusteringError):
                    cluster_faces(two_people(), merge_threshold=0.5, run_id=bad)

    def test_threshold_must_be_a_finite_distance_in_the_open_interval(self) -> None:
        for bad in (0.0, 2.0, -0.1, 2.5, float("nan"), float("inf"), True):
            with self.subTest(bad=bad):
                with self.assertRaises(ClusteringError):
                    cluster_faces(two_people(), merge_threshold=bad, run_id="run-1")

    def test_duplicate_face_ids_raise(self) -> None:
        faces = two_people()
        with self.assertRaises(ClusteringError):
            cluster_faces(faces + [faces[0]], merge_threshold=0.5, run_id="run-1")

    def test_mixed_spaces_raise(self) -> None:
        faces = two_people()
        stray_id = fid("stray")
        faces.append(
            FaceObservation(
                face_id=stray_id,
                embedding=FaceEmbedding.from_raw(
                    stray_id, "adaface_ir101_512", axis_vector(3)
                ),
            )
        )
        with self.assertRaises(ClusteringError):
            cluster_faces(faces, merge_threshold=0.5, run_id="run-1")

    def test_embedding_belonging_to_another_face_raises(self) -> None:
        with self.assertRaises(ClusteringError):
            FaceObservation(
                face_id=fid("a"), embedding=embedding(fid("b"), axis_vector(0))
            )


class CompleteLinkageTests(unittest.TestCase):
    def test_diameter_never_exceeds_the_threshold(self) -> None:
        rng = random.Random(11)
        faces = [
            obs(
                f"r{i}",
                unit([rng.gauss(0, 1) if d < 6 else 0.0 for d in range(512)]),
            )
            for i in range(24)
        ]
        vectors = {f.face_id: f.embedding for f in faces}
        for threshold in (0.2, 0.5, 0.9, 1.3):
            with self.subTest(threshold=threshold):
                result = cluster_faces(
                    faces, merge_threshold=threshold, run_id="run-1"
                )
                for cluster in result.clusters:
                    for a, b in itertools.combinations(
                        cluster.representative_face_ids, 2
                    ):
                        self.assertLessEqual(
                            cosine_distance(vectors[a], vectors[b]),
                            threshold + 1e-12,
                            f"{cluster.cluster_id} exceeds its own diameter bound",
                        )

    def test_a_bridge_face_does_not_chain_two_identities(self) -> None:
        # A is at 0.0, C is 0.9 away, B sits halfway. Single linkage merges all
        # three (each hop is ~0.45); complete linkage refuses, because A and C
        # are 0.9 apart and the threshold is 0.5.
        left = axis_vector(0)
        right = _rotate(left, 0.9)
        middle = _rotate(left, 0.45)
        faces = [obs("a", left), obs("b", middle), obs("c", right)]
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        sizes = sorted(c.size for c in result.clusters)
        self.assertNotEqual(sizes, [3], "single-linkage chaining merged two people")
        self.assertEqual(sizes, [1, 2])

    def test_a_cluster_forms_only_when_every_cross_pair_is_close(self) -> None:
        left = axis_vector(0)
        near = _rotate(left, 0.30)
        far = _rotate(left, 0.34)
        faces = [obs("a", left), obs("b", near), obs("c", far)]
        wide = cluster_faces(faces, merge_threshold=0.35, run_id="run-1")
        self.assertEqual([c.size for c in wide.clusters], [3])
        # 0.32 excludes the (a,c) pair, so `c` cannot join even though it is
        # well within the threshold of `b`. Average linkage would admit it.
        tight = cluster_faces(faces, merge_threshold=0.32, run_id="run-1")
        self.assertEqual(sorted(c.size for c in tight.clusters), [1, 2])


class NoiseTests(unittest.TestCase):
    def test_a_far_singleton_is_noise(self) -> None:
        faces = two_people() + [obs("lonely", axis_vector(200))]
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        lonely = result.membership_of(fid("lonely"))
        self.assertTrue(lonely.is_noise)
        self.assertEqual(lonely.method, METHOD_SINGLETON)

    def test_a_singleton_held_apart_by_a_human_is_not_noise(self) -> None:
        # `is_noise` means "too far from any cluster", a statement about
        # distance. A face kept alone by a cannot_link is close to its
        # neighbours; calling it noise would delete it from automated output as
        # a reward for having been correctly labelled.
        faces = two_people()
        constraints = PairConstraints(
            cannot_link=frozenset(
                {
                    frozenset({fid("a0"), fid("a1")}),
                    frozenset({fid("a0"), fid("a2")}),
                }
            )
        )
        result = cluster_faces(
            faces, merge_threshold=0.5, run_id="run-1", constraints=constraints
        )
        a0 = result.membership_of(fid("a0"))
        self.assertEqual(a0.method, METHOD_SINGLETON)
        self.assertFalse(a0.is_noise)

    def test_the_only_face_in_a_library_is_not_noise(self) -> None:
        result = cluster_faces(
            [obs("only", axis_vector(0))], merge_threshold=0.5, run_id="run-1"
        )
        self.assertFalse(result.clusters[0].is_noise)

    def test_a_singleton_has_no_membership_strength(self) -> None:
        result = cluster_faces(
            [obs("only", axis_vector(0))], merge_threshold=0.5, run_id="run-1"
        )
        self.assertIsNone(result.membership_of(fid("only")).membership_strength)

    def test_membership_strength_falls_as_distance_to_centre_grows(self) -> None:
        loose = cluster_faces(
            [obs("a", axis_vector(0)), obs("b", _rotate(axis_vector(0), 0.30))],
            merge_threshold=0.5,
            run_id="run-1",
        )
        tight = cluster_faces(
            [obs("a", axis_vector(0)), obs("b", _rotate(axis_vector(0), 0.05))],
            merge_threshold=0.5,
            run_id="run-1",
        )
        loose_strength = loose.membership_of(fid("a")).membership_strength
        tight_strength = tight.membership_of(fid("a")).membership_strength
        self.assertGreater(tight_strength, loose_strength)
        for strength in (loose_strength, tight_strength):
            self.assertGreaterEqual(strength, 0.0)
            self.assertLessEqual(strength, 1.0)


class TrackTests(unittest.TestCase):
    TRACK = "1b6d4e90-3a72-4c85-9f01-8d5e2b7a4c63"

    def track_faces(self, count: int, *, representative: int | None = 0):
        return [
            obs(
                f"t{i}",
                _mix(0, 400 + i, 0.01),
                track_id=self.TRACK,
                is_track_representative=(i == representative),
                quality=0.5 + i * 0.01,
            )
            for i in range(count)
        ]

    def test_a_track_contributes_one_representative(self) -> None:
        faces = self.track_faces(30)
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(len(result.clusters[0].representative_face_ids), 1)
        self.assertEqual(result.clusters[0].size, 30)

    def test_a_long_track_does_not_outvote_a_library(self) -> None:
        faces = self.track_faces(30) + [
            obs("s0", axis_vector(10)),
            obs("s1", _mix(10, 11, 0.02)),
            obs("s2", _mix(10, 12, 0.02)),
        ]
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        self.assertEqual(len(result.clusters), 2)
        self.assertEqual(
            sorted(len(c.representative_face_ids) for c in result.clusters), [1, 3]
        )

    def test_track_members_inherit_membership_and_are_marked(self) -> None:
        faces = self.track_faces(5)
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        inherited = [
            result.membership_of(f.face_id).inherited_from_track for f in faces
        ]
        self.assertEqual(inherited.count(False), 1)
        self.assertEqual(inherited.count(True), 4)

    def test_an_unembeddable_track_member_still_gets_the_cluster(self) -> None:
        faces = self.track_faces(4)
        blind_id = fid("t-blind")
        faces.append(
            FaceObservation(
                face_id=blind_id, embedding=None, track_id=self.TRACK, quality=0.1
            )
        )
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        membership = result.membership_of(blind_id)
        self.assertIsNotNone(membership)
        self.assertTrue(membership.inherited_from_track)
        self.assertIsNone(membership.distance_to_centroid)
        self.assertNotIn(blind_id, result.unembedded_face_ids)

    def test_two_declared_representatives_raise(self) -> None:
        faces = self.track_faces(3)
        faces[1] = FaceObservation(
            face_id=faces[1].face_id,
            embedding=faces[1].embedding,
            track_id=self.TRACK,
            is_track_representative=True,
            quality=faces[1].quality,
        )
        with self.assertRaises(ClusteringError):
            cluster_faces(faces, merge_threshold=0.5, run_id="run-1")

    def test_a_declared_representative_without_an_embedding_raises(self) -> None:
        faces = self.track_faces(3, representative=None)
        faces.append(
            FaceObservation(
                face_id=fid("t-blind"),
                embedding=None,
                track_id=self.TRACK,
                is_track_representative=True,
            )
        )
        with self.assertRaises(ClusteringError):
            cluster_faces(faces, merge_threshold=0.5, run_id="run-1")

    def test_without_a_declaration_the_best_quality_frame_represents(self) -> None:
        faces = self.track_faces(4, representative=None)
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        best = max(faces, key=lambda f: f.quality)
        self.assertEqual(result.clusters[0].representative_face_ids, (best.face_id,))

    def test_a_track_with_no_embeddable_frame_is_reported_unembedded(self) -> None:
        faces = [
            FaceObservation(face_id=fid(f"t{i}"), embedding=None, track_id=self.TRACK)
            for i in range(3)
        ]
        result = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        self.assertEqual(result.clusters, ())
        self.assertEqual(len(result.unembedded_face_ids), 3)

    def test_track_id_must_be_a_uuid(self) -> None:
        with self.assertRaises(ClusteringError):
            FaceObservation(
                face_id=fid("a"),
                embedding=embedding(fid("a"), axis_vector(0)),
                track_id="track-1",
            )

    def test_representative_without_a_track_raises(self) -> None:
        with self.assertRaises(ClusteringError):
            FaceObservation(
                face_id=fid("a"),
                embedding=embedding(fid("a"), axis_vector(0)),
                is_track_representative=True,
            )


class ConstraintTests(unittest.TestCase):
    def test_cannot_link_keeps_two_faces_apart(self) -> None:
        faces = [obs("a", axis_vector(0)), obs("b", _mix(0, 1, 0.02))]
        merged = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        self.assertEqual(len(merged.clusters), 1)
        split = cluster_faces(
            faces,
            merge_threshold=0.5,
            run_id="run-1",
            constraints=PairConstraints(
                cannot_link=frozenset({frozenset({fid("a"), fid("b")})})
            ),
        )
        self.assertEqual(len(split.clusters), 2)

    def test_must_link_joins_faces_the_metric_would_separate(self) -> None:
        faces = [obs("a", axis_vector(0)), obs("b", axis_vector(200))]
        result = cluster_faces(
            faces,
            merge_threshold=0.5,
            run_id="run-1",
            constraints=PairConstraints(
                must_link=frozenset({frozenset({fid("a"), fid("b")})})
            ),
        )
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(result.clusters[0].method, METHOD_USER_GROUPED)
        self.assertGreater(result.clusters[0].diameter, 0.5)

    def test_contradictory_constraints_raise(self) -> None:
        pair = frozenset({fid("a"), fid("b")})
        with self.assertRaises(ClusteringError):
            PairConstraints(cannot_link=frozenset({pair}), must_link=frozenset({pair}))

    def test_a_constraint_naming_one_face_raises(self) -> None:
        with self.assertRaises(ClusteringError):
            PairConstraints(cannot_link=frozenset({frozenset({fid("a")})}))

    def test_a_constraint_naming_a_non_digest_raises(self) -> None:
        with self.assertRaises(ClusteringError):
            PairConstraints(must_link=frozenset({frozenset({fid("a"), "nope"})}))

    def test_must_link_for_an_absent_face_is_ignored_not_fatal(self) -> None:
        result = cluster_faces(
            [obs("a", axis_vector(0))],
            merge_threshold=0.5,
            run_id="run-1",
            constraints=PairConstraints(
                must_link=frozenset({frozenset({fid("a"), fid("deleted")})})
            ),
        )
        self.assertEqual(len(result.clusters), 1)
        self.assertEqual(result.clusters[0].method, METHOD_SINGLETON)


class DeterminismTests(unittest.TestCase):
    def test_shuffled_input_gives_identical_clusters_and_ids(self) -> None:
        faces = two_people()
        expected = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        rng = random.Random(3)
        for _ in range(5):
            shuffled = faces[:]
            rng.shuffle(shuffled)
            actual = cluster_faces(shuffled, merge_threshold=0.5, run_id="run-1")
            self.assertEqual(
                [(c.cluster_id, c.member_face_ids) for c in actual.clusters],
                [(c.cluster_id, c.member_face_ids) for c in expected.clusters],
            )

    def test_cluster_id_is_a_function_of_membership_only(self) -> None:
        faces = two_people()
        first = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        second = cluster_faces(faces, merge_threshold=0.5, run_id="run-2")
        self.assertEqual(
            [c.cluster_id for c in first.clusters],
            [c.cluster_id for c in second.clusters],
        )
        self.assertNotEqual(
            first.membership_of(fid("a0")).clustering_run_id,
            second.membership_of(fid("a0")).clustering_run_id,
        )

    def test_changing_membership_changes_the_id(self) -> None:
        faces = two_people()
        before = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        after = cluster_faces(
            faces + [obs("a3", _mix(0, 3, 0.02))],
            merge_threshold=0.5,
            run_id="run-1",
        )
        self.assertNotEqual(
            {c.cluster_id for c in before.clusters},
            {c.cluster_id for c in after.clusters},
        )

    def test_method_is_never_reported_as_hdbscan(self) -> None:
        # HDBSCAN is not installed and is not what runs. Reporting its name
        # would make `membership_strength` read as a density probability.
        result = cluster_faces(two_people(), merge_threshold=0.5, run_id="run-1")
        for cluster in result.clusters:
            self.assertIn(
                cluster.method,
                {METHOD_AGGLOMERATIVE, METHOD_SINGLETON, METHOD_USER_GROUPED},
            )


def exact_half_distance_pair():
    """Two vectors whose cosine distance is EXACTLY 0.5, no rounding.

    a is a unit axis; b puts 0.5 in four components, so its norm is
    sqrt(4 * 0.25) = 1.0 exactly in binary floating point and the dot product is
    exactly 0.5. Threshold behaviour can therefore be asserted at the boundary
    rather than near it.
    """
    a = axis_vector(0)
    b = [0.0] * 512
    for index in range(4):
        b[index] = 0.5
    return [obs("a", a), obs("b", b)]


class MutationSurvivorTests(unittest.TestCase):
    """Properties the first mutation sweep proved nothing was checking."""

    def test_the_merge_threshold_is_inclusive_at_the_boundary(self) -> None:
        # SURVIVOR clu04: `linkage > threshold` -> `linkage >= threshold`
        # survived, because every existing fixture sat near a threshold rather
        # than exactly on one. Two faces exactly `merge_threshold` apart must
        # merge; a hair further apart must not.
        faces = exact_half_distance_pair()
        self.assertEqual(
            cosine_distance(faces[0].embedding, faces[1].embedding), 0.5
        )
        inclusive = cluster_faces(faces, merge_threshold=0.5, run_id="run-1")
        self.assertEqual(len(inclusive.clusters), 1)
        exclusive = cluster_faces(
            faces, merge_threshold=0.4999999999, run_id="run-1"
        )
        self.assertEqual(len(exclusive.clusters), 2)

    def test_membership_strength_is_the_stated_formula(self) -> None:
        # SURVIVOR clu09: `distance / merge_threshold` -> `distance *
        # merge_threshold` survived, because the existing test only checked that
        # a tighter cluster scored higher, which both formulas satisfy. This
        # asserts the value: distance-to-centroid 0.13397... at threshold 0.5 is
        # 1 - 0.26794... = 0.73205..., where the mutant reports 0.93301...
        result = cluster_faces(
            exact_half_distance_pair(), merge_threshold=0.5, run_id="run-1"
        )
        membership = result.membership_of(fid("a"))
        self.assertAlmostEqual(
            membership.distance_to_centroid, 0.1339745962155613, places=12
        )
        self.assertAlmostEqual(
            membership.membership_strength, 0.7320508075688774, places=12
        )

    def test_a_cluster_id_does_not_depend_on_the_order_of_its_members(self) -> None:
        # SURVIVOR clu13: dropping the `sorted()` inside the id survived because
        # the only caller happens to pass a sorted tuple today. The helper's
        # contract is order-independence; testing it directly stops a future
        # caller from breaking it silently.
        from memory_engine_face.clustering import _cluster_id

        members = [fid("z"), fid("a"), fid("m")]
        self.assertEqual(
            _cluster_id(members), _cluster_id(list(reversed(members)))
        )
        self.assertEqual(_cluster_id(members), _cluster_id(sorted(members)))

    def test_unclustered_faces_come_back_in_a_stable_order(self) -> None:
        # SURVIVOR clu14: not sorting the input survived because the existing
        # determinism test only compared clusters, and clustering re-sorts
        # internally. `unembedded_face_ids` is the output that carries the input
        # order straight through.
        blind = [
            FaceObservation(face_id=fid(f"blind{i}"), embedding=None)
            for i in range(6)
        ]
        forward = cluster_faces(blind, merge_threshold=0.5, run_id="run-1")
        backward = cluster_faces(
            list(reversed(blind)), merge_threshold=0.5, run_id="run-1"
        )
        self.assertEqual(
            forward.unembedded_face_ids, tuple(sorted(forward.unembedded_face_ids))
        )
        self.assertEqual(forward.unembedded_face_ids, backward.unembedded_face_ids)


class ValidationTests(unittest.TestCase):
    def test_quality_must_be_a_unit(self) -> None:
        for bad in (-0.1, 1.1, float("nan"), True, "high"):
            with self.subTest(bad=bad):
                with self.assertRaises(ClusteringError):
                    FaceObservation(
                        face_id=fid("a"),
                        embedding=embedding(fid("a"), axis_vector(0)),
                        quality=bad,
                    )

    def test_face_id_must_be_a_digest(self) -> None:
        with self.assertRaises(ClusteringError):
            FaceObservation(face_id="face-1")

    def test_faces_without_embeddings_are_reported_not_dropped(self) -> None:
        faces = two_people()
        blind = FaceObservation(face_id=fid("blind"), embedding=None)
        result = cluster_faces(faces + [blind], merge_threshold=0.5, run_id="run-1")
        self.assertEqual(result.unembedded_face_ids, (fid("blind"),))
        self.assertIsNone(result.membership_of(fid("blind")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
