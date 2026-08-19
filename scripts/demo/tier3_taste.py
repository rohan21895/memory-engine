"""Run the Tier 3 taste pass over a workdir the pipeline has already analysed.

    # 1. look at what would be sent, without a key and without consenting
    python3 scripts/demo/tier3_taste.py LIBRARY --workdir WORK --dry-run

    # 2. record consent, then send
    python3 scripts/demo/tier3_taste.py LIBRARY --workdir WORK --grant-consent
    python3 scripts/demo/tier3_taste.py LIBRARY --workdir WORK

WHY THIS IS TWO COMMANDS AND NOT A FLAG

The whole design of this pass is that a person can read the exact bytes before
authorising them. `--dry-run` writes the request body, the composed sheet and
the manifest and stops before consent is even consulted; `--grant-consent`
writes a ConsentRef next to the library. Collapsing them into one invocation
would make the inspection step something you skip by not passing an argument,
which is the same as not having it.

`--grant-consent` writes a LOCAL stand-in for a record that `services/api` will
own: a real deployment issues the ledger entry when the user agrees in the UI,
and this script only exists because that UI is not built. It is named for what
it does so that finding it in a production path is obvious in review.

The workdir must already hold an analysed library -- run the pipeline's
ingest/analysis/faces/ranking stages first. This script re-runs only `taste`,
which is cheap, so the inspect-then-decide loop does not re-analyse anything.

Exit codes match the pipeline CLI: 0 fine, 1 blocked, 2 failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "pipeline"))

from memory_engine_pipeline.runner import run_pipeline  # noqa: E402
from memory_engine_pipeline.stages import taste  # noqa: E402
from memory_engine_pipeline.stages.base import Settings  # noqa: E402

SCOPE = "tier3_contact_sheet"


def write_consent(path: Path, hours: int) -> dict:
    """A ConsentRef with a real expiry. Never open-ended.

    `expires_at` is required by this script even though the schema allows it to
    be null: a consent that never expires is one nobody revisits, and the
    transport treats the expiry instant itself as over (`<=`, not `<`).
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    record = {
        "ledger_entry_id": str(uuid.uuid4()),
        "scope": SCOPE,
        "granted_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=hours)).isoformat(),
        "revoked_at": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tier3_taste")
    parser.add_argument("source", type=Path, help="the folder that was ingested")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="compose and write the request body without sending")
    parser.add_argument("--grant-consent", action="store_true",
                        help="write a ConsentRef into the workdir and exit")
    parser.add_argument("--consent-hours", type=int, default=24)
    parser.add_argument("--consent", type=Path, default=None,
                        help="where the ConsentRef lives (default <workdir>/"
                             f"{taste.DEFAULT_CONSENT_FILENAME})")
    parser.add_argument("--album-photos", type=int, default=8,
                        help="how many the model is asked to choose")
    parser.add_argument("--pool", type=int, default=3,
                        help="candidates shown per slot wanted")
    parser.add_argument("--model", default=Settings().tier3_model)
    args = parser.parse_args(argv)

    consent_path = args.consent or (args.workdir / taste.DEFAULT_CONSENT_FILENAME)

    if args.grant_consent:
        record = write_consent(consent_path, args.consent_hours)
        print(f"wrote a consent record to {consent_path}")
        print(f"  scope      {record['scope']}")
        print(f"  granted_at {record['granted_at']}")
        print(f"  expires_at {record['expires_at']}")
        print("\nDelete this file to withdraw it; the pass refuses without it.")
        return 0

    report = run_pipeline(
        [args.source],
        args.workdir,
        stages=["taste"],
        settings=Settings(
            render_print=False,
            render_video=False,
            album_target_count=args.album_photos,
            tier3_enabled=True,
            tier3_dry_run=args.dry_run,
            tier3_model=args.model,
            tier3_consent_path=str(consent_path),
            tier3_pool_multiplier=args.pool,
        ),
    )

    for result in report.results:
        print(f"\n{result.stage} -> {result.status.value}")
        print(f"  {result.detail}")
        for key, value in (result.counts or {}).items():
            print(f"  {key}: {value}")
        for output in result.outputs:
            print(f"  output: {output}")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
