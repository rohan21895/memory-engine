"""Record the benchmark's inputs from a real ingest run. Never by hand.

`benchmarks/inputs/synthetic-demo-phash.json` is the committed input the dedupe
cases run against. Every number in it comes from somewhere specific, and where
it comes from is the reason the cases mean anything:

    phash_hex    computed by `workers/ingest` -- the Rust `phash::dct_64` that
                 runs in the product -- over the synthetic library, and read
                 back out of the MediaRecords that run wrote. NOT recomputed in
                 Python. A Python reimplementation would resize with a different
                 Lanczos kernel and grey with different luma weights, and the
                 fixture would then pin bits no shipped code produces, while
                 claiming in its own field name to be the pHash ingest derives.

    media_id     the file's BLAKE3, which is what makes the fixture joinable to
                 the MANIFEST and to any later run over the same library.

    bursts       from the generator's own MANIFEST `expectations`, which is the
                 only ground truth that exists here: the library was DRAWN with
                 these frames as one burst, so "did dedupe recover them" is a
                 question with a right answer.

    quality      NOT a measurement. Ingest writes no quality, and the analysis
                 stage that would is not what these cases exercise. Primary
                 selection needs a total order to be deterministic ABOUT, so a
                 fixed pseudo-quality is derived from the media_id. It is
                 arbitrary, it is stated to be arbitrary in the fixture itself,
                 and no case reads it as a score.

Usage:

    python3 -m memory_engine_eval.record_inputs \\
        --records  /path/to/demo-work/records/records \\
        --library  /path/to/demo-library \\
        --out      packages/eval-harness/benchmarks/inputs/synthetic-demo-phash.json

The output is sorted and re-derivable: the same ingest run over the same library
writes the same file, byte for byte.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .library import MANIFEST_NAME

EXPECTED_ALGORITHM = "phash-dct-64"


class RecordingError(Exception):
    """The inputs could not be recorded from what is on disk."""


def _pseudo_quality(media_id: str) -> float:
    """A fixed, arbitrary order over the library. See the module docstring.

    Derived from the media_id so it is stable across machines and re-derivable
    from the fixture itself, and deliberately NOT from any image content: a
    number that looked like it came from the picture would eventually be read as
    if it had.
    """
    return round(int(media_id[:8], 16) / 0xFFFFFFFF, 6)


def collect(records_root: Path, library_root: Path) -> dict[str, Any]:
    manifest_path = library_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise RecordingError(f"{library_root} has no {MANIFEST_NAME}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    by_relpath: dict[str, dict[str, Any]] = {}
    for path in sorted(records_root.rglob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("kind") != "image":
            continue
        image_hash = ((record.get("perceptual") or {}).get("image_hash")) or {}
        digest = image_hash.get("hex")
        if not digest:
            # A record with no pHash cannot participate in a dedupe benchmark.
            # Skipped rather than defaulted: a zeroed hash would sit 0 bits from
            # every other zeroed hash and merge them all.
            continue
        if image_hash.get("algorithm") != EXPECTED_ALGORITHM:
            raise RecordingError(
                f"{path}: pHash algorithm is {image_hash.get('algorithm')!r}, expected "
                f"{EXPECTED_ALGORITHM!r}. The fixture must say which algorithm produced "
                "it, and a mixed set cannot be compared by Hamming distance"
            )
        sources = record.get("sources") or []
        if not sources:
            raise RecordingError(f"{path}: record has no source path")
        try:
            relpath = str(Path(sources[0]["path"]).resolve().relative_to(library_root))
        except ValueError as outside:
            raise RecordingError(
                f"{path}: source {sources[0]['path']} is not inside {library_root}; "
                "these records came from a different library"
            ) from outside
        media_id = str(record["media_id"])
        if relpath in by_relpath and by_relpath[relpath]["media_id"] != media_id:
            raise RecordingError(f"two different media_ids for {relpath}")
        by_relpath[relpath] = {
            "media_id": media_id,
            "relpath": relpath,
            "phash_hex": digest,
            "quality": _pseudo_quality(media_id),
        }
    if not by_relpath:
        raise RecordingError(
            f"no image record with a perceptual hash under {records_root}"
        )

    expectations = manifest.get("expectations") or {}
    bursts: dict[str, list[str]] = {}
    for burst in expectations.get("near_duplicate_bursts") or []:
        members = []
        for relpath in burst["members"]:
            entry = by_relpath.get(relpath)
            if entry is None:
                raise RecordingError(
                    f"burst {burst['burst_id']} names {relpath}, which the ingest run "
                    "produced no record for. A burst with a missing member would score "
                    "as an unrecoverable burst forever"
                )
            members.append(entry["media_id"])
        bursts[str(burst["burst_id"])] = sorted(members)
    if not bursts:
        raise RecordingError(
            f"{manifest_path} declares no near_duplicate_bursts; there is nothing for "
            "the dedupe cases to have a right answer about"
        )

    return {
        "_comment": (
            "Recorded by memory_engine_eval.record_inputs from a real workers/ingest "
            "run over the synthetic library. Do not hand-edit: phash_hex is the Rust "
            "phash::dct_64 output, and a value typed in here would pin bits no shipped "
            "code produces. `quality` is an arbitrary fixed ordering derived from the "
            "media_id, NOT a measurement of anything."
        ),
        "library_id": "synthetic-demo",
        "library_version": str(manifest.get("generator_version")),
        "generator_seed": manifest.get("seed"),
        "algorithm": EXPECTED_ALGORITHM,
        "quality_is_a_measurement": False,
        "items": [by_relpath[relpath] for relpath in sorted(by_relpath)],
        "bursts": dict(sorted(bursts.items())),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m memory_engine_eval.record_inputs",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--records", required=True, help="the ingest run's records dir")
    parser.add_argument("--library", required=True, help="the library root it walked")
    parser.add_argument("--out", required=True, help="fixture path to write")
    args = parser.parse_args(argv)

    try:
        document = collect(
            Path(args.records).resolve(), Path(args.library).resolve()
        )
    except (RecordingError, KeyError, json.JSONDecodeError) as broken:
        print(f"nothing recorded: {broken}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(
        f"recorded {len(document['items'])} item(s) and "
        f"{len(document['bursts'])} burst(s) into {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
