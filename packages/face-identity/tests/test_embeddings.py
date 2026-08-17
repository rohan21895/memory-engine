"""Embedding tests.

Written against the failure mode this file exists to prevent: a wrong embedding
does not raise, it produces a plausible number. So the tests concentrate on the
boundary checks (norm tolerance, space, alignment, NaN, zero) rather than on the
arithmetic, which is hard to get subtly wrong and easy to get loudly wrong.

Every threshold is tested on both sides, so moving a constant fails a test
rather than silently loosening a check.
"""

from __future__ import annotations

import math
import random
import unittest

from support import DIMENSIONS, SPACE, axis_vector, fid, unit  # noqa: E402

from memory_engine_face.embeddings import (  # noqa: E402
    NORM_TOLERANCE,
    AlignedFace,
    AlignmentMismatch,
    EmbeddingError,
    FaceEmbedding,
    SpaceMismatch,
    centroid,
    check_alignment,
    cosine_distance,
    cosine_similarity,
)


class ConstructionTests(unittest.TestCase):
    def test_accepts_a_unit_vector(self) -> None:
        vector = FaceEmbedding(fid("a"), SPACE, axis_vector(0))
        self.assertEqual(vector.dimensions, DIMENSIONS)
        self.assertEqual(vector.values[0], 1.0)

    def test_rejects_a_bad_face_id(self) -> None:
        for bad in ("", "not-a-digest", fid("a").upper(), fid("a")[:63], 7):
            with self.subTest(bad=bad):
                with self.assertRaises(EmbeddingError):
                    FaceEmbedding(bad, SPACE, axis_vector(0))  # type: ignore[arg-type]

    def test_rejects_an_unknown_space(self) -> None:
        with self.assertRaises(EmbeddingError):
            FaceEmbedding(fid("a"), "siglip2_base_768", axis_vector(0))

    def test_rejects_a_dimension_mismatch(self) -> None:
        with self.assertRaises(EmbeddingError):
            FaceEmbedding(fid("a"), SPACE, [1.0] + [0.0] * 10)

    def test_rejects_non_finite_components(self) -> None:
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                values = axis_vector(0)
                values[5] = bad
                with self.assertRaises(EmbeddingError):
                    FaceEmbedding(fid("a"), SPACE, values)

    def test_rejects_the_zero_vector(self) -> None:
        with self.assertRaises(EmbeddingError):
            FaceEmbedding(fid("a"), SPACE, [0.0] * DIMENSIONS)

    def test_norm_tolerance_is_enforced_on_both_sides(self) -> None:
        # Just inside the tolerance: accepted and corrected.
        inside = axis_vector(0, scale=1.0 + NORM_TOLERANCE * 0.5)
        vector = FaceEmbedding(fid("a"), SPACE, inside)
        self.assertAlmostEqual(
            math.sqrt(math.fsum(v * v for v in vector.values)), 1.0, places=12
        )
        # Just outside: refused, rather than silently normalised.
        outside = axis_vector(0, scale=1.0 + NORM_TOLERANCE * 2.0)
        with self.assertRaises(EmbeddingError):
            FaceEmbedding(fid("a"), SPACE, outside)

    def test_drift_is_corrected_so_distances_are_exact(self) -> None:
        drifted = FaceEmbedding(fid("a"), SPACE, axis_vector(0, scale=1.0 + 5e-4))
        exact = FaceEmbedding(fid("b"), SPACE, axis_vector(0))
        self.assertEqual(cosine_distance(drifted, exact), 0.0)

    def test_from_raw_normalises_anything_finite_and_non_zero(self) -> None:
        vector = FaceEmbedding.from_raw(fid("a"), SPACE, axis_vector(0, scale=20.0))
        self.assertAlmostEqual(vector.values[0], 1.0, places=12)

    def test_from_raw_still_refuses_nan_and_zero(self) -> None:
        with self.assertRaises(EmbeddingError):
            FaceEmbedding.from_raw(fid("a"), SPACE, [float("nan")] * DIMENSIONS)
        with self.assertRaises(EmbeddingError):
            FaceEmbedding.from_raw(fid("a"), SPACE, [0.0] * DIMENSIONS)

    def test_is_frozen(self) -> None:
        vector = FaceEmbedding(fid("a"), SPACE, axis_vector(0))
        with self.assertRaises(Exception):
            vector.face_id = fid("b")  # type: ignore[misc]


class _Embedder:
    space = SPACE
    required_landmark_scheme = "insightface_5"

    def embed(self, faces):  # pragma: no cover - protocol shape only
        return tuple(None for _ in faces)


