"""The sensitive-content gate (issue #21).

    Absence is `indeterminate`, and indeterminate blocks.

Everything in this package is that sentence made mechanical. The classifier is a
1152->3 linear head over the SigLIP 2 `so400m-384` embedding, chosen in
`docs/safety-classifier-decision.md` after thirteen candidates were rejected on
training-data provenance rather than on licence. The head does not exist yet:
SigLIP 2 has no ONNX export in this registry (issue #79), so there is nothing to
fit it over.

What that means in practice, today, when you import this:

    every print export, every share, and every contact sheet sent to a frontier
    model is BLOCKED, with `load_gate_denied` as the recorded reason.

That is the gate working. The failure mode it exists to prevent is the opposite
one -- a check that silently no-ops when its model is missing, so everything
downstream reads the absence as a pass.

Nothing here fabricates an embedding, and there is no flag that makes it.
"""

from memory_engine_safety.classes import CLASS_ORDER, ClassOrderMismatch
from memory_engine_safety.classify import (
    DEFAULT_THRESHOLD,
    Candidate,
    Classification,
    SafetyClassifier,
    Thresholds,
)
from memory_engine_safety.embedding import (
    AbsentEmbedder,
    EmbedderUnavailable,
    ImageEmbedder,
)
from memory_engine_safety.gate import (
    guard_frontier_egress,
    guard_print,
    guard_share,
)
from memory_engine_safety.manifest import build_manifest, compute_decision
from memory_engine_safety.verify import (
    Clearance,
    PublicationBlocked,
    verify_clearance,
)

__all__ = [
    "CLASS_ORDER",
    "DEFAULT_THRESHOLD",
    "AbsentEmbedder",
    "Candidate",
    "Classification",
    "Clearance",
    "ClassOrderMismatch",
    "EmbedderUnavailable",
    "ImageEmbedder",
    "PublicationBlocked",
    "SafetyClassifier",
    "Thresholds",
    "build_manifest",
    "compute_decision",
    "guard_frontier_egress",
    "guard_print",
    "guard_share",
    "verify_clearance",
]
