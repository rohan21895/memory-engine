"""Ranking engine: quality score fusion, dedupe and primary selection.

Phase 1 half of the build plan. Consumes contract-shaped records, returns
decisions media-db can store directly.
"""

from .dedupe import (
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

__all__ = [
    "Candidate",
    "DuplicateGroup",
    "assignments",
    "band_keys",
    "candidate_pairs",
    "cosine_distance",
    "find_duplicates",
    "hamming_distance",
    "select_primary",
]
