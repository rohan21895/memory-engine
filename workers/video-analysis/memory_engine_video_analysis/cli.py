"""`python3 -m memory_engine_video_analysis` — proxies in, MomentRecords out.

    proxy + frame index (workers/ingest)
        -> visual, audio, shot producers
        -> FeatureStream
        -> packages/story-engine plan_moments
        -> MomentRecords, validated against contracts/schemas

EXIT CODES ARE THE CONTRACT WITH A CALLER, and they follow
`services/pipeline`'s vocabulary rather than inventing a second one:

    0   every video that was asked for produced moments
    1   there was nothing to analyse, or a prerequisite a person must supply is
        missing (no proxies yet — run ingest's generate_video_proxy job; no
        FFmpeg). Recoverable by acting. NOT a success
    2   analysis ran and broke

A run that finds forty videos and analyses none of them exits 1, for the same
reason the pipeline does: a zero-length success is the failure mode this
repository exists to make impossible.

WHAT IT WILL NOT DO
It will not analyse a video with no proxy by making one. Proxy generation is
`workers/ingest`'s `generate_video_proxy` job — hardware decode, checkpoints,
span reconciliation, content-addressed output — and a second implementation
here would be a second answer to what the 480p raster of a video is.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import stream as stream_module
from .decode import DecodeError, ToolMissing
from .proxy import ProxyError, proxies_in_record
from .transcript import NullTranscriptBackend

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "contracts" / "schemas"


def _records_root(workdir: Path, override: Path | None) -> Path:
    """Where MediaRecords live.

    `services/pipeline` hands `workdir/records` to the Rust worker as its
    output directory and the worker then writes `records/xx/yy/<id>.json`
    inside it, so the default is two levels of "records". Both layouts are
    accepted because a caller pointing at the ingest output directly is a
    reasonable thing to do; neither is guessed silently — the chosen path is
    printed.
    """
    if override is not None:
        return override
    nested = workdir / "records" / "records"
    if nested.is_dir():
        return nested
    return workdir / "records"


def _load_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*/*.json")):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"record {path} is unreadable: {error}") from error
        if isinstance(parsed, dict) and parsed.get("media_id"):
            records.append(parsed)
    return records


def _validator() -> Any:
    from jsonschema import Draft202012Validator  # noqa: PLC0415
    from referencing import Registry, Resource  # noqa: PLC0415

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(documents["moment-record.schema.json"], registry=registry)


def _write_atomically(path: Path, payload: Any) -> None:
    import os  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m memory_engine_video_analysis",
        description="Produce MomentRecords from the 480p proxies in a library.",
    )
    parser.add_argument("workdir", type=Path, help="a services/pipeline workdir")
    parser.add_argument("--records", type=Path, default=None,
                        help="MediaRecord tree, if not <workdir>/records/records")
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write moment JSON (default <workdir>/moments)")
    parser.add_argument("--media-id", action="append", default=[],
                        help="analyse only these media ids (repeatable)")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after N videos; 0 means all")
    parser.add_argument(
        "--at", default=None,
        help=(
            "RFC 3339 instant recorded as created_at/ran_at. Defaults to now, "
            "which is the truth for a real run; pass a fixed value to make two "
            "runs byte-identical"
        ),
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    from memory_engine_story.moments import plan_moments  # noqa: PLC0415

    def say(message: str = "") -> None:
        if not arguments.quiet:
            print(message)

    root = _records_root(arguments.workdir, arguments.records)
    if not root.is_dir():
        say(f"no MediaRecords under {root}. Run the ingest stage first.")
        return 1
    records = _load_records(root)
    wanted = set(arguments.media_id)
    videos = [
        record
        for record in records
        if record.get("kind") == "video" and (not wanted or record["media_id"] in wanted)
    ]
    say(f"records  {len(records)} under {root}, {len(videos)} video")

    with_proxy: list[tuple[dict[str, Any], Any]] = []
    without: list[str] = []
    for record in sorted(videos, key=lambda item: item["media_id"]):
        try:
            proxies = proxies_in_record(record)
        except ProxyError as error:
            print(f"FAILED {record['media_id'][:12]}: {error}", file=sys.stderr)
            return 2
        if proxies:
            if len(proxies) > 1:
                # A record with two 480p proxies means one was regenerated and
                # the old one was never detached. Picking the lowest proxy_id is
                # deterministic, which is the only property that matters here,
                # but doing it SILENTLY would mean an operator who regenerated a
                # proxy could be analysing the previous one without knowing.
                say(
                    f"{record['media_id'][:12]} carries {len(proxies)} video "
                    f"proxies; analysing {proxies[0].proxy_id[:12]} (lowest "
                    "proxy_id). Detach the stale one."
                )
            with_proxy.append((record, proxies[0]))
        else:
            without.append(record["media_id"])
    if without:
        say(
            f"skipping {len(without)} video(s) with no 480p proxy: run the ingest "
            "worker's generate_video_proxy job for them"
        )
    if not with_proxy:
        say(
            "nothing to analyse. Moment scoring needs a video proxy and a frame "
            "index, and no record has one."
        )
        return 1
    if arguments.limit > 0:
        with_proxy = with_proxy[: arguments.limit]

    analysed_at = arguments.at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    out_root = arguments.out or (arguments.workdir / "moments")
    validator = _validator()
    total_moments = 0
    total_eliminated = 0
    failures = 0

    for record, proxy in with_proxy:
        name = (record.get("sources") or [{}])[0].get("original_filename") or record[
            "media_id"
        ][:12]
        try:
            feature_stream, report = stream_module.analyse_proxy(
                proxy, transcript_backend=NullTranscriptBackend()
            )
            plan = plan_moments(
                feature_stream,
                run_id=stream_module.SCORER_RUN_ID,
                created_at=analysed_at,
                model_runs=stream_module.model_runs_for(
                    proxy, report, ran_at=analysed_at
                ),
            )
        except (DecodeError, ProxyError, ToolMissing, ValueError, RuntimeError) as error:
            failures += 1
            print(f"FAILED {name}: {type(error).__name__}: {error}", file=sys.stderr)
            continue

        rows = plan.records
        invalid = [
            (index, sorted(validator.iter_errors(row), key=lambda e: list(e.path)))
            for index, row in enumerate(rows)
        ]
        broken = [(index, errors) for index, errors in invalid if errors]
        if broken:
            failures += 1
            index, errors = broken[0]
            print(
                f"FAILED {name}: record {index} of {len(rows)} does not satisfy "
                f"moment-record.schema.json: "
                + "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:3]),
                file=sys.stderr,
            )
            continue

        _write_atomically(
            out_root / f"{record['media_id']}.json",
            {
                "media_id": plan.media_id,
                "analysed_at": analysed_at,
                "analyser": f"{stream_module.ANALYSER_ID}/{stream_module.ANALYSER_VERSION}",
                "report": {
                    "frame_count": report.frame_count,
                    "rate": report.rate,
                    "start_value": report.start_value,
                    "analysis_size": list(report.analysis_size),
                    "shot_detector": report.shot_detector,
                    "shot_count": report.shot_count,
                    "cut_frames": list(report.cut_frames),
                    "transnetv2_seam": report.transnetv2_seam,
                    "audio_available": report.audio_available,
                    "audio_reason": report.audio_reason,
                    "transcript_available": report.transcript_available,
                    "transcript_reason": report.transcript_reason,
                    "not_measured": list(report.not_measured),
                },
                "weights_id": plan.weights_id,
                "weights_digest": plan.weights_digest,
                "eliminated_fraction": plan.eliminated_fraction,
                "records": list(rows),
            },
        )

        total_moments += len(plan.moments)
        total_eliminated += len(plan.eliminated)
        say("")
        say(f"{name}")
        for line in report.lines():
            say(f"  {line}")
        say(
            f"    moments: {len(plan.moments)} kept, {len(plan.eliminated)} "
            f"eliminated, {plan.eliminated_fraction:.0%} of samples eliminated, "
            f"{plan.candidates_considered} candidates considered"
        )
        for scored in plan.moments[:3]:
            from memory_engine_story.moments import explain  # noqa: PLC0415

            start = scored.start / report.rate
            say(
                f"    kept  {start:6.2f}s +{scored.duration / report.rate:4.2f}s  "
                f"{explain(scored)}"
            )

    say("")
    say(
        f"{len(with_proxy) - failures}/{len(with_proxy)} videos analysed; "
        f"{total_moments} moments kept, {total_eliminated} eliminated records "
        f"written under {out_root}"
    )
    if failures:
        return 2
    return 0
