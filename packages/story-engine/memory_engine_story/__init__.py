"""Story engine: moment scoring, reel planner, film planner, EDL generation.

Phase 4 of the build plan. The reel planner is the first piece: selected
moments + a beat grid + a target duration in, one EDL out, and every creative
decision written down in the plan because the renderer is not allowed to make
any of them.

THIS FILE IMPORTS NOTHING, ON PURPOSE.

An eager re-export here is a dependency edge whether or not the code has one.
The album engine learned this the expensive way: `__init__` imported all four
submodules, so the clustering and validator suites started failing on a change
to the ranking engine that neither of them touches. The same trap is set here --
the reel planner will grow siblings (film planner, moment scoring) with quite
different dependencies, and a moment scorer that pulls in an ONNX runtime must
not be dragged into a process that only wanted to validate an EDL.

So import what you need:

    from memory_engine_story import reel
    from memory_engine_story.reel import plan_reel, validate_edl

Names are deliberately not flattened either. `Beat`, `Moment` and `Clip` will
mean subtly different things to the film planner than they do here, and one
flat namespace is how a caller ends up handing a film-planner Moment to a reel
planner that wanted the other one.
"""

__all__: list[str] = []
