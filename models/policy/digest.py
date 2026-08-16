"""BLAKE3 digest of a model config file, and the registry stamping tool.

THE ALGORITHM, STATED FIRST BECAUSE OTHER LANGUAGES MUST REPRODUCE IT

    config_blake3 = BLAKE3(the config file's raw bytes, exactly as committed)

No parsing, no re-serialisation, no key sorting, no normalisation of any kind.
Read the file, hash the bytes. That is the entire specification, and it is
deliberately something Rust and TypeScript can implement in two lines without
matching any of Python's opinions about JSON.

The files themselves are held in a canonical format (2-space indent, UTF-8, no
BOM, LF line endings, single trailing newline) which `--write` produces and
`--check` enforces. That format is a REPOSITORY convention, not part of the
digest algorithm -- a host verifying a digest never needs to know it.

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

WHY NOT "SORTED KEYS AND COMPACT SEPARATORS"

That was the first design and it was wrong. Codex rejected it in review, and the
counter-example is already in our own configs: Python serialises the float
`1.0` as `1.0`, `1e-07` as `1e-07` and `-0.0` as `-0.0`; JavaScript serialises
the same values as `1`, `1e-7` and `0`. Seven of eight model configs contain a
`1.0` today. So a digest computed in the Rust host or the TypeScript desktop
shell would simply not match the one Python stamped -- and it would not fail
loudly, it would fail as CONFIG_MISMATCH on every single model, which reads as
"someone edited the configs" rather than "the digest is not portable".

Number formatting is only the first of the differences. Key ordering for
integer-like and non-BMP keys diverges too, and every language's JSON writer
has its own opinions. Getting agreement would mean implementing a named
canonical form (RFC 8785 JCS) three times and keeping the three in step -- one
more thing to drift, in service of a property nobody asked for.

SO: THE DIGEST IS OVER THE FILE BYTES

Hashing bytes is something every language does identically, with no
serialisation semantics involved at all. The cost is that reformatting a config
now changes its digest -- which is why `--check` also enforces the canonical
on-disk FORMAT (2-space indent, trailing newline, UTF-8). Formatting is
mechanical and CI-enforceable; cross-language float serialisation is not. The
property we actually need is "the host and the registry agree about the exact
config that ran", and bytes give that unconditionally.
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


class ConfigNotCanonical(ValueError):
    """The file on disk is not in the canonical format the digest assumes."""


def canonical_text(obj: object) -> str:
    """The canonical on-disk FORM of a config.

    Not a serialisation contract for other languages -- nothing outside this
    file needs to reproduce it. It exists so that "reformatted" is a state CI
    can detect and a developer can fix with one command, which is what makes
    byte-hashing safe to rely on.
    """
    return json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def canonical_bytes(obj: object) -> bytes:
    return canonical_text(obj).encode("utf-8")


def blake3_hex(data: bytes) -> str:
    try:
        from blake3 import blake3  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise Blake3Missing(
            "the blake3 package is required to compute config digests: pip install blake3"
        ) from exc
    return blake3(data).hexdigest()


def is_canonical(path: Path) -> bool:
    """Whether the file on disk is byte-identical to its canonical form."""
    raw = path.read_bytes()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return raw == canonical_bytes(parsed)


def config_digest(path: Path, *, require_canonical: bool = True) -> str:
    """BLAKE3 of the config file's BYTES.

    Raises rather than returning a placeholder when the file is unreadable or
    not valid JSON: a digest that silently means "could not read the config" is
    worse than no digest, because it would compare equal to itself.

    Refuses a non-canonically-formatted file by default. Hashing bytes means
    formatting is part of the identity, so an unformatted file would produce a
    digest that changes the next time anyone's editor touches it -- and a digest
    that churns gets restamped reflexively until nobody reads the diff, which is
    how a real change gets waved through.
    """
    raw = path.read_bytes()
    json.loads(raw.decode("utf-8"))  # parse to reject a malformed file loudly
    if require_canonical and not is_canonical(path):
        raise ConfigNotCanonical(
            f"{path.name} is not canonically formatted; run "
            "python3 models/policy/digest.py --write"
        )
    return blake3_hex(raw)


def digests(registry_path: Path | None = None) -> dict[str, str]:
    """model_id -> canonical config digest, for every entry in the registry."""
    path = registry_path or REGISTRY_PATH
    registry = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    return {
        entry["model_id"]: config_digest(root / entry["config"])
        for entry in registry["entries"]
    }


def _reformat(registry_path: Path) -> list[str]:
    """Rewrite any non-canonically-formatted config, returning what changed."""
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    root = registry_path.parent
    rewritten = []
    for entry in registry["entries"]:
        path = root / entry["config"]
        if is_canonical(path):
            continue
        parsed = json.loads(path.read_bytes().decode("utf-8"))
        path.write_bytes(canonical_bytes(parsed))
        rewritten.append(entry["model_id"])
    return rewritten


def _stamp(registry_path: Path, check_only: bool) -> int:
    if not check_only:
        for model_id in _reformat(registry_path):
            print(f"reformatted {model_id}")

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    root = registry_path.parent

    unformatted = [
        entry["model_id"]
        for entry in registry["entries"]
        if not is_canonical(root / entry["config"])
    ]
    if unformatted:
        print(
            "Model configs are not canonically formatted:\n"
            + "".join(f"  {model_id}\n" for model_id in unformatted)
            + "The digest is over the file BYTES, so formatting is part of the\n"
            "config's identity. Run:  python3 models/policy/digest.py --write",
            file=sys.stderr,
        )
        return 1

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

    registry_path.write_bytes(canonical_bytes(registry))
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
        # Codex flagged that this used to return 0 here, which made the
        # advertised gate pass precisely where it was supposed to fail. It now
        # returns 2 -- distinguishable from a real staleness failure (1), but
        # still a failure, because "I could not check" and "I checked and it was
        # fine" must not share an exit code. `blake3` is a declared dependency
        # of this package; a machine without it has an incomplete install.
        print(
            f"Cannot verify config digests: {missing}\n"
            "Install it:  pip install blake3",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
