"""What a stage is, and the five ways one can end.

The status vocabulary is the important part of this file. A stage that could
not run must not report the same thing as a stage that ran and found nothing to
do, and neither may report what a stage that succeeded reports. Collapsing
those three is how a pipeline produces a confident, empty result.

    COMPLETED    did work, or verified and reused a durable result.
    SKIPPED      requested work did not run and produced no result. This is
                 incomplete, never a successful cache hit.
    BLOCKED      a prerequisite is missing that a person must fix -- the model
                 host is not running, an upstream stage is blocked. Recoverable
                 by acting and re-running. NOT a success.
    UNAVAILABLE  a worker this repository has not built or installed. Also not
                 a success, but distinct from BLOCKED because the remedy is a
                 build rather than a service start.
    FAILED       ran and broke.

COMPLETED and SKIPPED let dependents inspect durable state or report their own
absence. Only COMPLETED is a successful run outcome. Everything else propagates
or leaves the run incomplete: a dependent of a blocked stage is itself blocked,
with the blocking stage named, so the summary says "album: blocked by
analyze_image" rather than "album: produced 0 pages".
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = ["Settings", "StageContext", "StageResult", "StageStatus"]


class StageStatus(str, Enum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"

    @property
    def satisfies_dependents(self) -> bool:
        return self in (StageStatus.COMPLETED, StageStatus.SKIPPED)

    @property
    def is_success(self) -> bool:
        return self is StageStatus.COMPLETED


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: str
    status: StageStatus
    detail: str = ""
    job_id: str | None = None
    counts: Mapping[str, Any] = field(default_factory=dict)
    outputs: tuple[str, ...] = ()
    blocked_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage,
            "status": self.status.value,
            "detail": self.detail,
        }
        if self.job_id:
            payload["job_id"] = self.job_id
        if self.counts:
            payload["counts"] = dict(self.counts)
        if self.outputs:
            payload["outputs"] = list(self.outputs)
        if self.blocked_by:
            payload["blocked_by"] = self.blocked_by
        return payload


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the operator can change, in one place with the defaults visible."""

    scope: str = "library:default"
    follow_symlinks: bool = False
    include_hidden: bool = False
    max_depth: int = 32
    rescan: bool = False

    ml_runtime_endpoint: str | None = None
    ml_runtime_timeout_s: float = 3.0
    embedding_model: str = "siglip2-so400m-384"
    face_model: str = "scrfd-10g-bnkps"
    infer_batch: int = 32

    # Only faces at or above this detector score are counted towards a
    # MediaRecord's face summary. Precision over recall: a wrong person in a
    # family album is a catastrophic failure, and the album stage reads this
    # summary.
    #
    # It is also the floor `memory_engine_face.records.SUBJECT_DETECTION_FLOOR`
    # applies when handing rectangles to the print validator, and the two are
    # deliberately the same number: a face that counts toward "how many people
    # are in this photo" and a face the trim check protects must be the same
    # set, or the validator's own contradiction checks fire on a difference of
    # convention.
    face_score_floor: float = 0.60

    # The recognition model. Its config declares the alignment template, and
    # the analysis stage refuses to run when that template's landmark scheme is
    # not the one the detector emits -- see stages/analysis.py.
    face_embedding_model: str = "arcface-buffalo-l"

    # Clear the two face steps on every record and run them again. The remedy
    # for a library analysed before faces were wired, or one whose detector has
    # been swapped: `face_id` includes the detector's id and version, so the
    # old rectangles are not merely stale, they are addressed differently.
    # Without this the album stage refuses such a library (its face_count
    # disagrees with the rectangles stored for it) and there is nothing the
    # operator can do about it short of deleting the database.
    reanalyze_faces: bool = False

    vendor_profile: str = "layflat-300-square"
    # Path to the vendor's real ICC profile. Absent means the print stage falls
    # back to sharp's built-in CMYK profile under the vendor's profile NAME,
    # which is a development convenience the renderer permits only while the
    # vendor profile pins no `icc_hash`. It is announced every time, because a
    # PDF whose embedded profile is not the one its name claims is exactly the
    # kind of near-miss that survives to a print run.
    icc_profile_path: str | None = None
    album_target_count: int = 24
    album_photos_per_page: int = 1

    # What the reel planner is ASKED for. Landing on a cut point matters more
    # than hitting the number, so the realised length is usually shorter and the
    # plan says by how much.
    reel_seconds: float = 15.0
    reel_loudness_lufs: float = -14.0

    render_print: bool = True
    render_video: bool = True

    # Path to the print SafetyClearance manifest. None means
    # `<workdir>/print-clearance.json`. Absence is handed to the renderer as an
    # absent clearance, which its gate denies -- never skipped here.
    print_clearance_path: str | None = None
    # Accept a `load_mode: "development"` clearance for the print sink. Off by
    # default; travels into the render job's params (and so its digest), and is
    # announced in the event stream every time it is used.
    allow_development_clearance: bool = False

    # ---- Tier 3, the only thing here that leaves the device ----------------
    #
    # DEFAULT OFF, and off is the absence of a request rather than a disabled
    # feature. Inferring intent from the presence of an API key would mean a
    # key exported for some unrelated tool silently authorises uploading this
    # user's photographs.
    tier3_enabled: bool = False
    # Compose the sheet, write the exact request body to the workdir, and stop
    # before the egress check. The inspection mode, and the reason a person can
    # decide what they are agreeing to before agreeing to it.
    tier3_dry_run: bool = False
    # Pinned here as well as in `album_taste.MODEL_ID` so that "which model
    # answered" is settable per run without editing a package, and recorded in
    # the run report either way. CLAUDE.md rule 7: no silent model swap.
    tier3_model: str = "claude-sonnet-5"
    # Path to a ConsentRef JSON. None means `<workdir>/tier3-consent.json`.
    # Absence of the file blocks; it does not default to permission.
    tier3_consent_path: str | None = None
    # Path to a safety-clearance manifest covering every photograph on the
    # contact sheet. None means `<workdir>/tier3-clearance.json`. Absence
    # blocks at the safety gate -- consent says a sheet MAY be sent, clearance
    # says what is ON it, and neither implies the other.
    tier3_clearance_path: str | None = None
    # Candidates shown per slot wanted. Below 2 there is nothing to reject and
    # the upload buys nothing; the sheet's own 64-cell ceiling caps the top.
    tier3_pool_multiplier: int = 3


