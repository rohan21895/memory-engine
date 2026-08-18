"""Benchmark library identity: what a suite was measured on, pinned hard.

`harness.py` refuses a comparison whose MODEL digests disagree. Nothing until
now refused a comparison whose *inputs* disagreed beyond a single opaque
`inputs_digest` the caller supplied by hand. That is the other half of the same
hole: a baseline measured on 200 photographs and a candidate measured on 40 of
them is not a delta, and if the person editing the gate file also types the
`inputs_digest`, the check is a check on their typing.

This module makes the benchmark library a first-class, verifiable object.

WHAT A LIBRARY DECLARATION PINS, AND WHY EACH FIELD IS THERE

    library_id / library_version   identity, so two libraries cannot collide
    provenance                     synthetic_generated | consented_real
    claim_ceiling                  the HIGHEST claim class a case over this
                                   library is permitted to make
    generator                      exact command + seed, so it can be rebuilt
    consent                        required, and required NON-NULL, when the
                                   provenance is consented_real
    file_count / inventory_digest  BLAKE3 over the canonical inventory bytes

`claim_ceiling` is the structural containment of the rule every file in
`scripts/demo/` states in prose: *no quality number measured on the synthetic
library may reach a model card, an eval report or a regression gate.* A comment
saying so is obeyed until somebody is in a hurry. A `claim_ceiling` of
`plumbing` on the synthetic library means `benchmarks.py` REFUSES TO LOAD a case
that declares `claim_class: quality` against it -- the suite does not compile,
so the number cannot be produced, so it cannot be quoted.

`consent` is required rather than optional for the same reason in the other
direction. A real benchmark library is a folder of other people's families. A
declaration that cannot name the consent record it rests on is not a benchmark
library, it is a pile of photographs, and the loader says so before anything is
measured. See docs/benchmark-libraries.md.

TWO DIGESTS, BECAUSE THEY ANSWER DIFFERENT QUESTIONS

`inventory_digest` is over file bytes. It answers "is this the same library,
byte for byte" and it is deliberately fragile: a re-encode, a different libjpeg,
a touched file, all change it. That is what makes a stale library refuse.

It is NOT what a case's `inputs_digest` is computed from. A case is compared
across machines, and the same generator on a different platform can emit
different JPEG bytes for the same picture. A case therefore digests the
*derived* inputs it actually scores (see `benchmarks.CaseDeclaration`), which
are encoder-robust where the derivation is, and the encoder-robustness of that
derivation is itself a benchmark case rather than an assumption
(`phash_encoder_robustness`).

So: `inventory_digest` guards the library on THIS machine; `inputs_digest`
guards the comparison ACROSS machines. Collapsing them into one number would
force a choice between refusing every cross-platform comparison and noticing no
library change at all.

VERIFICATION HASHES THE FILES. IT DOES NOT TRUST THE MANIFEST.

`MANIFEST.json` records a BLAKE3 per file. `resolve()` recomputes every one of
them off the disk. A manifest agreeing with itself proves nothing -- the failure
being guarded against is a library whose files were edited, regenerated at a
different version, or partially copied, all of which leave the manifest intact
and every downstream measurement wrong with no exception raised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

try:
    from blake3 import blake3
except ImportError as error:  # pragma: no cover - dependency guard
    # Deliberately fatal. Falling back to blake2b would produce a digest that
    # claims to be a content address, is not, and compares unequal to every
    # digest the rest of the repo computes -- which would read as a stale
    # library rather than as a missing dependency. The same argument
    # story-engine makes.
    raise ImportError(
        "memory_engine_eval.library requires blake3; content-addressed identity "
        "is load-bearing here and there is no acceptable fallback"
    ) from error

__all__ = [
    "MANIFEST_NAME",
    "ClaimClass",
    "LibraryDeclaration",
    "LibraryError",
    "LibraryMismatch",
    "LibraryStale",
    "LibraryUnavailable",
    "Provenance",
    "ResolvedLibrary",
    "inventory_digest",
    "load_library_declaration",
]

MANIFEST_NAME = "MANIFEST.json"


class ClaimClass(Enum):
    """What kind of statement a number is allowed to be.

    Ordered, and the order is the whole point: a case may claim at or below its
    library's ceiling and never above it.

    PLUMBING     the code path ran and produced the shape it should. Says
                 nothing about whether the answer is good. A face detector
                 scoring 1.0 on cartoon ovals has proved that it was called,
                 that the colour order and the resize convention survived, and
                 that post-processing returned boxes. It has proved nothing
                 about faces.
    DETERMINISM  the same input produced the same output. A property of the
                 code, independent of how good the output is, and therefore
                 measurable on synthetic data without lying.
    QUALITY      the output is good, by a measure a human would accept. Only
                 obtainable on real, consented media with real ground truth.
    """

    PLUMBING = "plumbing"
    DETERMINISM = "determinism"
    QUALITY = "quality"

    @property
    def rank(self) -> int:
        return _CLAIM_RANK[self]

    def permits(self, other: "ClaimClass") -> bool:
        """May a case of class `other` be declared under this ceiling?"""
        return other.rank <= self.rank


# PLUMBING and DETERMINISM are both below QUALITY and neither is below the
# other: "it ran" and "it repeats" are different axes, and a library that
# supports one supports the other. Equal rank makes `permits` a genuine
# ceiling rather than a chain that would accidentally forbid determinism cases
# on a plumbing-ceilinged library.
_CLAIM_RANK: dict[ClaimClass, int] = {
    ClaimClass.PLUMBING: 0,
    ClaimClass.DETERMINISM: 0,
    ClaimClass.QUALITY: 1,
}


class Provenance(Enum):
    """Where the media came from. Decides what consent must be on file."""

    SYNTHETIC_GENERATED = "synthetic_generated"
    CONSENTED_REAL = "consented_real"


# A synthetic library may never carry a QUALITY case, whatever its declaration
# says. Enforced here rather than left to whoever writes the declaration,
# because the declaration is the thing an author in a hurry edits.
_MAX_CEILING: dict[Provenance, ClaimClass] = {
    Provenance.SYNTHETIC_GENERATED: ClaimClass.PLUMBING,
    Provenance.CONSENTED_REAL: ClaimClass.QUALITY,
}


class LibraryError(Exception):
    """Base for everything this module raises."""


class LibraryUnavailable(LibraryError):
    """The library is not on this machine.

    Distinct from LibraryStale on purpose. "I do not have it" and "I have a
    different one" call for different actions, and the runner maps only one of
    them onto a claim that anything was measured.
    """


class LibraryMismatch(LibraryError):
    """The library on disk is a different library from the one declared."""


class LibraryStale(LibraryError):
    """The library on disk claims the declared identity and has drifted."""


def _hex(value: object, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise LibraryError(f"{where} must be a 64-character BLAKE3 hex digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise LibraryError(f"{where} must be lowercase BLAKE3 hex")
    return value


def inventory_digest(entries: Iterable[tuple[str, str, int]]) -> str:
    """BLAKE3 over a canonical byte encoding of (relpath, blake3, byte_size).

    Encoded as length-prefixed UTF-8 fields, NOT as JSON. The repository has
    already been bitten once by a digest taken over a re-serialisation rather
    than over bytes: Python writes `1.0` where JavaScript writes `1`, and every
    model reported a config mismatch. A byte size is an integer here and an
    integer in every reader, because it is written as its decimal digits with
    its own length in front and nothing gets to choose a float representation
    for it.

    Sorted by relpath, so a directory walk in any order digests the same.
    """
    hasher = blake3()
    for relpath, digest, byte_size in sorted(entries):
        if byte_size < 0:
            raise LibraryError(f"{relpath}: negative byte_size")
        for field in (relpath, digest, str(byte_size)):
            raw = field.encode("utf-8")
            # The length prefix is what stops ("ab", "c") and ("a", "bc") from
            # digesting identically -- a relpath containing the separator would
            # otherwise let two different inventories agree.
            hasher.update(len(raw).to_bytes(4, "big"))
            hasher.update(raw)
    return hasher.hexdigest()


def file_digest(path: Path) -> str:
    """BLAKE3 of a file's bytes, streamed.

    This is exactly the `media_id` ingest derives, which is why the manifest's
    per-file digest can be checked against it at all.
    """
    hasher = blake3()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(frozen=True)
class LibraryDeclaration:
    """A committed statement of what a benchmark library is.

    Small on purpose: it holds the digest of the inventory, not the inventory.
    The inventory lives on the machine that has the library, in its MANIFEST,
    and is checked against the disk rather than against this file.
    """

    library_id: str
    library_version: str
    provenance: Provenance
    claim_ceiling: ClaimClass
    description: str
    file_count: int
    inventory_digest: str
    generator: Mapping[str, object] | None
    consent: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if not self.library_id:
            raise LibraryError("library_id must be non-empty")
        if not self.library_version:
            raise LibraryError("library_version must be non-empty")
        if self.file_count < 1:
            raise LibraryError(f"{self.library_id}: file_count must be at least 1")
        _hex(self.inventory_digest, f"{self.library_id}.inventory_digest")

        ceiling = _MAX_CEILING[self.provenance]
        if not ceiling.permits(self.claim_ceiling):
            raise LibraryError(
                f"{self.library_id}: provenance {self.provenance.value} cannot support "
                f"claim_ceiling {self.claim_ceiling.value}; the highest it supports is "
                f"{ceiling.value}. Generated media has no ground truth to be right about"
            )

        if self.provenance is Provenance.SYNTHETIC_GENERATED:
            if self.generator is None:
                raise LibraryError(
                    f"{self.library_id}: a synthetic library must declare the generator "
                    "that produces it, or it cannot be rebuilt and its digest is a "
                    "number nobody can reproduce"
                )
            for field in ("command", "generator_version", "seed"):
                if field not in self.generator:
                    raise LibraryError(
                        f"{self.library_id}: generator is missing {field!r}"
                    )
        if self.provenance is Provenance.CONSENTED_REAL:
            if not self.consent:
                raise LibraryError(
                    f"{self.library_id}: a consented_real library must declare its "
                    "consent record. A benchmark library of real photographs with no "
                    "consent on file is not a benchmark library"
                )
            for field in ("consent_ledger_ref", "subjects_consented", "minors_present"):
                if field not in self.consent:
                    raise LibraryError(
                        f"{self.library_id}: consent is missing {field!r}"
                    )
            if self.consent.get("minors_present") and not self.consent.get(
                "minor_consent_ref"
            ):
                raise LibraryError(
                    f"{self.library_id}: minors are present and no minor_consent_ref is "
                    "declared; child-face labelling sits behind separate explicit "
                    "consent (build plan §8)"
                )

    @property
    def ref(self) -> str:
        return f"{self.library_id}@{self.library_version}"

    def permits(self, claim: ClaimClass) -> bool:
        return self.claim_ceiling.permits(claim)


@dataclass(frozen=True)
class ResolvedLibrary:
    """A declaration that has been checked against a directory on this machine."""

    declaration: LibraryDeclaration
    root: Path
    files: Mapping[str, str]  # relpath -> blake3, as verified off the disk
    manifest: Mapping[str, object]

    def path(self, relpath: str) -> Path:
        if relpath not in self.files:
            raise LibraryStale(
                f"{self.declaration.ref}: {relpath!r} is not in the verified inventory"
            )
        return self.root / relpath


_ALLOWED_TOP = {
    "library_id",
    "library_version",
    "provenance",
    "claim_ceiling",
    "description",
    "file_count",
    "inventory_digest",
    "generator",
    "consent",
}


def load_library_declaration(path: Path | str) -> LibraryDeclaration:
    """Parse a `*.library.json`. Unknown fields are refused.

    The same argument `harness.load_gate_input` makes: a misspelled key takes
    its default silently, so the declaration in the repository would say one
    thing and the declaration that ran would say another.
    """
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LibraryError(f"no library declaration at {path}") from error
    except json.JSONDecodeError as error:
        raise LibraryError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise LibraryError(f"{path} must contain a JSON object")

    unknown = sorted(set(document) - _ALLOWED_TOP)
    if unknown:
        raise LibraryError(f"{path} has unknown field(s): {', '.join(unknown)}")
    missing = sorted(
        {
            "library_id",
            "library_version",
            "provenance",
            "claim_ceiling",
            "description",
            "file_count",
            "inventory_digest",
        }
        - set(document)
    )
    if missing:
        raise LibraryError(f"{path} is missing required field(s): {', '.join(missing)}")

    try:
        provenance = Provenance(document["provenance"])
    except ValueError as error:
        raise LibraryError(
            f"{path}: provenance must be one of "
            + ", ".join(p.value for p in Provenance)
        ) from error
    try:
        ceiling = ClaimClass(document["claim_ceiling"])
    except ValueError as error:
        raise LibraryError(
            f"{path}: claim_ceiling must be one of "
            + ", ".join(c.value for c in ClaimClass)
        ) from error

    file_count = document["file_count"]
    if not isinstance(file_count, int) or isinstance(file_count, bool):
        raise LibraryError(f"{path}: file_count must be an integer")

    return LibraryDeclaration(
        library_id=str(document["library_id"]),
        library_version=str(document["library_version"]),
        provenance=provenance,
        claim_ceiling=ceiling,
        description=str(document["description"]),
        file_count=file_count,
        inventory_digest=str(document["inventory_digest"]),
        generator=document.get("generator"),
        consent=document.get("consent"),
    )


def manifest_inventory(manifest: Mapping[str, object]) -> list[tuple[str, str, int]]:
    """The (relpath, blake3, byte_size) triples a manifest declares.

    Only files the generator says it actually wrote are counted. A manifest may
    list a file it skipped -- `make_library.py` records its skips rather than
    dropping them -- and an entry with no digest is not part of the inventory
    because there is nothing to verify it against.
    """
    files = manifest.get("files")
    if not isinstance(files, list):
        raise LibraryStale("MANIFEST.json has no `files` array")
    inventory: list[tuple[str, str, int]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise LibraryStale("MANIFEST.json `files` contains a non-object entry")
        if entry.get("generated") is False:
            continue
        relpath = entry.get("relpath")
        digest = entry.get("blake3")
        size = entry.get("byte_size")
        if not isinstance(relpath, str):
            raise LibraryStale("MANIFEST.json entry has no relpath")
        if not isinstance(digest, str) or not isinstance(size, int):
            # Not skipped: a file the manifest lists as written but cannot
            # digest is exactly the state a partially-copied library is in.
            raise LibraryStale(
                f"MANIFEST.json entry {relpath!r} has no blake3/byte_size; the "
                "library is incomplete"
            )
        inventory.append((relpath, digest, size))
    if not inventory:
        raise LibraryStale("MANIFEST.json declares no generated files")
    return inventory


def resolve(
    declaration: LibraryDeclaration,
    root: Path | str | None,
    *,
    verify_bytes: bool = True,
) -> ResolvedLibrary:
    """Check a declaration against a directory, or refuse.

    `verify_bytes=False` exists for the one caller that has already hashed every
    file for another reason. It is NOT a fast path for CI: skipping it turns the
    manifest into its own witness, which is the failure this function exists to
    catch.
    """
    if root is None:
        raise LibraryUnavailable(
            f"{declaration.ref}: no library root supplied. Nothing was measured"
        )
    root = Path(root)
    if not root.is_dir():
        raise LibraryUnavailable(f"{declaration.ref}: {root} is not a directory")

    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise LibraryUnavailable(
            f"{declaration.ref}: {root} has no {MANIFEST_NAME}, so it is not a "
            "declared library and nothing about it can be verified"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LibraryStale(f"{manifest_path} is not valid JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise LibraryStale(f"{manifest_path} must contain a JSON object")

    generator = declaration.generator or {}
    if generator:
        for declared_key, manifest_key in (
            ("generator_version", "generator_version"),
            ("seed", "seed"),
        ):
            expected = generator.get(declared_key)
            actual = manifest.get(manifest_key)
            if expected != actual:
                raise LibraryMismatch(
                    f"{declaration.ref}: {manifest_key} is {actual!r} on disk and "
                    f"{expected!r} in the declaration. Bumping the generator changes "
                    "every file's bytes and therefore every media_id"
                )

    inventory = manifest_inventory(manifest)
    if len(inventory) != declaration.file_count:
        raise LibraryStale(
            f"{declaration.ref}: {len(inventory)} generated file(s) on disk, "
            f"{declaration.file_count} declared"
        )
    actual_digest = inventory_digest(inventory)
    if actual_digest != declaration.inventory_digest:
        raise LibraryStale(
            f"{declaration.ref}: inventory digest is {actual_digest} on disk and "
            f"{declaration.inventory_digest} in the declaration. This library is not "
            "the library the baseline was measured on"
        )

    verified: dict[str, str] = {}
    for relpath, digest, byte_size in inventory:
        target = root / relpath
        if not target.is_file():
            raise LibraryStale(f"{declaration.ref}: {relpath!r} is missing from {root}")
        if verify_bytes:
            actual_size = target.stat().st_size
            if actual_size != byte_size:
                raise LibraryStale(
                    f"{declaration.ref}: {relpath!r} is {actual_size} bytes, manifest "
                    f"says {byte_size}"
                )
            actual_file = file_digest(target)
            if actual_file != digest:
                raise LibraryStale(
                    f"{declaration.ref}: {relpath!r} hashes to {actual_file}, manifest "
                    f"says {digest}. The manifest and the media disagree"
                )
        verified[relpath] = digest

    return ResolvedLibrary(
        declaration=declaration, root=root, files=verified, manifest=manifest
    )


def describe_library(root: Path | str) -> dict[str, object]:
    """Compute the fields a `*.library.json` needs from a library on disk.

    Used by `python3 -m memory_engine_eval.runner declare-library`. Emitting the
    digest from a script rather than asking a human to paste one is the point:
    a hand-typed digest is a check on somebody's clipboard.
    """
    root = Path(root)
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    inventory = manifest_inventory(manifest)
    return {
        "file_count": len(inventory),
        "inventory_digest": inventory_digest(inventory),
        "generator": {
            "command": [
                "python3",
                "scripts/demo/make_library.py",
                "--out",
                "<ROOT>",
                "--seed",
                str(manifest.get("seed")),
                "--stills",
                str((manifest.get("counts") or {}).get("stills")),
                "--clips",
                str((manifest.get("counts") or {}).get("clips")),
            ],
            "generator_version": manifest.get("generator_version"),
            "seed": manifest.get("seed"),
        },
    }


def sorted_relpaths(files: Mapping[str, str], suffixes: Sequence[str]) -> list[str]:
    """Relpaths with one of `suffixes`, case-insensitively, in sorted order."""
    lowered = tuple(s.lower() for s in suffixes)
    return sorted(
        relpath for relpath in files if relpath.lower().endswith(lowered)
    )
