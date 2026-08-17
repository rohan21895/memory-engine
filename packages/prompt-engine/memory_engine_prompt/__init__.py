"""Prompt engine: contact-sheet composition, Tier 3 prompting, structured-output parsing.

This module deliberately imports NOTHING.

An eager re-export here would make `import memory_engine_prompt.structured` also
execute every other submodule, which creates dependency edges the code does not
actually have: a syntax error or a missing constant in a sibling module then
breaks a test suite that never referenced it. That happened twice. Import the
submodule you need:

    from memory_engine_prompt.structured import Request, parse_reply
"""
