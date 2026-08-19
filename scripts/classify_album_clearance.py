#!/usr/bin/env python3
"""Classify an album's photographs and write its print SafetyClearance.

Reads the AlbumSpec's publication set (first-appearance order across pages,
matching the renderer's `publicationMediaIds`), pulls each photo's existing
SigLIP 2 vision embedding from the library's vector index, runs the safety
head, and writes `<workdir>/print-clearance.json` with
`load_mode: "development"` — the renderer refuses it unless the job
explicitly sets `allow_development_load_mode`.

Usage:
    python scripts/classify_album_clearance.py --workdir runs/library
"""
from __future__ import annotations

import argparse
import array
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "safety-gate"))

from memory_engine_safety.calibration import load_calibration  # noqa: E402
from memory_engine_safety.classify import (  # noqa: E402
    Candidate,
    SafetyClassifier,
    Thresholds,
)
from memory_engine_safety.embedding import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    EMBEDDING_SPACE,
    StaticEmbedder,
)
from memory_engine_safety.head import load_head  # noqa: E402
from memory_engine_safety.manifest import build_manifest  # noqa: E402

DEFAULT_HEAD_DIR = REPO_ROOT / "models" / "weights" / "safety-head"


def publication_media_ids(album: dict) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for page in album["pages"]:
        for placement in page["placements"]:
            media_id = placement["media_id"]
            if media_id not in seen:
                seen.add(media_id)
                ordered.append(media_id)
    return ordered


def unit_vector(blob: bytes) -> tuple[float, ...]:
    values = array.array("f")
    values.frombytes(blob)
    if len(values) != EMBEDDING_DIMENSIONS:
        raise SystemExit(
            f"stored vector is {len(values)}-d, not {EMBEDDING_DIMENSIONS}-d"
        )
    norm = math.sqrt(math.fsum(v * v for v in values))
    if norm == 0.0 or norm != norm:
        raise SystemExit("stored vector has no direction")
    return tuple(v / norm for v in values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True, type=Path)
    parser.add_argument("--album", type=Path, default=None,
                        help="AlbumSpec path; defaults to the workdir's only album")
    parser.add_argument("--head", type=Path, default=DEFAULT_HEAD_DIR / "nsfw-siglip-head.v1.json")
    parser.add_argument("--calibration", type=Path,
                        default=DEFAULT_HEAD_DIR / "nsfw-siglip-head.v1.calibration.json")
    parser.add_argument("--out", type=Path, default=None,
                        help="defaults to <workdir>/print-clearance.json")
    parser.add_argument("--override-blocked-by", default=None, metavar="NAME",
                        help="record a human override on every BLOCKED verdict in "
                             "this run, attributed to NAME. This is the owner "
                             "saying 'print these anyway' -- scope is this album "
                             "and the print sink only, per the contract. Never a "
                             "default; indeterminate verdicts are not overridable.")
    args = parser.parse_args()

    album_path = args.album
    if album_path is None:
        candidates = sorted((args.workdir / "outputs" / "album").glob("*.json"))
        if len(candidates) != 1:
            raise SystemExit(
                f"{len(candidates)} AlbumSpecs in {args.workdir}/outputs/album; "
                "pass --album to pick one"
            )
        album_path = candidates[0]
    album = json.loads(album_path.read_text(encoding="utf-8"))
    media_ids = publication_media_ids(album)

    head = load_head(args.head)
    calibration = load_calibration(args.calibration)

    connection = sqlite3.connect(args.workdir / "library.db")
    vectors: dict[str, tuple[float, ...]] = {}
    evidence_by_media: dict[str, str] = {}
    for media_id in media_ids:
        row = connection.execute(
            "SELECT embedding FROM vector WHERE owner_kind='media' AND owner_id=? AND space=?",
            (media_id, EMBEDDING_SPACE),
        ).fetchone()
        proxy = connection.execute(
            "SELECT proxy_id FROM media_proxy WHERE media_id=? AND kind='thumbnail_512'",
            (media_id,),
        ).fetchone()
        if row is None or proxy is None:
            # Leave it out of the embedder's table: the classifier records the
            # miss as an indeterminate verdict, which blocks — the honest result.
            print(f"warning: no embedding or proxy for {media_id[:12]}...; "
                  "its verdict will be indeterminate", file=sys.stderr)
            evidence_by_media[media_id] = proxy[0] if proxy else media_id
            continue
        evidence_by_media[media_id] = proxy[0]
        vectors[proxy[0]] = unit_vector(row[0])
    connection.close()

    classifier = SafetyClassifier(
        thresholds=Thresholds(),
        head=head,
        calibration=calibration,
        embedder=StaticEmbedder(vectors),
    )
    classification = classifier.classify(
        [Candidate(media_id=m, evidence_id=evidence_by_media[m]) for m in media_ids]
    )

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    overrides = None
    if args.override_blocked_by:
        overrides = {
            verdict["media_id"]: {
                "decided_at": now,
                "decided_by": args.override_blocked_by,
                "scope": "item_and_sink",
            }
            for verdict in classification.verdicts
            if verdict["verdict"] == "blocked"
        }
        if overrides:
            print(f"overriding {len(overrides)} blocked verdict(s), "
                  f"decided by {args.override_blocked_by}")
        else:
            overrides = None
    manifest = build_manifest(
        classification,
        sink="print",
        created_at=now,
        ran_at=now,
        model={
            "model_id": "nsfw-siglip-head",
            "version": "v1-dev",
            "provenance": head.provenance.method,
            "text_tower": head.provenance.text_tower,
            "prompt_bank_digest": head.provenance.prompt_bank_digest,
        },
        thresholds=classifier.thresholds,
        load_mode="development",
        sink_detail=f"album {album['album_id'][:12]}",
        overrides=overrides,
    )

    out = args.out or (args.workdir / "print-clearance.json")
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    decision = manifest["decision"]
    print(f"clearance: {out}")
    print(json.dumps(decision, indent=2))
    return 0 if decision["cleared_for_publication"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
