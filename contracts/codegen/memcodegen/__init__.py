"""Memory Engine contract code generation.

Turns `contracts/schemas/*.schema.json` into pydantic, TypeScript and Rust
bindings. Deliberately stdlib-only: the contract layer is the one thing both
agents must be able to regenerate on any machine, in CI, offline, without a
dependency resolution step standing between a schema edit and a diff.
"""

from .ir import Contracts, load_contracts

__all__ = ["Contracts", "load_contracts"]