class MutationSurvivorTests(unittest.TestCase):
    """Properties the first mutation sweep proved nothing was checking.

    Each test names the mutation it exists to kill. They are here rather than
    folded into the sections above because the reason they exist -- "a plausible
    edit survived" -- is worth keeping next to them.
    """

    def test_the_norm_bound_is_absolute_not_relative_to_the_constant(self) -> None:
        # SURVIVOR emb01: NORM_TOLERANCE 1e-3 -> 1e-1 survived, because the
        # existing test derived its inputs FROM the constant and moved with it.
        # These numbers do not move.
        with self.assertRaises(EmbeddingError):
            FaceEmbedding(fid("a"), SPACE, axis_vector(0, scale=1.05))
        with self.assertRaises(EmbeddingError):
            FaceEmbedding(fid("a"), SPACE, axis_vector(0, scale=0.95))
        FaceEmbedding(fid("a"), SPACE, axis_vector(0, scale=1.0005))

    def test_the_zero_vector_is_refused_for_being_the_zero_vector(self) -> None:
        # SURVIVOR emb03: `norm == 0.0` -> `norm < 0.0` still raised, because
        # the zero vector then failed the norm-tolerance check instead. Same
        # exception, wrong explanation -- and a wrong explanation sends whoever
        # is debugging a broken embedder to the wrong producer.
        with self.assertRaisesRegex(EmbeddingError, "zero vector"):
            FaceEmbedding(fid("a"), SPACE, [0.0] * DIMENSIONS)

    def test_the_lower_clamp_is_reachable_and_is_reached(self) -> None:
        # SURVIVOR emb05: `dot < -1.0` -> `dot < -2.0` survived because no test
        # used a pair whose floating-point dot product actually undershoots -1.
        # This one does: the very first vector from this seed gives
        # -1.0000000000000002, and without the clamp the distance exceeds 2.0.
        rng = random.Random(0)
        raw = [rng.gauss(0, 1) for _ in range(DIMENSIONS)]
        a = FaceEmbedding.from_raw(fid("a"), SPACE, raw)
        b = FaceEmbedding.from_raw(fid("b"), SPACE, [-x for x in raw])
        self.assertLess(
            math.fsum(x * y for x, y in zip(a.values, b.values)),
            -1.0,
            "this fixture no longer exercises the clamp; find another seed",
        )
        self.assertEqual(cosine_similarity(a, b), -1.0)
        self.assertEqual(cosine_distance(a, b), 2.0)

    def test_the_dot_product_does_not_depend_on_component_order(self) -> None:
        # SURVIVOR emb12: math.fsum -> sum survived, because the existing test
        # compared d(a,b) with d(b,a), which multiplies the same pairs in the
        # same order. This permutes the components of both vectors together:
        # mathematically the same dot product, a different summation order, and
        # only an exactly-rounded sum returns the identical float.
        rng = random.Random(5)
        left = unit([rng.gauss(0, 1) for _ in range(DIMENSIONS)])
        right = unit([rng.gauss(0, 1) for _ in range(DIMENSIONS)])
        order = list(range(DIMENSIONS))
        rng.shuffle(order)
        straight = cosine_distance(
            FaceEmbedding.from_raw(fid("a"), SPACE, left),
            FaceEmbedding.from_raw(fid("b"), SPACE, right),
        )
        permuted = cosine_distance(
            FaceEmbedding.from_raw(fid("a"), SPACE, [left[i] for i in order]),
            FaceEmbedding.from_raw(fid("b"), SPACE, [right[i] for i in order]),
        )
        self.assertEqual(straight, permuted)

    def test_two_insightface_schemes_are_not_interchangeable_either(self) -> None:
        # SURVIVOR emb09: comparing a 10-character prefix survived, because
        # "insightface_5" and "insightface_106" share their first ten
        # characters -- and a 5-point warp fed to a 106-point template is the
        # same undetectable failure as the yunet case the other test covers.
        face = AlignedFace(face_id=fid("a"), landmark_scheme="insightface_106")
        with self.assertRaises(AlignmentMismatch):
            check_alignment(_Embedder(), face)


