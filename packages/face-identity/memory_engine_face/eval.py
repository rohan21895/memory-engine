"""The benchmark: what this package CAN measure, and what it cannot.

WHAT CANNOT BE MEASURED HERE, STATED FIRST

Face recognition precision -- the number the build plan's quality gate is
written in terms of (">=99% precision at the confidence threshold used for
automated output") -- CANNOT be measured in this repository. It is a property of
ArcFace embeddings on real faces, and there are no ArcFace weights and no real
faces here. Nothing in this module produces that number, `identity.py` refuses
to open the automated path without it, and no file in this package states one.

The eval that WOULD measure it is specified in README.md, "The eval that would
measure precision". It needs a labelled benchmark library, the real embedder,
and a sweep over operating points; it produces a `FittedCalibrator`, and that
object is the only thing that unlocks automated assignment.

WHAT IS MEASURED HERE

The CLUSTERING ALGORITHM, on synthetic vectors whose ground truth this module
generates and therefore knows exactly. That is a real measurement of real code:
break complete linkage, change a tie-break, invert a comparison, and these
numbers move. It is not a measurement of face recognition, and the case ids say
`synthetic_` so that nobody reading a report can mistake one for the other.

TWO METRICS PER CATEGORY, AND WHY BOTH

    pairwise precision: of the pairs we put together, how many belong together.
    pairwise recall:    of the pairs that belong together, how many did we find.

Precision alone is trivially gamed by a clustering that groups nothing: every
one of its zero claims is correct. That is not a hypothetical -- it is the exact
shape a "make it more precise" change takes. So recall is gated alongside it in
every category, and a run that improves precision by collapsing to singletons
fails on recall. In the same spirit, a precision measured over zero predicted
pairs is reported as 0.0 rather than 1.0: a clustering that made no claims has
made no correct ones.

THE SYNTHETIC LIBRARIES

Six, one per declared benchmark category (build plan section 6), differing in
the two things that actually vary between them: how many people are in the
library and how tightly one person's faces sit together. A wedding is many
people with few faces each; a baby album is three people with hundreds. Each
library also contains LOOKALIKE PAIRS -- two people whose centres sit close
enough to be confusable -- because a benchmark of well-separated identities
measures nothing that matters. Siblings are the case that breaks face grouping
in real family libraries, and a suite without them reports 1.000 forever.

These are Gaussian blobs on a sphere. Real ArcFace embeddings are not Gaussian
blobs on a sphere: intra-person variation is structured (pose, age, lighting)
and lookalikes are not symmetric. So these numbers say "the algorithm behaves as
specified on data shaped like this"; they do not say "it will work on your
family". Recorded here rather than discovered later.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .clustering import FaceObservation, cluster_faces
from .embeddings import FaceEmbedding

__all__ = [
    "ALGORITHM_VERSION",
    "BENCHMARK_MERGE_THRESHOLD",
    "LibrarySpec",
    "SYNTHETIC_LIBRARIES",
    "build_gate_document",
    "generate_library",
    "pairwise_scores",
    "run_benchmark",
]

# Bumping this is the visible act of changing clustering behaviour. It appears
# in the gate file's model pins, so a run against a different algorithm version
# is a different model set and the harness refuses the comparison rather than
# subtracting two unrelated numbers (harness.py, decision 2).
ALGORITHM_VERSION = "1.0.0"

# The operating point the benchmark is measured at. Declared here rather than
# defaulted in clustering.py, because a distance threshold that has not been
# measured on real embeddings should not look like a recommendation.
BENCHMARK_MERGE_THRESHOLD = 0.50

DIMENSIONS = 512
SPACE = "arcface_buffalo_l_512"


@dataclass(frozen=True, slots=True)
class LibrarySpec:
    """One synthetic library, deterministic in `seed`."""

    category: str
    people: int
    faces_per_person: int
    jitter: float
    lookalike_pairs: int
    seed: int

    def __post_init__(self) -> None:
        if self.people < 2 or self.faces_per_person < 2:
            raise ValueError(f"{self.category}: a library needs at least 2x2 faces")
        if not 0.0 < self.jitter < 1.0:
            raise ValueError(f"{self.category}: jitter must be in (0,1)")
        if self.lookalike_pairs * 2 > self.people:
            raise ValueError(
                f"{self.category}: {self.lookalike_pairs} lookalike pairs needs "
                f"{self.lookalike_pairs * 2} people, library has {self.people}"
            )


SYNTHETIC_LIBRARIES: tuple[LibrarySpec, ...] = (
    # Few people, very many faces each, tight variation: the baby album.
    LibrarySpec("baby_family", people=4, faces_per_person=25, jitter=0.020,
                lookalike_pairs=1, seed=1001),
    # Many people, few faces each: the wedding. The hardest shape for a gallery,
    # because almost nobody has enough faces to enrol from.
    LibrarySpec("indian_weddings", people=20, faces_per_person=5, jitter=0.022,
                lookalike_pairs=4, seed=1002),
    # Crowds, mixed light, moderate counts.
    LibrarySpec("festivals", people=12, faces_per_person=8, jitter=0.030,
                lookalike_pairs=3, seed=1003),
    # Helmets, motion blur, extreme angles: wide intra-person variation.
    LibrarySpec("gopro_adventure", people=6, faces_per_person=12, jitter=0.035,
                lookalike_pairs=1, seed=1004),
    # Tiny faces from altitude: the widest variation in the suite, and the
    # category where over-splitting is expected and acceptable.
    LibrarySpec("drone", people=5, faces_per_person=8, jitter=0.040,
                lookalike_pairs=1, seed=1005),
    LibrarySpec("travel", people=9, faces_per_person=10, jitter=0.025,
                lookalike_pairs=2, seed=1006),
)


def generate_library(
    spec: LibrarySpec,
) -> tuple[tuple[FaceObservation, ...], dict[str, int]]:
    """Build one synthetic library and its ground truth.

    Returns the observations and a face_id -> person index map. The face ids are
    derived from (category, person, index) so they are stable across runs and a
    failing case can be traced back to the exact synthetic face.
    """
    rng = random.Random(spec.seed)
    centres: list[list[float]] = []
    for person in range(spec.people):
        if person < spec.lookalike_pairs * 2 and person % 2 == 1:
            # The odd member of a lookalike pair sits close to the even one.
            # `0.35` is roughly where sibling embeddings land: comfortably
            # closer than strangers, comfortably further than two photos of the
            # same person in the same hour.
            centres.append(_perturb(centres[person - 1], 0.35, rng))
        else:
            centres.append(_unit(rng))

    observations: list[FaceObservation] = []
    truth: dict[str, int] = {}
    for person, centre in enumerate(centres):
        for index in range(spec.faces_per_person):
            face_id = _face_id(spec.category, person, index)
            values = [c + rng.gauss(0.0, spec.jitter) for c in centre]
            observations.append(
                FaceObservation(
                    face_id=face_id,
                    embedding=FaceEmbedding.from_raw(face_id, SPACE, values),
                    quality=0.9,
                )
            )
            truth[face_id] = person
    observations.sort(key=lambda o: o.face_id)
    return tuple(observations), truth


def _unit(rng: random.Random) -> list[float]:
    values = [rng.gauss(0.0, 1.0) for _ in range(DIMENSIONS)]
    norm = math.sqrt(math.fsum(v * v for v in values))
    return [v / norm for v in values]


def _perturb(
    centre: Sequence[float], target_distance: float, rng: random.Random
) -> list[float]:
    """A unit vector at approximately `target_distance` cosine from `centre`."""
    direction = _unit(rng)
    dot = math.fsum(a * b for a, b in zip(centre, direction, strict=True))
    orthogonal = [d - dot * c for d, c in zip(direction, centre, strict=True)]
    norm = math.sqrt(math.fsum(v * v for v in orthogonal))
    orthogonal = [v / norm for v in orthogonal]
    cos = 1.0 - target_distance
    sin = math.sqrt(max(0.0, 1.0 - cos * cos))
    return [cos * c + sin * o for c, o in zip(centre, orthogonal, strict=True)]


def _face_id(category: str, person: int, index: int) -> str:
    import hashlib  # noqa: PLC0415 - only needed to name synthetic faces

    return hashlib.blake2b(
        f"synthetic:{category}:{person}:{index}".encode(), digest_size=32
    ).hexdigest()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def pairwise_scores(
    assignment: Mapping[str, str], truth: Mapping[str, int]
) -> tuple[float, float]:
    """(precision, recall) over same-group face pairs.

    `assignment` maps face_id to whatever group id the algorithm produced. Any
    face missing from it is treated as its own group, which is the honest
    reading of "the clusterer did not place it".
    """
    faces = sorted(truth)
    true_positive = 0
    predicted_positive = 0
    actual_positive = 0
    for i, a in enumerate(faces):
        for b in faces[i + 1 :]:
            same_predicted = (
                a in assignment and b in assignment and assignment[a] == assignment[b]
            )
            same_true = truth[a] == truth[b]
            if same_predicted:
                predicted_positive += 1
            if same_true:
                actual_positive += 1
            if same_predicted and same_true:
                true_positive += 1
    # A clustering that grouped nothing made no correct claims. Reporting 1.0
    # for the empty case would make "put everything in its own cluster" the
    # highest-precision algorithm available, which is exactly the change this
    # gate exists to catch.
    precision = true_positive / predicted_positive if predicted_positive else 0.0
    recall = true_positive / actual_positive if actual_positive else 0.0
    return precision, recall


def run_benchmark(
    *,
    merge_threshold: float = BENCHMARK_MERGE_THRESHOLD,
    libraries: Sequence[LibrarySpec] = SYNTHETIC_LIBRARIES,
) -> dict[str, float]:
    """Every case in the suite, measured. Deterministic: same in, same out."""
    scores: dict[str, float] = {}
    for spec in libraries:
        observations, truth = generate_library(spec)
        result = cluster_faces(
            observations,
            merge_threshold=merge_threshold,
            run_id=f"bench-{spec.category.replace('_', '-')}",
        )
        grouping = {
            face_id: membership.cluster_id
            for face_id, membership in result.memberships.items()
        }
        precision, recall = pairwise_scores(grouping, truth)
        scores[f"synthetic_{spec.category}_precision"] = _round(precision)
        scores[f"synthetic_{spec.category}_recall"] = _round(recall)
    return scores


def _round(value: float) -> float:
    # Six decimals matches the harness's own quantisation, so a value written
    # here survives the round trip through the gate file unchanged.
    return round(value, 6)


# ---------------------------------------------------------------------------
# Gate file
# ---------------------------------------------------------------------------


def _digest(payload: str) -> str:
    """BLAKE3 of a string, as the contract's Blake3Hash.

    Imported lazily and NOT substituted with another hash if it is missing. A
    field called `weights_blake3` holding a SHA-256 would validate, compare
    equal to itself, and be a lie in the one place this repository uses to
    decide whether two runs are the same run.
    """
    try:
        from blake3 import blake3  # noqa: PLC0415
    except ImportError as missing:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "blake3 is required to regenerate the gate file; the committed gate "
            "file can be read and run without it"
        ) from missing

    return blake3(payload.encode("utf-8")).hexdigest()


def _model_pins(merge_threshold: float) -> list[dict[str, str]]:
    """Pins for the thing actually under test: this algorithm, at this config.

    There are no weights, so `weights_blake3` digests the algorithm identity and
    `config_blake3` digests the operating point. That is what those fields mean
    -- "the bytes that determined this behaviour" -- and it makes the harness
    refuse to compare a run at threshold 0.55 against one at 0.60, which is a
    comparison somebody would otherwise make and report as a regression.
    """
    return [
        {
            "model_id": "face-clustering-agglomerative",
            "version": ALGORITHM_VERSION,
            "weights_blake3": _digest(
                f"face-clustering-agglomerative:{ALGORITHM_VERSION}"
            ),
            "config_blake3": _digest(
                f"complete_linkage:cosine:merge_threshold={merge_threshold!r}"
            ),
        }
    ]


def build_gate_document(
    *,
    as_of: str,
    merge_threshold: float = BENCHMARK_MERGE_THRESHOLD,
    floors: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Assemble a gate file from a fresh measurement.

    Baseline and candidate are the same measurement, which makes every delta
    exactly zero. The teeth are elsewhere and are worth naming, because a gate
    whose deltas are always zero looks like a gate that cannot fail:

    * `expected` per case, with `enforce_expected`, is an absolute floor. A
      change that drops a category below its floor fails the gate on its own,
      with no baseline movement involved.
    * tests/test_eval.py re-runs this benchmark and asserts the numbers match
      the committed file exactly. That is what catches a change that moves the
      measurement, since the committed file cannot notice its own staleness.
    """
    scores = run_benchmark(merge_threshold=merge_threshold)
    pins = _model_pins(merge_threshold)
    declared_floors = dict(floors or {})

    cases = []
    results = []
    for case_id in sorted(scores):
        category = _category_of(case_id)
        inputs_digest = _digest(f"synthetic-library:{category}:{ALGORITHM_VERSION}")
        cases.append(
            {
                "case_id": case_id,
                "category": category,
                "inputs_digest": inputs_digest,
                "baseline_models": pins,
                "metric": {
                    "name": case_id.rsplit("_", 1)[-1],
                    "direction": "higher_is_better",
                },
                "expected": declared_floors.get(case_id, _floor_for(scores[case_id])),
                "description": (
                    "synthetic clustering measurement; NOT face recognition precision"
                ),
            }
        )
        results.append(
            {
                "case_id": case_id,
                "models": pins,
                "inputs_digest": inputs_digest,
                # Three identical repeats, because the algorithm is deterministic
                # and `min_repeats: 3` is how that claim is checked rather than
                # asserted.
                "samples": [scores[case_id]] * 3,
            }
        )

    return {
        "as_of": as_of,
        "description": (
            "Synthetic face-clustering benchmark. Measures packages/face-identity's "
            "clustering algorithm on generated vectors with known ground truth. It "
            "does NOT measure face recognition precision -- see "
            "packages/face-identity/README.md."
        ),
        "policy": {
            "min_cases_per_category": 2,
            "min_repeats": 3,
            "enforce_expected": True,
        },
        "suite": {"cases": cases},
        "baseline": results,
        "candidate": results,
    }


def _category_of(case_id: str) -> str:
    return case_id[len("synthetic_") :].rsplit("_", 1)[0]


def _floor_for(measured: float) -> float:
    """A floor a little below the measurement, rounded down to two decimals.

    Not equal to the measurement: a floor set exactly at today's number fails on
    the first legitimate improvement's rounding, and a gate that fails for no
    reason is a gate somebody switches off.
    """
    return max(0.0, math.floor(measured * 100.0 - 2.0) / 100.0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="ISO date for the gate file")
    parser.add_argument(
        "--write", type=Path, default=None, help="write the gate file here"
    )
    parser.add_argument(
        "--merge-threshold", type=float, default=BENCHMARK_MERGE_THRESHOLD
    )
    args = parser.parse_args(argv)
    document = build_gate_document(
        as_of=args.as_of, merge_threshold=args.merge_threshold
    )
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.write is None:
        sys.stdout.write(rendered)
    else:
        args.write.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
