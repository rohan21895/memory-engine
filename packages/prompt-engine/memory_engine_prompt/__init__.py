"""Prompt engine: frontier-model prompting, contact-sheet composition, and
structured-output parsing.

This file intentionally imports NOTHING and re-exports NOTHING.

Eager re-exports (`from .structured import ...` here) create dependency edges
the code does not actually have: importing any one module drags in every other
module in the package, so a syntax error, a missing optional dependency, or a
slow import in a sibling breaks callers that never referenced it. That is not
hypothetical -- it took out two test suites in a previous round, where a test
that only needed the parser failed on an unrelated module's import.

Import the module you need:

    from memory_engine_prompt.structured import Request, parse_reply
"""
