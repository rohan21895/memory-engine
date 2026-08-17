"""Structured progress.

A terabyte import that prints nothing for six hours is unusable, and a progress
bar that lies is worse than none. Three rules follow from that:

* REAL UNITS, NEVER A SYNTHETIC PERCENTAGE. `12,400 of 318,000 files` survives
  a restart honestly. A percentage computed against a total that is still being
  discovered jumps backwards, and a user watching it reasonably concludes the
  import restarted.

* `units_total` IS NULLABLE AND STAYS NULL UNTIL IT IS KNOWN. This mirrors
  JobSpec.Progress, which says the same thing for the same reason.

* EVERY EVENT IS A LINE OF JSON ON DISK AND A LINE OF TEXT ON THE TERMINAL.
  The JSONL file is what a desktop shell tails; the text is what a human
  watches. They carry the same facts, so a support conversation about "it said
  it was done" can be settled from the file.

The emitter is deliberately dumb: no threads, no background timer. It writes
when it is called and throttles by elapsed time so that a 300k-file scan does
not spend its life formatting strings. `flush=True` on every write is on
purpose -- an unflushed buffer is how a killed process loses the last thing it
said, which is precisely the thing you want to read after a kill.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

__all__ = ["Event", "ProgressReporter", "utc_now"]

_MIN_INTERVAL_S = 1.0


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Event:
    at: str
    run_id: str
    stage: str
    kind: str
    message: str = ""
    units_done: float | None = None
    units_total: float | None = None
    unit: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "at": self.at,
            "run_id": self.run_id,
            "stage": self.stage,
            "kind": self.kind,
        }
        if self.message:
            payload["message"] = self.message
        if self.units_done is not None:
            payload["units_done"] = self.units_done
            payload["units_total"] = self.units_total
            payload["unit"] = self.unit
        if self.detail:
            payload["detail"] = dict(self.detail)
        return payload


class ProgressReporter:
    """Writes JSONL to disk and human lines to a stream.

    `path` is appended to, never truncated: a resumed run's events belong in the
    same file as the run they are resuming, and a reader that finds a truncated
    log cannot tell a resume from a fresh start.
    """

    def __init__(
        self,
        *,
        run_id: str,
        path: Path | None = None,
        stream: TextIO | None = None,
        quiet: bool = False,
        min_interval_s: float = _MIN_INTERVAL_S,
        clock: Any = time.monotonic,
    ) -> None:
        self.run_id = run_id
        self._path = path
        self._stream = stream if stream is not None else sys.stderr
        self._quiet = quiet
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._last_emit: dict[str, float] = {}
        self._handle: TextIO | None = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = path.open("a", encoding="utf-8")

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
            self._handle = None

    def __enter__(self) -> ProgressReporter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- emission --------------------------------------------------------

    def emit(self, event: Event) -> None:
        if self._handle is not None:
            self._handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
            self._handle.flush()
        if not self._quiet:
            self._stream.write(self._render(event) + "\n")
            self._stream.flush()

    def event(self, stage: str, kind: str, message: str = "", **detail: Any) -> None:
        self.emit(
            Event(at=utc_now(), run_id=self.run_id, stage=stage, kind=kind,
                  message=message, detail=detail)
        )

    def progress(
        self,
        stage: str,
        *,
        units_done: float,
        units_total: float | None,
        unit: str,
        message: str = "",
        force: bool = False,
    ) -> None:
        """Throttled progress. `force` bypasses the throttle.

        Callers force the first and last tick of a stage. Without that a stage
        that finishes inside the throttle window emits nothing at all, and a
        short run looks like a hung one.
        """
        now = self._clock()
        previous = self._last_emit.get(stage)
        if not force and previous is not None and now - previous < self._min_interval_s:
            return
        self._last_emit[stage] = now
        self.emit(
            Event(
                at=utc_now(),
                run_id=self.run_id,
                stage=stage,
                kind="progress",
                message=message,
                units_done=units_done,
                units_total=units_total,
                unit=unit,
            )
        )

    # -- rendering -------------------------------------------------------

    @staticmethod
    def _render(event: Event) -> str:
        marker = {
            "stage_start": "->",
            "stage_done": "ok",
            "stage_skipped": "--",
            "stage_blocked": "!!",
            "stage_unavailable": "??",
            "stage_failed": "XX",
            "progress": "  ",
            "note": "  ",
            "run_start": "==",
            "run_done": "==",
        }.get(event.kind, "  ")
        parts = [f"  {marker} {event.stage:<14}"]
        if event.units_done is not None:
            total = "?" if event.units_total is None else f"{event.units_total:,.0f}"
            parts.append(f"{event.units_done:,.0f}/{total} {event.unit}")
        if event.message:
            parts.append(event.message)
        if event.detail:
            parts.append(
                " ".join(f"{key}={value}" for key, value in sorted(event.detail.items()))
            )
        return "  ".join(part for part in parts if part)
