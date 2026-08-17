"""JobSpecs, and the durable place they live.

THE UNIT OF WORK IS A JobSpec AND THE STORE IS THE LIBRARY DATABASE.

Not a file, not an in-memory queue. media-db already owns a `job` table with a
primary key on `job_id`, and `put_job` writes inside a transaction, so a
checkpoint either lands whole or does not land at all. Putting job state
anywhere else would mean two durability stories in one process and a window in
which the library and the job log disagree about what happened -- which is
precisely the window a kill lands in.

WHAT RESUMPTION ACTUALLY RESTS ON

Two mechanisms, deliberately different:

* COARSE, in the JobSpec: `state.status`, `state.progress`, and an opaque
  `checkpoint.cursor`. This is what tells the runner that a stage was underway
  and roughly where.

* FINE, in the records themselves: `MediaRecord.processing.stages.<stage>` is
  `done` or it is not. This is what tells the runner which of 300,000 files to
  skip.

The fine-grained half is deliberately NOT `checkpoint.completed_input_ids`,
even though the contract offers that field. A list of 300,000 hashes
re-serialised after every item is quadratic, and a scan that spends its time
rewriting its own checkpoint is the classic way this kind of runner dies at
scale. The contract's own note says the cursor is opaque and worker-owned; ours
says "ask the records". The records are the durable, content-addressed truth
anyway -- a completed_input_ids list could disagree with them, and then which
one is right?

IDEMPOTENCY IS THE PRIMARY KEY

`job_id` is a BLAKE3 over (job_type, sorted inputs, source-locator digest,
params digest, scope). Re-running identical work therefore finds a completed
row and does nothing. Changing a parameter produces a different id and does the
work again, which is correct: a re-run with a different threshold is not the
same job and must not silently reuse the first result.

EVERY JobSpec IS SCHEMA-CHECKED BEFORE IT IS STORED
Cheap (microseconds against an in-process validator) and it catches the class
of bug where a runner and the contract drift apart quietly for weeks. A job
that does not validate is a bug in this file, so it raises rather than being
repaired.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .events import utc_now
from .ids import job_identity, params_digest

__all__ = ["JobStore", "JobValidationError", "build_job"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _REPO_ROOT / "contracts" / "schemas"

CHECKPOINT_VERSION = 1


class JobValidationError(ValueError):
    """A JobSpec this runner built does not satisfy the contract."""


@lru_cache(maxsize=1)
def _job_validator() -> Any:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(_SCHEMA_DIR.glob("*.schema.json"))
    }
    registry = Registry().with_resources(
        [(name, Resource.from_contents(doc)) for name, doc in documents.items()]
    )
    return Draft202012Validator(documents["job-spec.schema.json"], registry=registry)


def validate_job(job: Mapping[str, Any]) -> None:
    errors = sorted(_job_validator().iter_errors(job), key=lambda error: list(error.path))
    if errors:
        rendered = "; ".join(f"{list(error.path)}: {error.message}" for error in errors[:5])
        raise JobValidationError(
            f"the runner built a JobSpec the contract rejects: {rendered}"
        )


def build_job(
    *,
    job_type: str,
    scope: str,
    params: Mapping[str, Any],
    media_ids: Sequence[str] = (),
    moment_ids: Sequence[str] = (),
    source_paths: Sequence[str] = (),
    locator_digest: str | None = None,
    depends_on: Sequence[str] = (),
    priority: int = 500,
    resumable: bool = True,
    requirements: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """A pending JobSpec with a content-addressed id.

    `media_ids` participate in the identity, which is what makes "analyse these
    exact 40,000 photos" a different job from "analyse these 40,001". That is
    the intended behaviour for the aggregate stages: adding a photo genuinely
    changes what a dedupe pass or an album plan should produce, and reusing the
    previous answer would be wrong rather than efficient.
    """
    digest = params_digest(params)
    identity_inputs = tuple(media_ids) + tuple(moment_ids)
    job_id = job_identity(
        job_type=job_type,
        input_ids=identity_inputs,
        locator_digest=locator_digest,
        params_digest_hex=digest,
        scope=scope,
    )
    job = {
        "schema_version": "v0",
        "job_id": job_id,
        "job_type": job_type,
        "inputs": {
            "media_ids": sorted(media_ids),
            "moment_ids": sorted(moment_ids),
            "face_ids": [],
            "edl_id": None,
            "album_id": None,
            "source_paths": list(source_paths),
            "source_locator_digest": locator_digest,
            "parent_job_id": None,
            "depends_on_job_ids": sorted(depends_on),
            "models": [],
        },
        "params": dict(params),
        "params_digest": digest,
        "scope": scope,
        "priority": priority,
        # Local work, every stage. `requires_egress: false` is written out
        # rather than omitted because an absent declaration and a negative one
        # must not look the same -- the contract says so and the CI egress test
        # depends on it.
        "egress": {
            "requires_egress": False,
            "consent": None,
            "destination": None,
            "payload_kind": None,
            "estimated_bytes": None,
        },
        "state": {
            "status": "pending",
            "attempts": 0,
            "worker_id": None,
            "started_at": None,
            "heartbeat_at": None,
            "finished_at": None,
            "progress": None,
        },
        "checkpoint": {
            "resumable": resumable,
            "cursor": None,
            "checkpoint_version": CHECKPOINT_VERSION,
            "updated_at": None,
            "completed_input_ids": [],
            "partial_output_ids": [],
        },
        "outputs": [],
        "error": None,
        "journal": {"entries": []},
        "created_at": created_at or utc_now(),
        "deadline": None,
    }
    if requirements is not None:
        # Declared only where it is true of the job. `requires_source_file` in
        # particular is a promise about the second and last time a source file
        # is opened in its life, and render-print refuses a job that does not
        # make it -- correctly, because a renderer that opened originals
        # without saying so would break the "sources are read exactly twice"
        # guarantee silently.
        job["requirements"] = {
            "compute": "cpu",
            "min_vram_mb": None,
            "min_ram_mb": None,
            "min_disk_mb": None,
            "hardware_decode": False,
            "requires_source_file": False,
            "estimated_duration_ms": None,
            **dict(requirements),
        }
    validate_job(job)
    return job


@dataclass(slots=True)
class JobStore:
    """Reads and writes JobSpecs through media-db, and nothing else."""

    database: Any
    worker_id: str

    # -- lookup ----------------------------------------------------------

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.database.connection.execute(
            "SELECT record_json FROM job WHERE job_id = ?", (job_id,)
        ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def put(self, job: Mapping[str, Any]) -> str:
        validate_job(job)
        return self.database.put_job(dict(job))

    def ensure(self, job: Mapping[str, Any]) -> dict[str, Any]:
        """Return the stored job with this id, storing `job` if there is none.

        The stored copy wins. A completed job must not be reset to pending
        because the runner rebuilt a fresh pending spec with the same id --
        that is the exact shape of "re-running redoes everything", and it is an
        easy accident because the freshly built spec looks perfectly correct.
        """
        existing = self.get(job["job_id"])
        if existing is not None:
            return existing
        self.put(job)
        return dict(job)

    # -- transitions -----------------------------------------------------

    def reclaim(self) -> list[str]:
        """Turn jobs left `running` by a killed process back into `pending`.

        The contract is explicit that `paused` is a deliberate user action and
        `pending`-after-a-crash is what a killed `running` job becomes, so a
        paused job is untouched here. Without this, a crashed job stays
        `running` forever and its queue never moves -- and, worse, a resumed
        run would see "already running" and decline to redo it, which reads as
        success.
        """
        reclaimed: list[str] = []
        for job in self.database.jobs_by_status("running"):
            job["state"]["status"] = "pending"
            job["state"]["worker_id"] = None
            job["state"]["heartbeat_at"] = utc_now()
            self.put(job)
            reclaimed.append(job["job_id"])
        return reclaimed

    def begin(self, job: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        job["state"]["status"] = "running"
        job["state"]["attempts"] = int(job["state"].get("attempts", 0)) + 1
        job["state"]["worker_id"] = self.worker_id
        job["state"]["started_at"] = job["state"].get("started_at") or now
        job["state"]["heartbeat_at"] = now
        job["state"]["finished_at"] = None
        job["error"] = None
        self.put(job)
        return job

    def checkpoint(
        self,
        job: dict[str, Any],
        *,
        cursor: Mapping[str, Any] | None = None,
        units_done: float | None = None,
        units_total: float | None = None,
        unit: str = "items",
        message: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        job["state"]["heartbeat_at"] = now
        if units_done is not None:
            job["state"]["progress"] = {
                "units_done": units_done,
                "units_total": units_total,
                "unit": unit,
                "bytes_processed": None,
                "message": message,
            }
        if cursor is not None:
            job["checkpoint"]["cursor"] = json.dumps(
                dict(cursor), sort_keys=True, separators=(",", ":")
            )
            job["checkpoint"]["checkpoint_version"] = CHECKPOINT_VERSION
            job["checkpoint"]["updated_at"] = now
        self.put(job)
        return job

    def read_cursor(self, job: Mapping[str, Any]) -> dict[str, Any]:
        """The cursor, or an empty dict when it is absent or from an old format.

        A cursor written by a different checkpoint version is DISCARDED rather
        than parsed. The contract spells this out: handing an old cursor to code
        that will misparse it is worse than restarting, because a misparsed
        cursor resumes at the wrong place and the gap is invisible.
        """
        checkpoint = job.get("checkpoint") or {}
        if int(checkpoint.get("checkpoint_version", 0)) != CHECKPOINT_VERSION:
            return {}
        raw = checkpoint.get("cursor")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def complete(
        self, job: dict[str, Any], *, outputs: Sequence[Mapping[str, Any]] = ()
    ) -> dict[str, Any]:
        now = utc_now()
        job["state"]["status"] = "completed"
        job["state"]["finished_at"] = now
        job["state"]["heartbeat_at"] = now
        job["state"]["worker_id"] = None
        job["outputs"] = [dict(output) for output in outputs]
        job["error"] = None
        self.put(job)
        return job

    def fail(
        self,
        job: dict[str, Any],
        *,
        code: str,
        message: str,
        retryable: bool,
        status: str = "failed",
    ) -> dict[str, Any]:
        now = utc_now()
        job["state"]["status"] = status
        job["state"]["finished_at"] = now
        job["state"]["heartbeat_at"] = now
        job["state"]["worker_id"] = None
        job["error"] = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "attempt": int(job["state"].get("attempts", 0)),
            "occurred_at": now,
            "failed_input_id": None,
        }
        self.put(job)
        return job

    def block(self, job: dict[str, Any], *, code: str, message: str) -> dict[str, Any]:
        """A stage that cannot run yet, and must not be mistaken for one that did.

        `blocked` is a first-class JobSpec status. Using it -- rather than
        completing the job with zero outputs -- is the difference between "the
        model host was not running" and "there was nothing to analyse", which
        are the two readings of an empty result and only one of them is a
        finished library.
        """
        now = utc_now()
        job["state"]["status"] = "blocked"
        job["state"]["heartbeat_at"] = now
        job["state"]["worker_id"] = None
        job["error"] = {
            "code": code,
            "message": message,
            "retryable": True,
            "attempt": int(job["state"].get("attempts", 0)),
            "occurred_at": now,
            "failed_input_id": None,
        }
        self.put(job)
        return job