class DistanceTests(unittest.TestCase):
    def test_identical_vectors_are_exactly_zero_apart(self) -> None:
        a = FaceEmbedding(fid("a"), SPACE, axis_vector(0))
        b = FaceEmbedding(fid("b"), SPACE, axis_vector(0))
        self.assertEqual(cosine_distance(a, b), 0.0)
        self.assertEqual(cosine_distance(a, a), 0.0)

    def test_similarity_never_escapes_minus_one_to_one(self) -> None:
        # A vector against itself is the case that overshoots 1.0 in floating
        # point; the clamp is what stops a negative distance downstream.
        messy = unit([1.0 / 3.0] * DIMENSIONS)
        a = FaceEmbedding(fid("a"), SPACE, messy)
        b = FaceEmbedding(fid("b"), SPACE, messy)
        self.assertLessEqual(cosine_similarity(a, b), 1.0)
        self.assertGreaterEqual(cosine_distance(a, b), 0.0)

    def test_opposite_vectors_are_two_apart(self) -> None:
        a = FaceEmbedding(fid("a"), SPACE, axis_vector(0))
        b = FaceEmbedding(fid("b"), SPACE, axis_vector(0, scale=-1.0))
        self.assertAlmostEqual(cosine_distance(a, b), 2.0, places=12)

    def test_orthogonal_vectors_are_one_apart(self) -> None:
        a = FaceEmbedding(fid("a"), SPACE, axis_vector(0))
        b = FaceEmbedding(fid("b"), SPACE, axis_vector(1))
        self.assertAlmostEqual(cosine_distance(a, b), 1.0, places=12)

    def test_cross_space_comparison_raises(self) -> None:
        a = FaceEmbedding(fid("a"), "arcface_buffalo_l_512", axis_vector(0))
        b = FaceEmbedding(fid("b"), "adaface_ir101_512", axis_vector(0))
        with self.assertRaises(SpaceMismatch):
            cosine_similarity(a, b)

    def test_distance_does_not_depend_on_component_order(self) -> None:
        # fsum is exactly rounded, so a permuted-but-equivalent computation
        # gives the identical float. Without it a face on the threshold can flip
        # between two runs that batched differently.
        forward = unit([1.0 / (i + 1) for i in range(DIMENSIONS)])
        a = FaceEmbedding(fid("a"), SPACE, forward)
        b = FaceEmbedding(fid("b"), SPACE, list(reversed(forward)))
        self.assertEqual(cosine_distance(a, b), cosine_distance(b, a))


class CentroidTests(unittest.TestCase):
    def test_centroid_of_one_is_itself(self) -> None:
        a = FaceEmbedding(fid("a"), SPACE, axis_vector(0))
        self.assertEqual(centroid([a]).values, a.values)

    def test_centroid_is_a_unit_vector(self) -> None:
        members = [
            FaceEmbedding(fid(f"a{i}"), SPACE, axis_vector(i)) for i in range(4)
        ]
        centre = centroid(members)
        self.assertAlmostEqual(
            math.sqrt(math.fsum(v * v for v in centre.values)), 1.0, places=12
        )

    def test_centroid_sits_between_its_members(self) -> None:
        a = FaceEmbedding(fid("a"), SPACE, axis_vector(0))
        b = FaceEmbedding(fid("b"), SPACE, axis_vector(1))
        centre = centroid([a, b])
        self.assertAlmostEqual(
            cosine_distance(a, centre), cosine_distance(b, centre), places=12
        )
        self.assertLess(cosine_distance(a, centre), cosine_distance(a, b))

    def test_centroid_is_order_independent(self) -> None:
        members = [
            FaceEmbedding(fid(f"a{i}"), SPACE, axis_vector(i)) for i in range(5)
        ]
        self.assertEqual(
            centroid(members).values, centroid(list(reversed(members))).values
        )

    def test_empty_centroid_raises(self) -> None:
        with self.assertRaises(EmbeddingError):
            centroid([])

    def test_mixed_space_centroid_raises(self) -> None:
        a = FaceEmbedding(fid("a"), "arcface_buffalo_l_512", axis_vector(0))
        b = FaceEmbedding(fid("b"), "adaface_ir101_512", axis_vector(1))
        with self.assertRaises(SpaceMismatch):
            centroid([a, b])

    def test_cancelling_vectors_raise_rather_than_producing_a_centre(self) -> None:
        a = FaceEmbedding(fid("a"), SPACE, axis_vector(0))
        b = FaceEmbedding(fid("b"), SPACE, axis_vector(0, scale=-1.0))
        with self.assertRaises(EmbeddingError):
            centroid([a, b])


class AlignmentTests(unittest.TestCase):
    def test_matching_scheme_passes(self) -> None:
        face = AlignedFace(face_id=fid("a"), landmark_scheme="insightface_5")
        check_alignment(_Embedder(), face)

    def test_five_point_schemes_are_not_interchangeable(self) -> None:
        # The schema calls this "the worst failure mode in this system because
        # nothing downstream can detect it". Both schemes have five points, so
        # nothing about the shape of the data catches it.
        face = AlignedFace(face_id=fid("a"), landmark_scheme="yunet_5")
        with self.assertRaises(AlignmentMismatch):
            check_alignment(_Embedder(), face)

    def test_unknown_scheme_is_refused_at_construction(self) -> None:
        with self.assertRaises(EmbeddingError):
            AlignedFace(face_id=fid("a"), landmark_scheme="insightface_7")

    def test_aligned_face_requires_a_digest_id(self) -> None:
        with self.assertRaises(EmbeddingError):
            AlignedFace(face_id="face-1", landmark_scheme="insightface_5")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
