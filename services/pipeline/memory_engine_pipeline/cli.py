"""`python -m memory_engine_pipeline <folder> --workdir DIR`.

The exit code is the contract with whatever is driving this:

    0   every requested stage completed or reused a verified durable result
    1   something was BLOCKED or UNAVAILABLE -- the model host is not running,
        a worker is not built. Recoverable by acting, and NOT a success.
    2   something FAILED.

A run that produced a library with no analysis in it, or disabled a requested
deliverable, exits 1 loudly. That is the whole point of separating the codes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .events import ProgressReporter
from .runner import STAGE_NAMES, format_report, run_pipeline
from .stages.base import Settings


# Read off an INSTANCE, never off the class: Settings uses slots, so
# `Settings.scope` is the slot descriptor rather than the default value, and
# argparse would happily accept it and hand a member_descriptor to the runner.
_DEFAULTS = Settings()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-engine-pipeline",
        description="Run a folder of photos through the whole engine.",
    )
    parser.add_argument("source", nargs="+", type=Path, help="folder(s) to import")
    parser.add_argument("--workdir", type=Path, required=True,
                        help="where the library, proxies and outputs live")
    parser.add_argument("--scope", default=_DEFAULTS.scope,
                        help="namespace separating otherwise-identical work")
    parser.add_argument("--stages", default="",
                        help=f"comma-separated subset of: {', '.join(STAGE_NAMES)}")
    parser.add_argument("--rescan", action="store_true",
                        help="ignore the stat inventory and re-read every file")
    parser.add_argument("--reanalyze-faces", action="store_true",
                        help="clear the face detection and face embedding steps on "
                             "every record and run them again. Needed after a "
                             "detector change, and for a library analysed before "
                             "faces were wired: face ids include the detector "
                             "version, so the old rectangles are addressed "
                             "differently rather than merely stale")
    parser.add_argument("--include-hidden", action="store_true")
    parser.add_argument("--follow-symlinks", action="store_true")
    parser.add_argument("--max-depth", type=int, default=_DEFAULTS.max_depth)
    parser.add_argument("--ml-runtime", default=None,
                        help="host:port of workers/ml-runtime (default 127.0.0.1:50151 "
                             "or $MEMORY_ENGINE_ML_RUNTIME)")
    parser.add_argument("--vendor-profile", default=_DEFAULTS.vendor_profile)
    parser.add_argument("--album-photos", type=int, default=_DEFAULTS.album_target_count)
    parser.add_argument("--photos-per-page", type=int, default=_DEFAULTS.album_photos_per_page)
    parser.add_argument("--icc-profile", default=None,
                        help="path to the vendor's ICC profile; without it the print "
                             "renderer embeds a built-in CMYK profile under the "
                             "vendor's name, which is for development only")
    parser.add_argument("--reel-seconds", type=float, default=_DEFAULTS.reel_seconds,
                        help="how long the reel is asked to be; it ends on a cut "
                             "point, so the realised length is usually shorter and "
                             "the plan says by how much")
    parser.add_argument("--tier3", action="store_true",
                        help="run the Tier 3 taste pass: upload a low-resolution "
                             "contact sheet of the album shortlist to a frontier "
                             "model and record its selection. OFF by default. Also "
                             "needs ANTHROPIC_API_KEY and a consent record; each "
                             "absence is reported separately")
    parser.add_argument("--tier3-dry-run", action="store_true",
                        help="compose the sheet and write the exact request body to "
                             "the workdir WITHOUT sending it. Implies --tier3. Use "
                             "this to see what would be transmitted before "
                             "authorising a real upload")
    parser.add_argument("--tier3-model", default=_DEFAULTS.tier3_model,
                        help="which frontier model answers; recorded in the run "
                             "report, and a reply from a different model fails the "
                             "stage rather than being accepted")
    parser.add_argument("--tier3-consent", default=None,
                        help="path to a ConsentRef JSON authorising the upload "
                             "(default <workdir>/tier3-consent.json). Absence blocks")
    parser.add_argument("--tier3-pool", type=int, default=_DEFAULTS.tier3_pool_multiplier,
                        help="candidates shown per album slot wanted")
    parser.add_argument("--no-render-print", action="store_true")
    parser.add_argument("--no-render-video", action="store_true")
    parser.add_argument("--quiet", action="store_true",
                        help="write events.jsonl but nothing to the terminal")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(
        scope=args.scope,
        follow_symlinks=args.follow_symlinks,
        include_hidden=args.include_hidden,
        max_depth=args.max_depth,
        rescan=args.rescan,
        reanalyze_faces=args.reanalyze_faces,
        ml_runtime_endpoint=args.ml_runtime,
        vendor_profile=args.vendor_profile,
        album_target_count=args.album_photos,
        album_photos_per_page=args.photos_per_page,
        icc_profile_path=args.icc_profile,
        reel_seconds=args.reel_seconds,
        render_print=not args.no_render_print,
        render_video=not args.no_render_video,
        # --tier3-dry-run implies --tier3: asking to see what would be sent is
        # asking for the pass to run, and requiring both flags would mean the
        # inspection mode silently does nothing for anyone who forgot one.
        tier3_enabled=args.tier3 or args.tier3_dry_run,
        tier3_dry_run=args.tier3_dry_run,
        tier3_model=args.tier3_model,
        tier3_consent_path=args.tier3_consent,
        tier3_pool_multiplier=args.tier3_pool,
    )
    stages = [name.strip() for name in args.stages.split(",") if name.strip()] or None

    workdir = args.workdir.expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    reporter = ProgressReporter(
        run_id="pending",
        path=workdir / "events.jsonl",
        stream=sys.stderr,
        quiet=args.quiet,
    )
    try:
        report = run_pipeline(
            args.source, workdir, settings=settings, stages=stages, reporter=reporter
        )
    except (FileNotFoundError, OSError) as error:
        sys.stderr.write(f"pipeline could not start: {error}\n")
        return 2
    finally:
        reporter.close()

    if not args.quiet:
        sys.stdout.write(format_report(report) + "\n")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