@dataclass(slots=True)
class StageContext:
    run_id: str
    settings: Settings
    source_roots: tuple[str, ...]
    workdir: Path
    repo_root: Path
    database: Any
    jobs: Any
    reporter: Any
    # Which stages this invocation was asked to run. A stage the operator did
    # not select is not a failed dependency -- `--stages analysis` over an
    # already-ingested library is a legitimate thing to ask for, and treating
    # the absent ingest stage as "blocked" would make the flag useless.
    #
    # This is why no stage relies on `require` for its real protection. The
    # album stage's actual guard is that it only accepts records whose
    # `processing.state` is `analyzed`, which is a fact about the library
    # rather than about which stages happened to run in this process.
    selected: frozenset[str] = frozenset()
    results: dict[str, StageResult] = field(default_factory=dict)

    def result(self, stage: str) -> StageResult | None:
        return self.results.get(stage)

    def require(self, stage: str, *stages: str) -> StageResult | None:
        """The first unsatisfied dependency, or None when all are satisfied."""
        for name in (stage, *stages):
            if self.selected and name not in self.selected:
                continue
            previous = self.results.get(name)
            if previous is None or not previous.status.satisfies_dependents:
                return previous or StageResult(
                    stage=name,
                    status=StageStatus.BLOCKED,
                    detail=f"{name} did not run",
                )
        return None

    def path(self, *parts: str) -> Path:
        target = self.workdir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


def blocked_by(stage: str, upstream: StageResult) -> StageResult:
    return StageResult(
        stage=stage,
        status=StageStatus.BLOCKED,
        detail=f"{upstream.stage} is {upstream.status.value}: {upstream.detail}",
        blocked_by=upstream.stage,
    )


def write_json_atomically(path: Path, payload: Any) -> None:
    """Write via a temporary file and rename.

    A half-written manifest that is later read as authoritative is a silent
    data loss, and this runner reads its own manifests on the next run. rename
    is atomic within a filesystem; the temporary lives in the same directory so
    it always is.
    """
    import json
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> Any | None:
    import json

    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
