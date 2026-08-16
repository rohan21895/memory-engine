#!/usr/bin/env python3
"""Regenerate contract bindings for Python, TypeScript and Rust.

Entry point invoked by `npm run codegen` (scripts/ci/generate-contracts.mjs).
Output is byte-stable for a given set of schemas: CI regenerates and fails the
build if anything differs from what is committed, so a schema change that skips
codegen cannot reach main.

Usage:
    python3 generate.py            # write the bindings
    python3 generate.py --check    # exit 1 if the committed bindings are stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from memcodegen import load_contracts  # noqa: E402
from memcodegen import emit_python, emit_rust, emit_typescript  # noqa: E402

SCHEMA_DIR = HERE.parent / "schemas"
OUT_DIR = HERE / "generated"

PYTHON_PACKAGE = OUT_DIR / "python" / "memory_engine_contracts"
TYPESCRIPT_DIR = OUT_DIR / "typescript"
RUST_DIR = OUT_DIR / "rust"

PYTHON_INIT = '''"""Generated pydantic bindings for the Memory Engine contracts.

Do not edit. Regenerate with `npm run codegen`.
"""

from .models import *  # noqa: F401,F403
from .models import ROOT_MODELS, ContractModel  # noqa: F401
'''

RUSTFMT_TOML = """# Generated code is formatted by its generator, not by rustfmt.
#
# CI runs `cargo fmt --check` across every crate it finds. Letting rustfmt also
# have an opinion about this file would make the build fail whenever the two
# disagree about a line break -- a diff no human authored and no human can fix
# at the source. The generator owns the formatting; this turns rustfmt off for
# this crate only.
disable_all_formatting = true
"""

CARGO_TOML = """[package]
name = "memory-engine-contracts"
version = "0.0.0"
edition = "2021"
publish = false
description = "Generated Rust bindings for the Memory Engine contract schemas"

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
"""

TS_PACKAGE_JSON = """{
  "name": "@memory-engine/contracts",
  "version": "0.0.0",
  "private": true,
  "description": "Generated TypeScript bindings for the Memory Engine contract schemas",
  "types": "index.ts",
  "main": "index.ts"
}
"""


def _targets() -> dict[Path, str]:
    contracts = load_contracts(SCHEMA_DIR)
    if not contracts.roots:
        raise SystemExit("no root schemas found -- nothing to generate")

    files: dict[Path, str] = {
        PYTHON_PACKAGE / "__init__.py": PYTHON_INIT,
        PYTHON_PACKAGE / "models.py": emit_python.emit(contracts),
        TYPESCRIPT_DIR / "index.ts": emit_typescript.emit(contracts),
        TYPESCRIPT_DIR / "package.json": TS_PACKAGE_JSON,
        RUST_DIR / "Cargo.toml": CARGO_TOML,
        RUST_DIR / "rustfmt.toml": RUSTFMT_TOML,
    }
    # Rust emission mutates the IR (it hoists inline unions to named enums), so
    # it runs last against a freshly loaded copy.
    files[RUST_DIR / "src" / "lib.rs"] = emit_rust.emit(load_contracts(SCHEMA_DIR))
    return files


def _run_proto_generator(check: bool) -> int:
    """Chain the .proto generator so `npm run codegen` covers both contracts.

    Kept as a subprocess rather than an import because it needs a pinned
    grpcio-tools that this generator deliberately does not depend on -- schema
    codegen must keep working on a bare Python.
    """
    import subprocess

    script = HERE.parent / "proto" / "generate.py"
    if not script.exists():
        return 0
    argv = [sys.executable, str(script)] + (["--check"] if check else [])
    result = subprocess.run(argv, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed bindings match the schemas; write nothing",
    )
    args = parser.parse_args()

    files = _targets()

    if args.check:
        stale = [
            path
            for path, content in sorted(files.items())
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("Generated bindings are stale:", file=sys.stderr)
            for path in stale:
                print(f"  {path.relative_to(HERE.parent.parent)}", file=sys.stderr)
            print("Run `npm run codegen` and commit the result.", file=sys.stderr)
            return 1
        print(f"Generated bindings are fresh ({len(files)} files).")
        return _run_proto_generator(check=True)

    for path, content in sorted(files.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(HERE.parent.parent)}")

    return _run_proto_generator(check=False)


if __name__ == "__main__":
    raise SystemExit(main())
