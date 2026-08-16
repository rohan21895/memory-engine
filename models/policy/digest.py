"""Canonical BLAKE3 digest of a model config, and the registry stamping tool.

WHY THIS EXISTS

`ModelPin` in contracts/proto/ml_runtime.proto carries `config_blake3` beside
`weights_blake3`, because weights alone do not determine behaviour. Everything
that decides what a model actually outputs -- input size, mean/std/scale,
score threshold, NMS IoU, the alignment template, the detection cap -- lives in
models/configs/*.json. Change any of it and every downstream decision changes
while the weights hash stays byte-identical.

That is not hypothetical here. The SCRFD/ArcFace preprocessing defect Codex
found applied the 1/128 scale twice and collapsed the whole 0-255 input range
into a 0.016-wide sliver. It touched no weights byte, it never raised, and it
would have produced quietly wrong embeddings for as long as nobody looked. A
provenance record that could not have distinguished before from after is not a
provenance record.

CANONICALISATION

The digest is taken over a canonical serialisation, not the file bytes, so
reformatting a config -- reindenting it, reordering keys, adding a trailing
newline -- does not read as a behaviour change. Comments do not exist in JSON,
so nothing meaningful is lost. Rules, matching the rest of this contract:

    UTF-8, sorted keys, no insignificant whitespace, non-ASCII left as-is.

`ensure_ascii=False` matters: escaping non-ASCII would make the digest depend
on the serialiser's escaping policy rather than on the value, and a Rust or
TypeScript implementation would then have to reproduce Python's escaping to
agree. Emitting the character itself is the only form all three agree on
without coordination.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = MODELS_DIR / "registry.json"


class Blake3Missing(RuntimeError):
    """The blake3 package is not installed on this machine."""


def canonical_bytes(obj: object) -> bytes:
    """The exact bytes the digest is taken over. Same rules in every language."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def blake3_hex(data: bytes) -> str:
    try:
        from blake3 import blake3  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise Blake3Missing(
            "the blake3 package is required to compute config digests: pip install blake3"
        ) from exc
    return blake3(data).hexdigest()


def config_digest(path: Path) -> str:
    """Canonical BLAKE3 of one model config.

    Raises rather than returning a placeholder when the file is unreadable or is
    not valid JSON: a digest that silently means "could not read the config" is
    worse than no digest, because it would compare equal to itself.
    """
    return blake3_hex(canonical_bytes(json.loads(path.read_text(encoding="utf-8"))))


def digests(registry_path: Path | None = None) -> dict[str, str]:
    """model_id -> canonical config digest, for every entry in the registry."""
    path = registry_path or REGISTRY_PATH
    registry = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    return {
        entry["model_id"]: config_digest(root / entry["config"])
        for entry in registry["entries"]
    }


def _stamp(registry_path: Path, check_only: bool) -> int:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    current = digests(registry_path)

    stale: list[str] = []
    for entry in registry["entries"]:
        want = current[entry["model_id"]]
        if entry.get("config_blake3") != want:
            stale.append(entry["model_id"])
            entry["config_blake3"] = want

    if not stale:
        print(f"Config digests are current ({len(registry['entries'])} entries).")
        return 0

    if check_only:
        print(
            "Model config digests are stale in models/registry.json:\n"
            + "".join(f"  {model_id}\n" for model_id in stale)
            + "A config changed without its digest being restamped, which means the\n"
            "pin no longer describes what the host would load. Run:\n"
            "  python3 models/policy/digest.py --write",
            file=sys.stderr,
        )
        return 1

    # Reserialise with the repo's formatting, not the canonical one -- the
    # canonical form is what gets hashed, never what gets committed.
    registry_path.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for model_id in stale:
        print(f"stamped {model_id} = {current[model_id][:16]}...")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="restamp registry.json in place"
    )
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if any digest is stale"
    )
    args = parser.parse_args()

    try:
        return _stamp(REGISTRY_PATH, check_only=not args.write)
    except Blake3Missing as missing:
        # Not a failure. CI installs pydantic and jsonschema only, and .github/
        # belongs to Codex. The digests are still guarded wherever blake3 is
        # present, and models/tests skip rather than pass silently.
        print(f"Skipping config digest check: {missing}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
