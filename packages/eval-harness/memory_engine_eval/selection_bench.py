"""The first selection-quality gate: critical errors, measured as rates.

WHAT THIS MEASURES

`packages/album-engine/memory_engine_album/selection.py` (selection v3), on
SYNTHETIC candidate pools this module constructs and therefore knows exactly.
Every "critical error" case plants a specific catastrophe the selector must
never commit -- a blink frame chosen over its clean group-mate, a near-twin
pair in the book, a screenshot on an album page, a pinned photo missing --
and reports the rate at which it happened. The expected value is 0 and the
gate is `enforce_expected`, so ANY departure from zero fails CI on its own,
with no baseline movement involved.

Two retention cases guard the other direction: the dominator of a shot group
must actually win (a gate that only checks absences would pass an empty
album), and the category presets must still change a winner (deterministically
-- the same flip, every run).

WHAT THIS DOES NOT MEASURE

Album taste. These pools are arithmetic constructions -- one-hot embeddings,
pinned contrasts -- not photographs. A rate of zero here says "the selector
enforces its own stated rules on data shaped like this"; it does not say the
album is good. The blind A/B tooling is where taste gets measured.

HOW THE CROSS-PACKAGE IMPORT WORKS

The repository's packages are plain source trees, not installed
distributions; every consumer inserts the sibling package roots on sys.path
(see packages/album-engine/tests/test_selection.py and
packages/face-identity/tests/test_eval.py, which imports THIS package the
same way in the other direction). This module does the same, guarded, from
its own location -- the monorepo layout is fixed, so the relative path is
not a guess.

SYNTHETIC DATA ONLY: ids are BLAKE2b/BLAKE3-style hex of bench strings,
person ids are fixed synthetic UUIDs. Nothing here touches a library.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _pkg in ("album-engine", "ranking-engine"):
    _root = str(_REPO / "packages" / _pkg)
    if _root not in sys.path:
        sys.path.insert(0, _root)

from memory_engine_album.selection import (  # noqa: E402
    DEFAULT_POLICY,
    PerFaceAggregates,
    Selection,
    SelectionCandidate,
    SelectionPolicy,
    select,
)
from memory_engine_ranking.fusion import FusedScore, Weights  # noqa: E402

__all__ = [
    "ALGORITHM_VERSION",
    "CASE_EXPECTATIONS",
    "CRITICAL_CASES",
    "RETENTION_CASES",
    "build_gate_document",
    "run_benchmark",
]

# Bumping this is the visible act of changing selection behaviour under the
# gate: it feeds the model pins, so a run against a different version is a
# different model set and the harness refuses the comparison (decision 2).
ALGORITHM_VERSION = "3.0.0"
MODEL_ID = "album-selection-greedy"

_WEIGHTS_DIGEST = Weights().digest()
_SPACE = "bench-synthetic-8d"
_DIM = 8

# One fixed epoch for every pool: 2026-03-01T00:00:00Z, written out per
# candidate as an RFC 3339 instant. Synthetic, deterministic, offset-explicit.
_EPOCH = "2026-03-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}+00:00"

_PERSON = {
    name: f"{index:08x}-0000-4000-8000-000000000000"
    for index, name in enumerate(("ava", "bo", "cy"), start=1)
}


def _mid(tag: str) -> str:
    """A contract-shaped 64-hex media id derived from a bench tag."""
    body = tag.encode("utf-8").hex()
    return (body + "0" * 64)[:64]


def _at(day: int, hour: int = 12, minute: int = 0, second: int = 0) -> str:
    return _EPOCH.format(day=day, hour=hour, minute=minute, second=second)


def _score(value: float) -> FusedScore:
    return FusedScore(
        value=value,
        coverage=1.0,
        rejected=False,
        rejection_reason=None,
        weights_id="default-v1",
        weights_digest=_WEIGHTS_DIGEST,
        feature_set_id="photo-quality-v1",
        contributions=(("exposure", value, 0.5), ("sharpness", value, 0.5)),
    )


def _unit(axis: int) -> tuple[float, ...]:
    """One-hot basis vector: distinct pool photos sit at cosine 0."""
    values = [0.0] * _DIM
    values[axis % _DIM] = 1.0
    return tuple(values)


def _near(axis: int, other_axis: int, cosine: float) -> tuple[float, ...]:
    """A unit vector at exactly `cosine` from `_unit(axis)`, rotated toward
    `_unit(other_axis)`. Exact arithmetic, no RNG: the twin cases pin the
    similarity they claim to pin."""
    if axis % _DIM == other_axis % _DIM:
        raise ValueError("need two distinct axes to rotate between")
    sine = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    values = [0.0] * _DIM
    values[axis % _DIM] = cosine
    values[other_axis % _DIM] = sine
    return tuple(values)


def _cand(tag: str, value: float = 0.8, **kwargs) -> SelectionCandidate:
    kwargs.setdefault("score", _score(value))
    if kwargs.get("embedding") is not None:
        kwargs.setdefault("embedding_space", _SPACE)
    return SelectionCandidate(media_id=_mid(tag), **kwargs)


def _faces(
    eyes: float, smile: float = 0.6, area: float = 0.05, exposure: float = 0.5
) -> PerFaceAggregates:
    return PerFaceAggregates(
        significant_count=1,
        largest_area=area,
        eyes_min=eyes,
        eyes_p10=eyes,
        eyes_mean=eyes,
        smile_min=smile,
        smile_mean=smile,
        exposure_min=exposure,
    )


def _fillers(
    count: int,
    *,
    start_axis: int,
    start_day: int,
    value: float = 0.6,
    eyes: float = 0.8,
) -> list[SelectionCandidate]:
    """Distinct, unremarkable pool context: one per day, pairwise cosine 0."""
    return [
        _cand(
            f"filler-{index}",
            value,
            captured_utc=_at(start_day + index),
            embedding=_unit(start_axis + index),
            awake=0.2,
            smile=0.3,
            aesthetic=0.2,
            composed=0.2,
            per_face=_faces(eyes),
        )
        for index in range(count)
    ]


# ---------------------------------------------------------------------------
# Case measurements. Each returns a rate in [0,1]; the `policy` parameter
# exists so the tests can prove every metric BITES -- hand in a policy with
# the relevant rule disabled and the rate must leave zero.
# ---------------------------------------------------------------------------


def measure_blink_when_clean_exists(policy: SelectionPolicy | None = None) -> float:
    """Of the planted blink frames whose clean group-mate exists, the fraction
    selected. The blink frame is deliberately NOT Pareto-dominated (it wins on
    aesthetic, loses on awake), so the only thing keeping it out is the blink
    machinery itself -- the exact thing this case gates."""
    policy = policy or DEFAULT_POLICY
    pairs = 3
    pool: list[SelectionCandidate] = []
    blink_ids: list[str] = []
    for index in range(pairs):
        axis = index
        day = 1 + index
        blink = _cand(
            f"pair-{index}-blink",
            0.8,
            captured_utc=_at(day, 12, 0, 0),
            embedding=_unit(axis),
            awake=-0.30,
            smile=0.10,
            aesthetic=0.30,  # beats its mate by > the 0.02 margin: no domination
            composed=0.2,
            per_face=_faces(eyes=0.20),
        )
        clean = _cand(
            f"pair-{index}-clean",
            0.8,
            captured_utc=_at(day, 12, 0, 10),
            embedding=_unit(axis),
            awake=0.30,
            smile=0.10,
            aesthetic=0.25,
            composed=0.2,
            per_face=_faces(eyes=0.90),
        )
        blink_ids.append(blink.media_id)
        pool.extend((blink, clean))
    pool.extend(_fillers(4, start_axis=4, start_day=10))
    result = select(pool, 7, policy=policy)
    chosen = set(result.selected)
    return sum(1 for media_id in blink_ids if media_id in chosen) / pairs


def measure_twin_pair_rate(policy: SelectionPolicy | None = None) -> float:
    """Of every selected pair, the fraction above 0.92 cosine. The twins sit
    at exactly 0.95, three hours apart -- outside the burst window, so shot
    grouping does NOT solve this; only the max_selected_similarity backstop
    (and the redundancy term) stand between them and facing pages."""
    policy = policy or DEFAULT_POLICY
    twin_a = _cand(
        "twin-a", 0.95, captured_utc=_at(1, 9), embedding=_unit(0),
        aesthetic=0.9, awake=0.2, per_face=_faces(0.8),
    )
    twin_b = _cand(
        "twin-b", 0.95, captured_utc=_at(1, 12), embedding=_near(0, 1, 0.95),
        aesthetic=0.9, awake=0.2, per_face=_faces(0.8),
    )
    pool = [twin_a, twin_b] + _fillers(5, start_axis=2, start_day=3, value=0.5)
    result = select(pool, 4, policy=policy)
    by_id = {c.media_id: c for c in pool}
    chosen = sorted(result.selected)
    violating = 0
    total = 0
    for i, first in enumerate(chosen):
        for second in chosen[i + 1:]:
            total += 1
            a, b = by_id[first].embedding, by_id[second].embedding
            if a is not None and b is not None:
                cosine = sum(x * y for x, y in zip(a, b, strict=True))
                if cosine > 0.92:
                    violating += 1
    return violating / total if total else 0.0


def measure_screenshot_selected(policy: SelectionPolicy | None = None) -> float:
    """Of the planted screenshot-gated candidates, the fraction selected. One
    of them is the only photo of a person, because the screenshot gate's
    waiver class is `never` -- a receipt is not a tribute, however scarce the
    person on it."""
    policy = policy or DEFAULT_POLICY
    shots = [
        _cand(
            "shot-receipt", 0.9, captured_utc=_at(1, 8), embedding=_unit(0),
            screenshot_document=0.42,
        ),
        _cand(
            "shot-meme", 0.9, captured_utc=_at(2, 8), embedding=_unit(1),
            screenshot_document=0.35, person_ids=(_PERSON["cy"],),
        ),
    ]
    pool = shots + _fillers(4, start_axis=2, start_day=4)
    result = select(pool, 4, policy=policy)
    chosen = set(result.selected)
    return sum(1 for c in shots if c.media_id in chosen) / len(shots)


def measure_rare_moment_falsely_rejected(
    policy: SelectionPolicy | None = None,
) -> float:
    """1.0 when the isolated unique moment lands in `rejected`, else 0.0. The
    photo sits below the quality floor (0.30 < 0.35) -- a MODERATE soft-floor
    failure -- and is the only candidate within 30 minutes, so the rare-moment
    waiver must keep it alive."""
    policy = policy or DEFAULT_POLICY
    lone = _cand(
        "lone-goodbye", 0.30, captured_utc=_at(5, 9), embedding=_unit(0),
    )
    pool = [lone] + _fillers(4, start_axis=1, start_day=10, value=0.7)
    result = select(pool, 5, policy=policy)
    return 1.0 if any(r.media_id == lone.media_id for r in result.rejected) else 0.0


def _domination_pool() -> tuple[list[SelectionCandidate], list[str], list[str]]:
    """Three shot pairs where one frame Pareto-dominates the other (wins on
    awake AND aesthetic by the perceptual margins, loses nowhere), plus
    distinct context. Returns (pool, dominated_ids, dominator_ids)."""
    pool: list[SelectionCandidate] = []
    dominated_ids: list[str] = []
    dominator_ids: list[str] = []
    for index in range(3):
        axis = index
        day = 1 + index
        # 0.97 within the pair, not 1.0: above shot_similarity (0.93), so the
        # pair is one shot group -- but strictly under 1.0, so the distinctness
        # backstop's `closest < max_selected_similarity` comparison can be
        # relaxed by a test proving this metric bites (at exactly 1.0 the
        # dominated frame is unselectable for a second, unrelated reason).
        worse = _cand(
            f"take-{index}-worse", 0.85,
            captured_utc=_at(day, 15, 0, 0), embedding=_unit(axis),
            awake=0.10, aesthetic=0.30, smile=0.4, per_face=_faces(0.8),
        )
        better = _cand(
            f"take-{index}-better", 0.85,
            captured_utc=_at(day, 15, 0, 5), embedding=_near(axis, 3, 0.97),
            awake=0.30, aesthetic=0.50, smile=0.4, per_face=_faces(0.8),
        )
        dominated_ids.append(worse.media_id)
        dominator_ids.append(better.media_id)
        pool.extend((worse, better))
    pool.extend(_fillers(3, start_axis=4, start_day=10))
    return pool, dominated_ids, dominator_ids


def measure_worse_version_selected(policy: SelectionPolicy | None = None) -> float:
    """Of the planted Pareto-dominated frames, the fraction selected while
    their dominator survives in the same shot group."""
    policy = policy or DEFAULT_POLICY
    pool, dominated_ids, _ = _domination_pool()
    result = select(pool, 6, policy=policy)
    chosen = set(result.selected)
    return sum(1 for m in dominated_ids if m in chosen) / len(dominated_ids)


def measure_clean_take_selected(policy: SelectionPolicy | None = None) -> float:
    """Retention: of the planted dominators, the fraction that actually made
    the album. A selector that dodged `worse_version_selected` by selecting
    NEITHER frame of the shot would pass the error gate and fail this one."""
    policy = policy or DEFAULT_POLICY
    pool, _, dominator_ids = _domination_pool()
    result = select(pool, 6, policy=policy)
    chosen = set(result.selected)
    return sum(1 for m in dominator_ids if m in chosen) / len(dominator_ids)


def _pin_exclude_pool() -> tuple[list[SelectionCandidate], str, str, SelectionPolicy]:
    """A pool where the pin and the exclude both have to fight: the pinned
    photo would fail the quality floor on its own (0.20 < 0.35, plus a cut
    face), and the excluded photo is the pool's best. Returns
    (pool, pinned_id, excluded_id, policy-with-pins)."""
    best = _cand(
        "exclude-best", 0.95, captured_utc=_at(1, 9), embedding=_unit(0),
        aesthetic=0.8, per_face=_faces(0.9),
    )
    pinned = _cand(
        "pin-grainy", 0.20, captured_utc=_at(2, 9, 1), embedding=_unit(1),
        face_cut=True, per_face=_faces(0.7),
    )
    # A neighbour 60 seconds from the pinned photo, so the pin can never be
    # quietly saved by the rare-moment waiver instead of by the pin itself.
    neighbour = _cand(
        "pin-neighbour", 0.6, captured_utc=_at(2, 9, 2), embedding=_unit(2),
        per_face=_faces(0.8),
    )
    pool = [best, pinned, neighbour] + _fillers(4, start_axis=3, start_day=5)
    policy = replace(
        DEFAULT_POLICY,
        pinned_media_ids=frozenset({pinned.media_id}),
        excluded_media_ids=frozenset({best.media_id}),
    )
    return pool, pinned.media_id, best.media_id, policy


def measure_pin_violated(policy: SelectionPolicy | None = None) -> float:
    """1.0 when the pinned photo is absent from the selection, else 0.0.
    `policy` overrides the built-in pin policy (that is how the bite test
    strips the pin and proves the metric moves)."""
    pool, pinned_id, _, pin_policy = _pin_exclude_pool()
    result = select(pool, 4, policy=policy or pin_policy)
    return 0.0 if pinned_id in result.selected else 1.0


def measure_exclude_violated(policy: SelectionPolicy | None = None) -> float:
    """1.0 when the excluded photo appears in the selection, else 0.0."""
    pool, _, excluded_id, pin_policy = _pin_exclude_pool()
    result = select(pool, 4, policy=policy or pin_policy)
    return 1.0 if excluded_id in result.selected else 0.0


def measure_category_presets_change_winner(
    policy: SelectionPolicy | None = None,
) -> float:
    """Retention, deterministic: with target 1, candidate A (best aesthetic)
    wins while B is uncategorised, and B (best worst-face eyes) wins once it
    is a `group` shot -- the preset's weight_face_eyes=0.50 is what flips it.
    1.0 when both sub-runs land exactly there, else 0.0."""
    policy = policy or DEFAULT_POLICY

    # The percentile arithmetic these numbers pin (pool of six, so ranks move
    # in sixths): A leads B on aesthetic by 0.500 of a percentile, B leads A
    # on worst-face eyes by 0.834. Default weights: A by
    # 0.55*0.500 - 0.30*0.834 = +0.025. Group preset (weight_face_eyes 0.50):
    # B by 0.50*0.834 - 0.55*0.500 = +0.142. Small margins, but exact -- the
    # pools are fixed constants and the selector is deterministic.
    def pool(b_category: str | None) -> list[SelectionCandidate]:
        a = _cand(
            "preset-a", 0.8, captured_utc=_at(1, 10), embedding=_unit(0),
            aesthetic=0.60, awake=0.2, per_face=_faces(0.55),
        )
        b = _cand(
            "preset-b", 0.8, captured_utc=_at(2, 10), embedding=_unit(1),
            aesthetic=0.20, awake=0.2, per_face=_faces(0.95),
            category=b_category,
        )
        return [a, b] + _fillers(4, start_axis=2, start_day=4, value=0.5, eyes=0.6)

    default_run = select(pool(None), 1, policy=policy)
    preset_run = select(pool("group"), 1, policy=policy)
    flipped = (
        default_run.selected == (_mid("preset-a"),)
        and preset_run.selected == (_mid("preset-b"),)
    )
    return 1.0 if flipped else 0.0


# ---------------------------------------------------------------------------
# Suite assembly
# ---------------------------------------------------------------------------

#: case_id -> (measure function, category, direction, expected).
#: Critical errors are rates expected to be EXACTLY 0; retention cases are
#: rates expected to be exactly 1. Both are enforced absolutely by
#: `enforce_expected` -- the gate fails on any departure with no baseline
#: movement involved.
CRITICAL_CASES = {
    "selection_blink_when_clean_exists": measure_blink_when_clean_exists,
    "selection_twin_pair_rate": measure_twin_pair_rate,
    "selection_screenshot_selected": measure_screenshot_selected,
    "selection_rare_moment_falsely_rejected": measure_rare_moment_falsely_rejected,
    "selection_worse_version_selected": measure_worse_version_selected,
    "selection_pin_violated": measure_pin_violated,
    "selection_exclude_violated": measure_exclude_violated,
}
RETENTION_CASES = {
    "selection_clean_take_selected": measure_clean_take_selected,
    "selection_category_presets_change_winner": measure_category_presets_change_winner,
}
CASE_EXPECTATIONS: dict[str, tuple[str, str, float]] = {
    **{
        case_id: ("selection_critical", "lower_is_better", 0.0)
        for case_id in CRITICAL_CASES
    },
    **{
        case_id: ("selection_retention", "higher_is_better", 1.0)
        for case_id in RETENTION_CASES
    },
}


def run_benchmark() -> dict[str, float]:
    """Every case, measured once. Deterministic: same code, same numbers."""
    measures = {**CRITICAL_CASES, **RETENTION_CASES}
    return {
        case_id: round(measures[case_id](), 6) for case_id in sorted(measures)
    }


def _digest(payload: str) -> str:
    """BLAKE3 of a string -- the contract's Blake3Hash, never substituted.

    Same rule as memory_engine_face.eval: a `weights_blake3` field holding
    some other hash would validate, compare equal to itself, and be a lie in
    the one place the harness uses to decide whether two runs are the same
    run. Only regeneration needs blake3; the committed gate file runs without.
    """
    try:
        from blake3 import blake3  # noqa: PLC0415
    except ImportError as missing:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "blake3 is required to regenerate the gate file; the committed "
            "gate file can be read and run without it"
        ) from missing
    return blake3(payload.encode("utf-8")).hexdigest()


def _model_pins() -> list[dict[str, str]]:
    """Pins for the thing under test: the selection algorithm at the default
    policy. No weights exist, so `weights_blake3` digests the algorithm
    identity and `config_blake3` digests the operating point -- the DEFAULT
    policy's own field values, so editing a default knob changes the pin and
    the harness refuses to compare across the edit."""
    import dataclasses  # noqa: PLC0415

    knobs = ",".join(
        f"{field.name}={getattr(DEFAULT_POLICY, field.name)!r}"
        for field in sorted(dataclasses.fields(DEFAULT_POLICY), key=lambda f: f.name)
    )
    return [
        {
            "model_id": MODEL_ID,
            "version": ALGORITHM_VERSION,
            "weights_blake3": _digest(f"{MODEL_ID}:{ALGORITHM_VERSION}"),
            "config_blake3": _digest(f"selection-policy-defaults:{knobs}"),
        }
    ]


def build_gate_document(*, as_of: str) -> dict[str, object]:
    """Assemble the gate file from three fresh measurements.

    Baseline and candidate are identical by construction, so every delta is
    zero; the teeth are `expected` + `enforce_expected` (an absolute rule per
    case) and the freshness test in tests/test_selection_gate.py, which
    re-runs this builder and refuses a committed file the code has moved
    from. Samples are three REAL runs, not one run copied three times, so
    `min_repeats: 3` checks determinism rather than asserting it.
    """
    runs = [run_benchmark() for _ in range(3)]
    pins = _model_pins()

    cases = []
    results = []
    for case_id in sorted(CASE_EXPECTATIONS):
        category, direction, expected = CASE_EXPECTATIONS[case_id]
        inputs_digest = _digest(
            f"selection-synthetic-pool:{case_id}:{ALGORITHM_VERSION}"
        )
        cases.append(
            {
                "case_id": case_id,
                "category": category,
                "inputs_digest": inputs_digest,
                "baseline_models": pins,
                "metric": {
                    "name": "error_rate"
                    if direction == "lower_is_better"
                    else "success_rate",
                    "direction": direction,
                },
                "expected": expected,
                "description": (
                    "synthetic selection pool; a critical error committed at any "
                    "rate fails the gate"
                    if category == "selection_critical"
                    else "synthetic selection pool; the selector must keep doing this"
                ),
            }
        )
        results.append(
            {
                "case_id": case_id,
                "models": pins,
                "inputs_digest": inputs_digest,
                "samples": [run[case_id] for run in runs],
            }
        )

    return {
        "as_of": as_of,
        "description": (
            "Selection v3 critical-error gate. Measures "
            "packages/album-engine selection on synthetic pools with known "
            "ground truth: every error case is a rate expected to be exactly "
            "0, enforced absolutely. Regenerate with `python3 -m "
            "memory_engine_eval.selection_bench --as-of <date> --write "
            "gates/selection-critical-errors.gate.json`."
        ),
        "policy": {
            "categories": ["selection_critical", "selection_retention"],
            "min_cases_per_category": 2,
            "min_repeats": 3,
            "enforce_expected": True,
        },
        "suite": {"cases": cases},
        "baseline": results,
        "candidate": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="ISO date for the gate file")
    parser.add_argument(
        "--write", type=Path, default=None, help="write the gate file here"
    )
    args = parser.parse_args(argv)
    document = build_gate_document(as_of=args.as_of)
    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if args.write is None:
        sys.stdout.write(rendered)
    else:
        args.write.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
