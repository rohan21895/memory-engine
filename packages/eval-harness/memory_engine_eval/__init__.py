"""Eval harness: benchmark cases, baseline comparison, regression gate.

DELIBERATELY EMPTY OF IMPORTS.

Every other package here re-exports its submodules from ``__init__``, and that
eager re-export creates dependency edges the code does not actually have:
importing ``memory_engine_eval`` for one dataclass pulls in every module in the
package, so a syntax error or a missing optional dependency in an unrelated
module takes down an unrelated test suite. That happened twice. Import from the
module you want:

    from memory_engine_eval.harness import BenchmarkCase, Policy, evaluate
"""
