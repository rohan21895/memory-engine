#!/usr/bin/env python3
"""Generate Python stubs from ml_runtime.proto, deterministically.

Determinism matters for the same reason it does in contracts/codegen: CI
regenerates and fails if the committed output differs, so a proto edit that
skips codegen cannot reach main.

Two things work against determinism with protoc and are handled here:

* The generated header embeds the protoc/grpcio-tools version. That is useful
  provenance but means an unpinned toolchain produces a spurious diff on every
  machine. `REQUIRED_GRPCIO_TOOLS` pins it, and the generator refuses to run
  under a different version rather than producing output that will fail
  freshness in CI.

* protoc bakes the input path into the descriptor. The compiler is therefore
  always invoked from this directory with a bare filename, so absolute paths
  from a developer's machine never enter the committed output.

Usage:
    python3 generate.py            # write the stubs
    python3 generate.py --check    # exit 1 if the committed stubs are stale
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Every .proto in this directory. Listed rather than globbed so adding one is a
# deliberate act that shows up in a diff -- a proto that appears in generated
# output without appearing here would be a contract nobody agreed to.
PROTOS = ("ml_runtime.proto", "media_query.proto")
OUT_DIR = HERE / "generated" / "python"

# Pinned so the version string baked into the stubs is reproducible.
REQUIRED_GRPCIO_TOOLS = "1.68.1"

BANNER = """\
# GENERATED FILE -- DO NOT EDIT.
#
# Produced by contracts/proto/generate.py from contracts/proto/ml_runtime.proto
# with grpcio-tools=={version}. Edit the .proto and re-run `npm run codegen`.
# CI fails if these files drift from the proto.
"""


def _installed_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("grpcio-tools")
    except Exception:  # pragma: no cover - metadata missing
        return None


def _normalise(text: str, filename: str) -> str:
    """Strip anything that varies between machines but not between protos."""
    # Absolute paths, should any survive the cwd-relative invocation.
    text = text.replace(str(HERE), ".")
    # Trailing whitespace differences across protoc patch releases.
    text = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
    version = _installed_version() or "unknown"
    return BANNER.format(version=version) + "\n" + text


class ToolchainMissing(RuntimeError):
    """The pinned grpcio-tools is not installed on this machine."""


def _generate() -> dict[Path, str]:
    installed = _installed_version()
    if installed is None:
        raise ToolchainMissing(
            "grpcio-tools is not installed. "
            f"Install the pinned version:  pip install grpcio-tools=={REQUIRED_GRPCIO_TOOLS}"
        )
    if installed != REQUIRED_GRPCIO_TOOLS:
        raise ToolchainMissing(
            f"grpcio-tools {installed} is installed but this generator is pinned to "
            f"{REQUIRED_GRPCIO_TOOLS}. An unpinned toolchain bakes a different version "
            "string into the stubs and fails the freshness check on someone else's "
            f"machine. Install:  pip install grpcio-tools=={REQUIRED_GRPCIO_TOOLS}"
        )

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                "--proto_path=.",
                f"--python_out={tmp}",
                f"--pyi_out={tmp}",
                f"--grpc_python_out={tmp}",
                *PROTOS,
            ],
            cwd=HERE,  # bare filename, so no absolute path enters the descriptor
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"protoc failed:\n{result.stdout}\n{result.stderr}")

        files: dict[Path, str] = {}
        for produced in sorted(Path(tmp).rglob("*")):
            if not produced.is_file():
                continue
            text = produced.read_text(encoding="utf-8")
            # The grpc stub imports the pb2 module as a top-level name; rewrite
            # it to a package-relative import so the generated package is
            # importable without putting its directory on sys.path.
            text = re.sub(
                r"^import (\w+)_pb2 as (\w+)__pb2$",
                r"from . import \1_pb2 as \2__pb2",
                text,
                flags=re.MULTILINE,
            )
            files[OUT_DIR / produced.name] = _normalise(text, produced.name)

        files[OUT_DIR / "__init__.py"] = (
            '"""Generated gRPC stubs for the ml-runtime contract.\n\n'
            "Do not edit. Regenerate with `npm run codegen`.\n"
            '"""\n'
        )
        return files


def _proto_digest() -> str:
    """One digest over ALL protos, in a fixed order.

    A per-file digest would let a second proto be edited without the freshness
    check noticing, which is the same class of hole as a test suite that skips
    and exits 0.
    """
    import hashlib

    digest = hashlib.sha256()
    for name in PROTOS:
        digest.update(name.encode("utf-8"))
        digest.update((HERE / name).read_bytes())
    return digest.hexdigest()


STAMP = OUT_DIR / "PROTO_DIGEST"


def _check_without_toolchain() -> int:
    """Freshness gate for machines with no protobuf toolchain.

    CI installs only pydantic and jsonschema, and .github/ belongs to Codex, so
    the gate cannot assume grpcio-tools. Regenerating is impossible there -- but
    detecting that someone edited the proto and forgot to regenerate is not: the
    generator stamps the proto's digest beside the stubs, and a mismatch means
    the committed stubs no longer describe the committed proto.

    Weaker than a full regeneration diff (it cannot catch a hand-edited stub)
    but it catches the failure that actually happens, with zero dependencies.
    """
    if not STAMP.exists():
        print(
            "Generated proto stubs are missing their digest stamp; regenerate them.",
            file=sys.stderr,
        )
        return 1
    recorded = STAMP.read_text(encoding="utf-8").strip()
    current = _proto_digest()
    if recorded != current:
        print(
            "ml_runtime.proto has changed but its generated stubs have not.\n"
            f"  stubs were generated from: {recorded[:16]}\n"
            f"  proto is now:              {current[:16]}\n"
            f"Run: pip install grpcio-tools=={REQUIRED_GRPCIO_TOOLS} && "
            "python3 contracts/proto/generate.py",
            file=sys.stderr,
        )
        return 1
    print("Proto stubs match the committed proto (digest check; no toolchain present).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check and _installed_version() != REQUIRED_GRPCIO_TOOLS:
        return _check_without_toolchain()

    try:
        files = _generate()
    except ToolchainMissing as missing:
        # Not an error. `npm run codegen` runs on machines and CI jobs that have
        # no protobuf toolchain, and failing there would make the proto contract
        # a hard dependency of unrelated schema work. The committed stubs are
        # still guarded: the digest stamp is checked by --check and by
        # contracts/tests/test_ml_runtime_proto.py, both dependency-free.
        print(f"Skipping proto codegen: {missing}")
        return _check_without_toolchain()

    files[STAMP] = _proto_digest() + "\n"

    if args.check:
        stale = [
            path
            for path, content in sorted(files.items())
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("Generated proto stubs are stale:", file=sys.stderr)
            for path in stale:
                print(f"  {path.relative_to(HERE.parent.parent)}", file=sys.stderr)
            return 1
        print(f"Generated proto stubs are fresh ({len(files)} files).")
        return 0

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    for path, content in sorted(files.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(HERE.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
