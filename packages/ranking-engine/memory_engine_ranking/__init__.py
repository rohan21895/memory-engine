"""Ranking engine: quality score fusion, dedupe and primary selection.

Phase 1 half of the build plan. Consumes contract-shaped records, returns
decisions media-db can store directly.
"""

from .dedupe import (
    DEFAULT_HAMMING_DECISIVE_THRESHOLD,
    DEFAULT_HAMMING_THRESHOLD,
    Candidate,
    DuplicateGroup,
    assignments,
    band_keys,
    candidate_pairs,
    cosine_distance,
    find_duplicates,
    hamming_distance,
    select_primary,
)
from .fusion import (
    DEFAULT_MIN_COVERAGE,
    DEFAULT_SHARPNESS_FLOOR,
    FEATURE_SET_ID,
    FusedScore,
    SharpnessFloor,
    Signals,
    UncalibratedFloor,
    Weights,
    eliminate,
    explain,
    fuse,
    rank,
)

__all__ = [
    "Candidate",
    "DEFAULT_HAMMING_DECISIVE_THRESHOLD",
    "DEFAULT_HAMMING_THRESHOLD",
    "DEFAULT_MIN_COVERAGE",
    "DEFAULT_SHARPNESS_FLOOR",
    "DuplicateGroup",
    "FEATURE_SET_ID",
    "FusedScore",
    "SharpnessFloor",
    "Signals",
    "UncalibratedFloor",
    "Weights",
    "assignments",
    "band_keys",
    "candidate_pairs",
    "cosine_distance",
    "eliminate",
    "explain",
    "find_duplicates",
    "fuse",
    "hamming_distance",
    "rank",
    "select_primary",
]
