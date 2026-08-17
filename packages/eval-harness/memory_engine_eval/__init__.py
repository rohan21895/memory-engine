"""Eval harness: benchmark libraries, blind A/B tooling, regression gates.

Import submodules directly (`from memory_engine_eval.harness import evaluate`).

This file deliberately re-exports NOTHING. An eager re-export here would make
importing any one module in this package import all of them, which creates
dependency edges the code itself does not have -- and turns a syntax error or a
missing optional dependency in an unrelated module into an import failure for
every consumer of the package.
"""
