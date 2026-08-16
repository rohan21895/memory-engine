"""Production model-registry policy.

Previously this logic lived only inside a test module, which Codex correctly
refused to import from: production code cannot depend on a test, and
reimplementing it would have meant two copies of one rule drifting apart.
"""

from .load_gate import (
    Candidate,
    load_policy,
    UnloadableReason,
    decide_load,
    resolve_mode,
)

__all__ = ["Candidate", "UnloadableReason", "decide_load", "load_policy", "resolve_mode"]
