"""Story engine: moment scoring, reel/film planning, EDL generation, beat sync.

This module deliberately imports NOTHING.

Eager re-exports (``from .beats import ...`` here) look convenient and are a
trap: they make importing *any* submodule drag in *every* submodule, so a
syntax error, a missing optional dependency, or a slow module-level constant in
one planner breaks every unrelated test that only wanted another. Two suites
died that way already. Import the submodule you actually need:

    from memory_engine_story.beats import BeatGrid, plan_cuts
"""
