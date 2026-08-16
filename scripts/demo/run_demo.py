#!/usr/bin/env python3
"""End-to-end demo: a folder of photos in, deduplicated searchable library out.

Wires the pieces that already exist into the first thing a person can actually
watch work:

    folder
      -> workers/ingest        (Codex, Rust)   hash, EXIF, pHash, thumbnails
      -> packages/media-db     (Claude)        store, index, full-text search
      -> packages/ranking-engine (Claude)      near-duplicate grouping
      -> this script                           show what it found

Deliberately needs no models. Dedupe runs on perceptual hashes alone, so there
is no ml-runtime dependency and nothing to download -- which is why a demo is
possible today rather than after the next phase.

Usage:
    python3 scripts/demo/run_demo.py <photo-folder> [--workdir DIR] [--search TERM]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
INGEST_BIN = REPO / "workers/ingest/target/release/memory-engine-ingest"

sys.path.insert(0, str(REPO / "packages/media-db"))
sys.path.insert(0, str(REPO / "packages/ranking-engine"))

from memory_engine_media_db import Database  # noqa: E402
from memory_engine_ranking import Candidate, assignments, find_duplicates  # noqa: E402


def canonical_locator(paths: list[str]) -> str:
    """The digest that keeps two scans of different folders from colliding.

    BLAKE3, matching JobSpec.inputs.source_locator_digest and the Rust ingest
    worker. Getting this wrong is not a silent failure -- ingest recomputes the
    digest and refuses the job -- which is the contract working as intended.
    """
    import blake3

    normalised = sorted(unicodedata.normalize("NFC", p.rstrip("/")) for p in paths)
    return blake3.blake3("\x00".join(normalised).encode()).hexdigest()


def build_job(source: Path, params: dict) -> dict:
    locator = canonical_locator([str(source)])
    import blake3

    params_digest = blake3.blake3(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    job_id = blake3.blake3(
        "\x1f".join(["scan_source", "", locator, params_digest, "demo"]).encode()
    ).hexdigest()
    return {
        "schema_version": "v0",
        "job_id": job_id,
        "job_type": "scan_source",
        "inputs": {
            "media_ids": [], "moment_ids": [], "face_ids": [],
            "edl_id": None, "album_id": None,
            "source_paths": [str(source)],
            "source_locator_digest": locator,
            "parent_job_id": None, "depends_on_job_ids": [], "models": [],
        },
        "params": params,
        "params_digest": params_digest,
        "scope": "demo",
        "priority": 500,
        "egress": {
            "requires_egress": False, "consent": None,
            "destination": None, "payload_kind": None, "estimated_bytes": None,
        },
        "state": {
            "status": "pending", "attempts": 0, "worker_id": None,
            "started_at": None, "heartbeat_at": None, "finished_at": None,
            "progress": None,
        },
        "checkpoint": {
            "resumable": True, "cursor": None, "checkpoint_version": 1,
            "updated_at": None, "completed_input_ids": [], "partial_output_ids": [],
        },
        "outputs": [], "error": None, "journal": {"entries": []},
        "created_at": "2026-08-16T00:00:00+00:00", "deadline": None,
    }


def run_ingest(source: Path, workdir: Path) -> tuple[dict, list[dict]]:
    if not INGEST_BIN.exists():
        raise SystemExit(
            f"ingest binary not built.\n  cd {REPO/'workers/ingest'} && cargo build --release"
        )

    out_dir = workdir / "records"
    out_dir.mkdir(parents=True, exist_ok=True)
    job_path = workdir / "job.json"
    checkpoint = workdir / "checkpoint.json"

    params = {"follow_symlinks": False, "include_hidden": False, "max_depth": 32}
    job_path.write_text(json.dumps(build_job(source, params), indent=2))

    started = time.time()
    result = subprocess.run(
        [str(INGEST_BIN), str(job_path), str(out_dir), str(checkpoint)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ingest failed:\n{result.stdout}\n{result.stderr}")

    report = json.loads(result.stdout.strip().splitlines()[-1])
    records = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(out_dir.rglob("*.json"))
    ]
    report["_elapsed_s"] = round(time.time() - started, 2)
    return report, records


def to_candidate(record: dict) -> Candidate:
    phash = (record.get("perceptual") or {}).get("image_hash") or {}
    quality = record.get("quality") or {}
    aesthetic = quality.get("aesthetic") or {}
    sharpness = quality.get("sharpness") or {}
    return Candidate(
        media_id=record["media_id"],
        phash_hex=phash.get("hex"),
        phash_bits=phash.get("bits", 64),
        embedding=None,  # no models in the demo path
        quality=aesthetic.get("value") or sharpness.get("value") or 0.0,
        captured_utc=record["capture"]["captured_at"].get("utc"),
        favorite=(record.get("user") or {}).get("favorite", False),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/memory-engine-demo"))
    parser.add_argument("--search", default="")
    parser.add_argument("--keep", action="store_true", help="reuse an existing workdir")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"not a directory: {source}")

    workdir = args.workdir.expanduser().resolve()
    if workdir.exists() and not args.keep:
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    rule = "-" * 68
    print(f"\n{rule}\n  MEMORY ENGINE — demo scan\n{rule}")
    print(f"  source   {source}")
    print(f"  workdir  {workdir}\n")

    # 1. INGEST -------------------------------------------------------------
    print("  [1/4] ingest      walking, hashing, EXIF, pHash, thumbnails ...")
    report, records = run_ingest(source, workdir)
    print(f"        {len(records)} records in {report['_elapsed_s']}s")
    for key in ("scanned", "quarantined", "skipped"):
        if key in report:
            print(f"        {key}: {report[key]}")

    # 2. STORE --------------------------------------------------------------
    print("\n  [2/4] media-db    storing, indexing, building search ...")
    db = Database.open(workdir / "library.db")
    for record in records:
        db.put_media(record)
    print(f"        {db.count_media()} in the library, vector backend={db.vectors.backend}")

    # 3. DEDUPE -------------------------------------------------------------
    print("\n  [3/4] dedupe      perceptual-hash grouping ...")
    candidates = [to_candidate(r) for r in records if r.get("perceptual")]
    groups = find_duplicates(candidates)
    for media_id, update in assignments(groups).items():
        record = db.get_media(media_id)
        if record:
            record["dedupe"] = update
            db.put_media(record)
    duplicates = sum(g.size for g in groups)
    print(f"        {len(groups)} duplicate groups covering {duplicates} files")

    # 4. RESULTS ------------------------------------------------------------
    print(f"\n  [4/4] results\n{rule}")

    if groups:
        print("\n  DUPLICATE GROUPS — one primary kept, the rest suppressed\n")
        for group in groups[:6]:
            print(f"    group of {group.size}  ({group.method})")
            for member in group.members:
                record = db.get_media(member)
                name = Path(record["sources"][0]["path"]).name if record else member[:12]
                mark = "KEEP  " if member == group.primary_media_id else "  dup "
                print(f"      {mark} {name}")
            print()
        if len(groups) > 6:
            print(f"    ... and {len(groups) - 6} more groups\n")
    else:
        print("\n  No near-duplicates found.\n")

    dated = db.list_media(chronological=True, limit=5)
    print(f"  CHRONOLOGY — {len(dated)} of {db.count_media()} have a usable date")
    for item in dated[:5]:
        record = db.get_media(item.media_id)
        name = Path(record["sources"][0]["path"]).name
        print(f"    {item.captured_utc or '(none)':26s} {item.capture_precision:8s} {name}")
    undated = [
        m for m in db.list_media(limit=1000, include_excluded=True)
        if m.capture_precision == "unknown"
    ]
    if undated:
        print(f"    {len(undated)} undated — excluded from the timeline, not lost")

    if args.search:
        print(f"\n  SEARCH — \"{args.search}\"")
        hits = db.search(args.search, limit=8)
        if hits:
            for hit in hits:
                record = db.get_media(hit.media_id)
                print(f"    {Path(record['sources'][0]['path']).name}")
        else:
            print("    no matches")

    primaries = db.list_media(primaries_only=True, limit=10000)
    print(f"\n{rule}")
    print(f"  {db.count_media()} scanned  ->  {len(primaries)} after duplicate suppression")
    print(f"  library: {workdir/'library.db'}")
    print(f"{rule}\n")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
