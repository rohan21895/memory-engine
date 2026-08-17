"""The regression gate that stands between a model swap and a quality drop.

CLAUDE.md hard rule 7: "Model swaps are gated by the eval harness in CI."
This module is that gate. It takes a benchmark suite, a baseline run, a
candidate run and a written policy, and returns PASS or FAIL with every reason
enumerated. It does not decide by vibe and it does not average its way to an
answer.

SIX DECISIONS, AND WHAT THE ALTERNATIVE BREAKS

1. THE MEAN CANNOT PASS A RUN. CATEGORIES CAN FAIL ONE.
   The standard eval harness compares aggregate scores. That harness ships the
   model that gains three points on travel photos while collapsing on low-light
   indoor family shots, because 400 travel cases outvote 12 family cases and
   the mean goes up. So:

     * every case belongs to exactly one of the declared benchmark categories
       (build plan §6: Indian weddings, festivals, GoPro/adventure, drone,
       baby/family, travel);
     * each category is gated on its own -- absolute floor, mean drop, and the
       fraction of its cases that regressed;
     * the overall number is reported but is the WEAKEST gate, and it is the
       mean of the category means, not the mean of the cases. Case-weighting
       the overall figure would re-create the exact drowning effect the
       per-category gates exist to prevent, one level up.

   A run passes only when nothing failed. There is no score to trade against.

2. A COMPARISON ACROSS A DIGEST MISMATCH IS REFUSED, NOT REPORTED.
   ModelRef carries weights_blake3 AND config_blake3 because weights alone do
   not pin behaviour -- input size, normalisation constants, score threshold,
   NMS IoU and the alignment template all live in the config and change every
   downstream decision while the weights hash stays byte-identical
   (common.schema.json, ModelRef.config_blake3). A "delta" measured against a
   baseline that a different model produced is not a delta; it is two unrelated
   numbers subtracted. So a mismatch raises `ComparisonRefused` rather than
   producing a report. An exception is deliberate: a refusal that came back as
   a FAIL verdict could be waived, argued with, or read as a quality signal.
   It is none of those. It is "this measurement does not exist".

   The same applies to the inputs: `inputs_digest` must match on both sides.
   Same model, different benchmark data, is equally meaningless.

3. IDENTITY IS THE DIGESTS, NOT THE VERSION STRING.
   ModelRef's own comment says the weights hash exists because "'the same
   version' of a HuggingFace repo has changed weights under people before". The
   converse holds too: a re-tagged release with byte-identical weights and
   config IS the same model. So identity is (model_id, weights_blake3,
   config_blake3); `version` is provenance that gets reported, never compared.
   Comparing version strings would refuse legitimate re-tags and, worse, would
   accept two different weight files that happen to share a tag.

4. SCORES ARE UNITS, AND DIRECTION IS DECLARED.
   Every case score is in [0,1], matching the contract's Unit convention. Two
   independent silent failures are being blocked here. Values outside [0,1] --
   a beat-alignment error handed over raw as 47.0 ms -- would dominate any mean
   it entered and make a category look fine while it is unmeasurable; that
   raises. And a lower-is-better metric (false-match rate) entered without a
   direction would read a regression as an improvement; that is why `Metric`
   carries `direction` and everything downstream is computed on `goodness`,
   which is uniformly higher-is-better.

5. A CASE THAT DID NOT RUN IS NOT A CASE THAT PASSED.
   Three shapes of absence, three different answers:

     * baseline has it, candidate does not -> MISSING_CANDIDATE, always a
       failure. Quietly dropping the hardest case is the cheapest way to make
       a suite green, so this is the one thing that can never be waived.
     * candidate has it, baseline does not -> NO_BASELINE. Not a delta, not a
       pass. Under the default policy it fails: a new case must be baselined in
       a separate, non-gating run before it is allowed to gate anything.
       `allow_new_cases` exists for that first run and is a visible choice.
     * neither -> the case is unrepresented; it counts against the category's
       representation check, not against a delta.

   Cases missing on one side are excluded from BOTH means. Including a case in
   the candidate mean that the baseline mean cannot contain shifts the delta by
   an amount unrelated to model quality -- the most convincing wrong number
   this file could produce.

6. NONDETERMINISM IS A FAILURE, NOT NOISE TO AVERAGE AWAY.
   Determinism is a product requirement (CLAUDE.md hard rule 3). A result may
   carry repeats; if they disagree by more than `nondeterminism_tolerance`
   (default 0.0) the case is NONDETERMINISTIC and fails, because a delta
   computed from a number the model cannot reproduce is noise wearing a
   decimal point. The no-op comparison is the sharpest form of this check: when
   the candidate digests equal the baseline digests, every delta must be zero.
   Anything else means something outside the model moved, and the whole run is
   untrustworthy.

WAIVERS: HOW A HUMAN SAYS "THIS ONE IS ACCEPTABLE" WITHOUT WRITING A BYPASS
   A waiver names exactly one case_id -- no globs, no categories, no wildcards.
   It is bound to the exact (baseline identity, candidate identity) pair it was
   reviewed against, so it evaporates the moment either side changes: a waiver
   written for candidate v2 does not silently cover v3. It caps the regression
   it forgives (`max_drop`, itself capped by MAX_WAIVER_DROP), so it cannot
   grow into "this case is exempt". It carries an approver, a reason, and a
   mandatory expiry no further than MAX_WAIVER_HORIZON_DAYS from approval.

   And it forgives one thing only: a per-case relative regression. It does not
   touch the category mean, the category regressed-fraction, the absolute
   expected outcome, a missing result, or nondeterminism. That is the property
   that keeps waivers from accumulating into a bypass: three waived cases in
   one category still sink that category's mean, and the only way out of that
   is to change the declared policy, which is a reviewed, visible edit.

DETERMINISM OF THE HARNESS ITSELF
   Same inputs, same report, byte for byte. Cases iterate in sorted id order,
   categories in sorted name order, failures sort by (code, scope). Means use
   math.fsum, which is exactly rounded and therefore order-independent, so no
   sort is needed to make addition reproducible. Every reported float is
   quantised to six decimals, and deltas are computed FROM the quantised
   figures so the printed delta always equals the difference of the printed
   values -- a report whose own arithmetic does not close is a report nobody
   can check.

THE GATE IS A COMMAND, AND IT HAS THREE ANSWERS, NOT TWO
   A library that returns a verdict is not a gate; CI reads exit codes. So this
   module is also a command (`python3 -m memory_engine_eval.harness GATE.json`)
   and the answers it can give are numbered:

     0  EXIT_PASS     the comparison ran and nothing failed.
     1  EXIT_FAIL     the comparison ran and something failed. A quality
                      signal: argue with it, waive a case, or fix the model.
     2  EXIT_REFUSED  there is no comparison. `ComparisonRefused` (decision 2)
                      came back: mismatched digests, mixed model sets, unpinned
                      weights, different inputs. NOT a quality signal, and
                      never green.
     3  EXIT_USAGE    the gate could not even be set up: a malformed suite,
                      policy, result set or gate file (`SuiteError`), or an
                      unreadable path. Nothing was measured and nothing about
                      the model was learned.

   Two of those matter more than the rest. `models/policy/digest.py` already
   had to learn this once and says it in a comment: "'I could not check' and 'I
   checked and it was fine' must not share an exit code." The same applies at
   the other end -- "I could not check" and "I checked and it got worse" must
   not share one either, because a refusal that arrives as a FAIL gets treated
   like a FAIL: waived, argued with, or overridden by whoever is in a hurry. So
   `Verdict` carries SKIP, `evaluate` still raises rather than returning it
   (a refusal must not be a value a policy can forgive), and the translation
   from exception to exit code happens exactly once, in `run_gate`.

   Non-zero is non-zero: 2 and 3 fail CI just as 1 does. The distinction is not
   about severity, it is about what a human should go and do.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import NoReturn

__all__ = [
    "BENCHMARK_CATEGORIES",
    "BaselineDigestMismatch",
    "BenchmarkCase",
    "BenchmarkSuite",
    "CaseComparison",
    "CaseResult",
    "CaseStatus",
    "CategoryReport",
    "ComparisonRefused",
    "Direction",
    "EXIT_FAIL",
    "EXIT_PASS",
    "EXIT_REFUSED",
    "EXIT_USAGE",
    "Failure",
    "GateInput",
    "GateOutcome",
    "GatePolicy",
    "GateReport",
    "HarnessError",
    "InputsDigestMismatch",
    "MAX_WAIVER_DROP",
    "MAX_WAIVER_HORIZON_DAYS",
    "Metric",
    "ModelPin",
    "ModelSet",
    "ModelSetMismatch",
    "SuiteError",
    "UnknownCase",
    "UnpinnedModel",
    "Verdict",
    "Waiver",
    "evaluate",
    "format_outcome",
    "format_report",
    "load_gate_file",
    "load_gate_input",
    "main",
    "run_gate",
    "run_gate_file",
]

# The gate's answers, as numbers, because CI reads numbers. See "THE GATE IS A
# COMMAND" in the module docstring for the argument. These are part of the
# module's contract with CI: changing one silently changes what a red build
# means.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_REFUSED = 2
EXIT_USAGE = 3


# The benchmark libraries named in build plan §6. Declared here so a typo in a
# case's category is caught as `unknown_category` rather than quietly creating a
# seventh bucket -- which would also silently remove that case from the real
# category it was meant to defend.
BENCHMARK_CATEGORIES: tuple[str, ...] = (
    "baby_family",
    "drone",
    "festivals",
    "gopro_adventure",
    "indian_weddings",
    "travel",
)

# A waiver may forgive at most this much goodness on a [0,1] scale. Past this
# the honest action is to move the baseline deliberately (a reviewed change to
# committed baseline results), not to annotate the gate.
MAX_WAIVER_DROP = 0.10

# A waiver may not outlive this many days from its approval date. Without a
# horizon, "expires_on: 2099-01-01" is a permanent bypass wearing an expiry
# field.
MAX_WAIVER_HORIZON_DAYS = 90

# Mirrors of contracts/schemas/common.schema.json Blake3Hash and Slug. The
# patterns are written with the contract's own anchors so the two read as the
# same rule, but they are only ever applied with `fullmatch`, never `match`:
# Python's `$` also matches immediately BEFORE a final newline, so `.match()`
# accepts "a"*64 + "\n" as a BLAKE3 digest and "travel_0\n" as a case_id. A
# digest read with `open(path).read()` instead of `.read().strip()` would then
# be a 65-character "digest" that compares unequal to the same digest without
# the newline -- BaselineDigestMismatch, a refusal, chased as a stale-baseline
# ghost. A case_id with a trailing newline forges a second line in
# format_report(). `_is_hex64`/`_is_slug` exist so no call site has to remember
# this.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _is_slug(value: object) -> bool:
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class HarnessError(Exception):
    """Base for everything this module raises."""


class SuiteError(HarnessError):
    """The suite, policy, results or waivers are malformed.

    Distinct from ComparisonRefused: this is "you handed me something that is
    not a benchmark", not "these two runs cannot be compared".
    """


class ComparisonRefused(HarnessError):
    """The two runs cannot be meaningfully compared, so no report is produced.

    Deliberately not a FAIL verdict. A FAIL is a quality signal a human can
    argue with or waive. This is the absence of a measurement.
    """


class ModelSetMismatch(ComparisonRefused):
    """Results were produced by weights/config other than the ones pinned."""


class BaselineDigestMismatch(ModelSetMismatch):
    """A baseline result disagrees with the pin its case declares."""


class InputsDigestMismatch(ComparisonRefused):
    """A run scored different benchmark inputs than the case declares."""


class UnpinnedModel(ComparisonRefused):
    """A model in the comparison has a null weights or config digest.

    ModelRef permits nulls for development mode. A gate is release mode: an
    unpinned run cannot be reproduced, so it cannot be evidence for anything.
    """


class UnknownCase(ComparisonRefused):
    """A result refers to a case the suite does not contain.

    Ignoring the stray result would mean silently comparing against a different
    suite than the one under review.
    """


# --------------------------------------------------------------------------
# Model identity
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPin:
    """One model, pinned hard enough to reproduce.

    Mirrors contracts/schemas/common.schema.json ModelRef. Nulls are accepted
    at construction because the contract permits them in development mode; they
    are refused at comparison time, where they mean "not reproducible".
    """

    model_id: str
    version: str
    weights_blake3: str | None
    config_blake3: str | None

    def __post_init__(self) -> None:
        if not _is_slug(self.model_id):
            raise SuiteError(f"model_id is not a Slug: {self.model_id!r}")
        if not self.version:
            raise SuiteError(f"{self.model_id}: version must be non-empty")
        for name in ("weights_blake3", "config_blake3"):
            digest = getattr(self, name)
            if digest is not None and not _is_hex64(digest):
                raise SuiteError(f"{self.model_id}: {name} is not a BLAKE3 hex digest")

    @property
    def identity(self) -> tuple[str, str | None, str | None]:
        """What makes two pins the same model.

        `version` is excluded on purpose -- see decision 3 in the module
        docstring. A tag is a label; the digests are the model.
        """
        return (self.model_id, self.weights_blake3, self.config_blake3)

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "weights_blake3": self.weights_blake3,
            "config_blake3": self.config_blake3,
        }


@dataclass(frozen=True)
class ModelSet:
    """Every model whose output the run depended on.

    A set rather than a single pin because a real swap changes one component of
    several -- new SigLIP weights while SCRFD stays put -- and a harness that
    only pinned "the model" would compare across a silently changed detector.
    """

    pins: tuple[ModelPin, ...]

    def __init__(self, pins: Iterable[ModelPin]) -> None:
        ordered = tuple(sorted(pins, key=lambda pin: pin.model_id))
        if not ordered:
            raise SuiteError("a ModelSet must pin at least one model")
        seen: set[str] = set()
        for pin in ordered:
            if pin.model_id in seen:
                raise SuiteError(f"duplicate model_id in ModelSet: {pin.model_id}")
            seen.add(pin.model_id)
        object.__setattr__(self, "pins", ordered)

    @property
    def identity(self) -> tuple[tuple[str, str | None, str | None], ...]:
        # `pins` is already sorted by model_id in __init__, so this tuple is a
        # stable key: two sets built from the same pins in different orders
        # compare equal.
        return tuple(pin.identity for pin in self.pins)

    def require_pinned(self, role: str) -> None:
        """Refuse a set that cannot be reproduced.

        config_blake3 is required as hard as weights_blake3. A component with
        genuinely no config file digests its empty config -- an explicit "there
        is no config" -- because the alternative is a null on both sides, under
        which a config change is invisible to the identity check and the gate
        compares two different models while reporting a delta.
        """
        for pin in self.pins:
            if pin.weights_blake3 is None:
                raise UnpinnedModel(
                    f"{role} model {pin.model_id!r} has no weights_blake3; "
                    "an unpinned run cannot gate a swap"
                )
            if pin.config_blake3 is None:
                raise UnpinnedModel(
                    f"{role} model {pin.model_id!r} has no config_blake3; "
                    "weights alone do not pin behaviour"
                )

    def describe(self) -> str:
        return ", ".join(f"{pin.model_id}@{pin.version}" for pin in self.pins)

    def to_dict(self) -> list[dict[str, object]]:
        return [pin.to_dict() for pin in self.pins]


# --------------------------------------------------------------------------
# Suite
# --------------------------------------------------------------------------


class Direction(Enum):
    """Which way is better for a metric.

    Without this, a false-match rate rising from 0.01 to 0.04 reads as a
    +0.03 improvement and the swap ships.
    """

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


@dataclass(frozen=True)
class Metric:
    """What a case measures and which way is good."""

    name: str
    direction: Direction

    def __post_init__(self) -> None:
        if not _is_slug(self.name):
            raise SuiteError(f"metric name is not a Slug: {self.name!r}")

    def goodness(self, value: float) -> float:
        """Map a raw metric value onto a uniformly higher-is-better [0,1].

        Everything downstream -- deltas, means, floors, waiver caps -- works on
        goodness, so direction is handled exactly once, here.
        """
        if self.direction is Direction.HIGHER_IS_BETTER:
            return _quantise(value)
        return _quantise(1.0 - value)


class CaseStatus(Enum):
    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    REGRESSED = "regressed"
    NO_BASELINE = "no_baseline"
    MISSING_CANDIDATE = "missing_candidate"
    NONDETERMINISTIC = "nondeterministic"
    UNRUN = "unrun"


@dataclass(frozen=True)
class BenchmarkCase:
    """One benchmark: what to run, what produced the baseline, what is expected.

    `baseline_models` is part of the CASE, not of the stored results, because
    it is the assertion under review: "these results were produced by these
    digests". A stored result that disagrees with it is a stale baseline, and
    finding that out is the entire point of writing it down twice.
    """

    case_id: str
    category: str
    inputs_digest: str
    baseline_models: ModelSet
    metric: Metric
    expected: float
    description: str = ""

    def __post_init__(self) -> None:
        if not _is_slug(self.case_id):
            raise SuiteError(f"case_id is not a Slug: {self.case_id!r}")
        if not _is_slug(self.category):
            raise SuiteError(f"category is not a Slug: {self.category!r}")
        if not _is_hex64(self.inputs_digest):
            raise SuiteError(f"{self.case_id}: inputs_digest is not a BLAKE3 hex digest")
        _require_unit(f"{self.case_id}.expected", self.expected)

    @property
    def expected_goodness(self) -> float:
        return self.metric.goodness(self.expected)

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "inputs_digest": self.inputs_digest,
            "baseline_models": self.baseline_models.to_dict(),
            "metric": self.metric.name,
            "direction": self.metric.direction.value,
            "expected": self.expected,
            "description": self.description,
        }


@dataclass(frozen=True)
class BenchmarkSuite:
    """The declared set of cases. Order-independent; iteration is by case_id."""

    cases: tuple[BenchmarkCase, ...]

    def __init__(self, cases: Iterable[BenchmarkCase]) -> None:
        ordered = tuple(sorted(cases, key=lambda case: case.case_id))
        if not ordered:
            raise SuiteError("a benchmark suite must contain at least one case")
        seen: set[str] = set()
        for case in ordered:
            if case.case_id in seen:
                raise SuiteError(f"duplicate case_id in suite: {case.case_id}")
            seen.add(case.case_id)
        object.__setattr__(self, "cases", ordered)

    @property
    def by_id(self) -> dict[str, BenchmarkCase]:
        return {case.case_id: case for case in self.cases}


@dataclass(frozen=True)
class CaseResult:
    """What a run actually measured for one case.

    `samples` is a sequence because one observation cannot tell you whether the
    number is reproducible. One sample is allowed (and reported as such); a
    policy that wants proof of determinism sets `min_repeats`.
    """

    case_id: str
    models: ModelSet
    inputs_digest: str
    samples: tuple[float, ...]

    def __init__(
        self,
        case_id: str,
        models: ModelSet,
        inputs_digest: str,
        samples: Sequence[float] | float,
    ) -> None:
        if isinstance(samples, bool):
            # Caught here rather than by _require_unit because tuple(True) would
            # raise TypeError first, and a TypeError from inside a gate reads as
            # a harness bug rather than as bad input.
            raise SuiteError(f"{case_id}: samples must be numbers in [0,1], got a bool")
        values = (samples,) if isinstance(samples, (int, float)) else tuple(samples)
        if not values:
            raise SuiteError(f"{case_id}: a result must carry at least one sample")
        for index, value in enumerate(values):
            _require_unit(f"{case_id}.samples[{index}]", value)
        if not _is_hex64(inputs_digest):
            raise SuiteError(f"{case_id}: inputs_digest is not a BLAKE3 hex digest")
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "models", models)
        object.__setattr__(self, "inputs_digest", inputs_digest)
        object.__setattr__(self, "samples", tuple(float(v) for v in values))

    @property
    def value(self) -> float:
        """The reported measurement: the mean of the repeats.

        math.fsum is exactly rounded, so the mean does not depend on the order
        the repeats arrived in -- a permutation of the same observations gives
        the same number, which is what makes the report reproducible when a
        runner emits repeats concurrently.
        """
        return _quantise(math.fsum(self.samples) / len(self.samples))

    @property
    def spread(self) -> float:
        """max - min across repeats. Computed on the RAW samples.

        Quantising first would hide jitter below the sixth decimal, which is
        still jitter: it is the difference between "the model is deterministic"
        and "the model is deterministic to the precision I happened to print".
        """
        return max(self.samples) - min(self.samples)


# --------------------------------------------------------------------------
# Waivers
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Waiver:
    """One human, one case, one model pair, one bounded regression, one expiry.

    Every field here exists to stop a waiver becoming a bypass. See the module
    docstring for the full argument.
    """

    case_id: str
    baseline_models: ModelSet
    candidate_models: ModelSet
    max_drop: float
    reason: str
    approved_by: str
    approved_on: date
    expires_on: date

    def __post_init__(self) -> None:
        if not _is_slug(self.case_id):
            raise SuiteError(f"waiver case_id is not a Slug: {self.case_id!r}")
        if self.max_drop <= 0:
            raise SuiteError(
                f"waiver for {self.case_id}: max_drop must be positive; a waiver "
                "that forgives nothing is a comment, not a waiver"
            )
        if self.max_drop > MAX_WAIVER_DROP:
            raise SuiteError(
                f"waiver for {self.case_id}: max_drop {self.max_drop} exceeds the "
                f"{MAX_WAIVER_DROP} cap; move the baseline deliberately instead"
            )
        if not self.reason.strip():
            raise SuiteError(f"waiver for {self.case_id}: reason must be non-empty")
        if not self.approved_by.strip():
            raise SuiteError(f"waiver for {self.case_id}: approved_by must be non-empty")
        if self.expires_on <= self.approved_on:
            raise SuiteError(
                f"waiver for {self.case_id}: expires_on must be after approved_on"
            )
        horizon = (self.expires_on - self.approved_on).days
        if horizon > MAX_WAIVER_HORIZON_DAYS:
            raise SuiteError(
                f"waiver for {self.case_id}: {horizon}-day horizon exceeds the "
                f"{MAX_WAIVER_HORIZON_DAYS}-day cap"
            )

    def covers(
        self,
        *,
        baseline: ModelSet,
        candidate: ModelSet,
    ) -> bool:
        """Whether this waiver was reviewed against exactly this comparison."""
        return (
            self.baseline_models.identity == baseline.identity
            and self.candidate_models.identity == candidate.identity
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "max_drop": self.max_drop,
            "reason": self.reason,
            "approved_by": self.approved_by,
            "approved_on": self.approved_on.isoformat(),
            "expires_on": self.expires_on.isoformat(),
        }


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GatePolicy:
    """The stated rules. Changing a gate means editing this, in review.

    Defaults are chosen to be strict where a mistake is catastrophic and merely
    tight where it is noise:

    * `case_regression_tolerance` decides what counts as a regression at all
      (IMPROVED/UNCHANGED/REGRESSED). It fails nothing on its own, but do not
      read that as "it is decorative": the classification it produces is what
      `max_regressed_fraction_per_category` counts, so loosening it hides cases
      from that gate. This used to be documented as "it does not pass or fail
      anything", which was false in a way that cost the per-case cap: the cap
      below was only checked on a case already classified REGRESSED, so a
      policy with `case_regression_tolerance` above `max_case_drop` made the
      cap unreachable and a 0.09 drop against a documented 0.05 cap returned
      PASS with an empty failure list. Two things stop that now -- the cap is
      no longer gated on the classification, and `__post_init__` refuses a
      policy whose tolerance exceeds its cap, because those two numbers
      describe the same axis and only one ordering of them is coherent.
    * `max_case_drop` is the hard per-case cap. A single case falling off a
      cliff fails the run no matter how good the aggregate looks, and no matter
      how the tolerance above chose to label it.
    * `max_category_mean_drop` is deliberately small but NOT zero. A category
      mean is computed over a handful of cases, and a zero-tolerance rule would
      fail every swap on ordinary movement. A gate that always fails gets
      switched off, which is strictly worse than a gate with a stated tolerance.
    * `max_regressed_fraction_per_category` catches what the mean cannot: half
      a category sliding while two big wins hold the average up.
    * `max_overall_mean_drop` is the weakest gate and is computed on the mean of
      category means, never on the mean of cases. Note what that implies: the
      overall figure cannot fall further than the worst category does, so with
      both caps at the same value this gate can never fire on its own -- any
      overall failure is already a category failure. It is kept because it is
      the gate that expresses "categories may trade against each other, the
      suite may not lose ground", which is a policy someone will want: set
      `max_category_mean_drop` loose and this one tight.
    """

    categories: tuple[str, ...] = BENCHMARK_CATEGORIES
    category_floors: Mapping[str, float] = None  # type: ignore[assignment]
    case_regression_tolerance: float = 0.01
    max_case_drop: float = 0.05
    max_category_mean_drop: float = 0.005
    max_regressed_fraction_per_category: float = 0.5
    max_overall_mean_drop: float = 0.005
    nondeterminism_tolerance: float = 0.0
    min_repeats: int = 1
    min_cases_per_category: int = 1
    allow_new_cases: bool = False
    enforce_expected: bool = True

    def __post_init__(self) -> None:
        if self.category_floors is None:
            object.__setattr__(self, "category_floors", {})
        if not self.categories:
            raise SuiteError("policy must declare at least one category")
        if len(set(self.categories)) != len(self.categories):
            raise SuiteError("policy categories contain a duplicate")
        # Sorted so two policies written in different orders produce the same
        # report digest.
        object.__setattr__(self, "categories", tuple(sorted(self.categories)))
        object.__setattr__(self, "category_floors", dict(sorted(self.category_floors.items())))
        for name, floor in self.category_floors.items():
            if name not in self.categories:
                raise SuiteError(f"floor declared for undeclared category {name!r}")
            _require_unit(f"category_floors[{name}]", floor)
        for name in (
            "case_regression_tolerance",
            "max_case_drop",
            "max_category_mean_drop",
            "max_overall_mean_drop",
            "nondeterminism_tolerance",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise SuiteError(f"policy.{name} must be a number")
            if not math.isfinite(value) or value < 0:
                raise SuiteError(f"policy.{name} must be a finite non-negative number")
        # The tolerance and the cap measure the same thing -- how far one case
        # fell -- so a policy that sets the tolerance above the cap is not a
        # loose policy, it is an incoherent one: it declares a cliff edge and
        # then declares that nothing which falls off it counts as a fall.
        # Rejected rather than clamped, because silently clamping a number
        # somebody wrote down is how a policy stops describing the gate.
        if self.case_regression_tolerance > self.max_case_drop:
            raise SuiteError(
                "policy.case_regression_tolerance "
                f"({self.case_regression_tolerance}) exceeds policy.max_case_drop "
                f"({self.max_case_drop}); a drop larger than the hard per-case cap "
                "would be classified as UNCHANGED, so it would also be invisible to "
                "max_regressed_fraction_per_category. Loosen the cap deliberately or "
                "keep the tolerance under it"
            )
        if not 0 < self.max_regressed_fraction_per_category <= 1:
            raise SuiteError("policy.max_regressed_fraction_per_category must be in (0,1]")
        if self.min_repeats < 1:
            raise SuiteError("policy.min_repeats must be at least 1")
        if self.min_cases_per_category < 1:
            raise SuiteError("policy.min_cases_per_category must be at least 1")

    def to_dict(self) -> dict[str, object]:
        return {
            "categories": list(self.categories),
            "category_floors": dict(self.category_floors),
            "case_regression_tolerance": self.case_regression_tolerance,
            "max_case_drop": self.max_case_drop,
            "max_category_mean_drop": self.max_category_mean_drop,
            "max_regressed_fraction_per_category": self.max_regressed_fraction_per_category,
            "max_overall_mean_drop": self.max_overall_mean_drop,
            "nondeterminism_tolerance": self.nondeterminism_tolerance,
            "min_repeats": self.min_repeats,
            "min_cases_per_category": self.min_cases_per_category,
            "allow_new_cases": self.allow_new_cases,
            "enforce_expected": self.enforce_expected,
        }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


class Verdict(Enum):
    """What a gate run can say, and what number CI hears.

    SKIP is here because "I could not measure this" needs a name and a number.
    It is NEVER produced by `evaluate`, which still raises `ComparisonRefused`
    (decision 2): a refusal that arrived as a returned verdict could be stored
    in a report, waived by a policy, or compared against a threshold, and it is
    none of those things. SKIP is produced exactly once, by `run_gate`, at the
    boundary where an exception has to become an exit code.
    """

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"

    @property
    def exit_code(self) -> int:
        """The shell status for this verdict.

        Three verdicts, three codes. Collapsing SKIP onto EXIT_FAIL is the bug
        this property exists to make impossible to write by accident.
        """
        if self is Verdict.PASS:
            return EXIT_PASS
        if self is Verdict.FAIL:
            return EXIT_FAIL
        return EXIT_REFUSED


@dataclass(frozen=True)
class Failure:
    """One reason the run failed, in a form a test can assert on.

    A code rather than a sentence: failure messages get reworded, and a CI gate
    whose assertions break on rewording gets its assertions loosened.
    """

    code: str
    scope: str
    detail: str

    def sort_key(self) -> tuple[str, str]:
        return (self.code, self.scope)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "scope": self.scope, "detail": self.detail}


@dataclass(frozen=True)
class CaseComparison:
    case_id: str
    category: str
    metric: str
    status: CaseStatus
    baseline_value: float | None
    candidate_value: float | None
    baseline_goodness: float | None
    candidate_goodness: float | None
    delta: float | None
    baseline_meets_expected: bool | None
    candidate_meets_expected: bool | None
    spread: float
    baseline_spread: float
    repeats: int
    waived: bool
    waiver: Waiver | None
    notes: tuple[str, ...]

    @property
    def comparable(self) -> bool:
        """Both sides measured, so this case may enter a mean."""
        return self.baseline_goodness is not None and self.candidate_goodness is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "metric": self.metric,
            "status": self.status.value,
            "baseline_value": self.baseline_value,
            "candidate_value": self.candidate_value,
            "baseline_goodness": self.baseline_goodness,
            "candidate_goodness": self.candidate_goodness,
            "delta": self.delta,
            "baseline_meets_expected": self.baseline_meets_expected,
            "candidate_meets_expected": self.candidate_meets_expected,
            "spread": _quantise(self.spread),
            "baseline_spread": _quantise(self.baseline_spread),
            "repeats": self.repeats,
            "waived": self.waived,
            "waiver": self.waiver.to_dict() if self.waiver else None,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class CategoryReport:
    category: str
    case_ids: tuple[str, ...]
    comparable_case_ids: tuple[str, ...]
    baseline_mean: float | None
    candidate_mean: float | None
    mean_delta: float | None
    floor: float | None
    meets_floor: bool | None
    regressed_case_ids: tuple[str, ...]
    waived_case_ids: tuple[str, ...]
    regressed_fraction: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "case_ids": list(self.case_ids),
            "comparable_case_ids": list(self.comparable_case_ids),
            "baseline_mean": self.baseline_mean,
            "candidate_mean": self.candidate_mean,
            "mean_delta": self.mean_delta,
            "floor": self.floor,
            "meets_floor": self.meets_floor,
            "regressed_case_ids": list(self.regressed_case_ids),
            "waived_case_ids": list(self.waived_case_ids),
            "regressed_fraction": self.regressed_fraction,
        }


@dataclass(frozen=True)
class GateReport:
    verdict: Verdict
    baseline_models: ModelSet | None
    candidate_models: ModelSet
    is_no_op: bool
    cases: tuple[CaseComparison, ...]
    categories: tuple[CategoryReport, ...]
    overall_baseline_mean: float | None
    overall_candidate_mean: float | None
    overall_delta: float | None
    case_weighted_candidate_mean: float | None
    failures: tuple[Failure, ...]
    unused_waivers: tuple[str, ...]
    expired_waivers: tuple[str, ...]
    policy: GatePolicy
    evaluated_as_of: date

    def __post_init__(self) -> None:
        # A report is the record of a comparison that happened. SKIP means no
        # comparison happened, so a report carrying it would be a measurement
        # of nothing -- with means, deltas and a digest attached, all of them
        # describing a run that was refused.
        if self.verdict is Verdict.SKIP:
            raise SuiteError(
                "a GateReport cannot carry Verdict.SKIP; a refusal has no report. "
                "Use run_gate(), which turns ComparisonRefused into a SKIP outcome"
            )

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS

    def failure_codes(self) -> tuple[str, ...]:
        return tuple(failure.code for failure in self.failures)

    def to_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict.value,
            "baseline_models": self.baseline_models.to_dict() if self.baseline_models else None,
            "candidate_models": self.candidate_models.to_dict(),
            "is_no_op": self.is_no_op,
            "cases": [case.to_dict() for case in self.cases],
            "categories": [category.to_dict() for category in self.categories],
            "overall_baseline_mean": self.overall_baseline_mean,
            "overall_candidate_mean": self.overall_candidate_mean,
            "overall_delta": self.overall_delta,
            "case_weighted_candidate_mean": self.case_weighted_candidate_mean,
            "failures": [failure.to_dict() for failure in self.failures],
            "unused_waivers": list(self.unused_waivers),
            "expired_waivers": list(self.expired_waivers),
            "policy": self.policy.to_dict(),
            "evaluated_as_of": self.evaluated_as_of.isoformat(),
        }

    def digest(self) -> str:
        """Content address of the report, for attaching to a CI run.

        Prefixed with the algorithm and NOT called a BLAKE3 id: the rest of the
        system content-addresses with BLAKE3, which is not in the standard
        library, and a digest labelled as something it is not is exactly the
        kind of quiet lie this repo keeps finding.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def evaluate(
    suite: BenchmarkSuite,
    baseline: Sequence[CaseResult],
    candidate: Sequence[CaseResult],
    policy: GatePolicy | None = None,
    waivers: Sequence[Waiver] = (),
    *,
    as_of: date,
) -> GateReport:
    """Compare a candidate run against a baseline run and return the verdict.

    `as_of` is required rather than defaulting to today. A gate whose result
    depends on an implicit clock is not reproducible: the same run would pass on
    Tuesday and fail on Wednesday when a waiver expired, and nothing in the
    report would say why.

    Raises ComparisonRefused (never returns a FAIL) when the two runs are not
    comparable at all.
    """
    policy = policy or GatePolicy()
    cases = suite.by_id

    baseline_by_case = _index_results(baseline, "baseline", cases)
    candidate_by_case = _index_results(candidate, "candidate", cases)

    baseline_models = _single_model_set(baseline_by_case, "baseline")
    # One check, not two. A belt-and-braces second guard on the same condition
    # would mask a deletion of the first: both raise the same error, so no test
    # could tell which one fired.
    candidate_models = _single_model_set(candidate_by_case, "candidate")
    if candidate_models is None:
        raise SuiteError(
            "candidate run produced no results; there is nothing to gate, and a "
            "gate with no measurements must never report PASS"
        )

    candidate_models.require_pinned("candidate")
    if baseline_models is not None:
        baseline_models.require_pinned("baseline")

    # A baseline result must have been produced by the digests its case claims
    # produced it. This is the stale-baseline check: results get committed and
    # then the pin gets bumped without a re-run, and every delta afterwards is
    # measured against a model nobody is running.
    for case_id in sorted(baseline_by_case):
        result = baseline_by_case[case_id]
        case = cases[case_id]
        if result.models.identity != case.baseline_models.identity:
            raise BaselineDigestMismatch(
                f"{case_id}: baseline result was produced by "
                f"[{result.models.describe()}] but the case pins "
                f"[{case.baseline_models.describe()}]"
            )

    _require_matching_inputs(cases, baseline_by_case, "baseline")
    _require_matching_inputs(cases, candidate_by_case, "candidate")

    is_no_op = (
        baseline_models is not None
        and baseline_models.identity == candidate_models.identity
    )

    waiver_by_case = _index_waivers(waivers)

    comparisons: list[CaseComparison] = []
    failures: list[Failure] = []
    used_waivers: set[str] = set()
    expired_waivers: set[str] = set()

    for case_id in sorted(cases):
        case = cases[case_id]
        comparison, case_failures, waiver_used, waiver_expired = _compare_case(
            case=case,
            baseline_result=baseline_by_case.get(case_id),
            candidate_result=candidate_by_case.get(case_id),
            baseline_models=baseline_models,
            candidate_models=candidate_models,
            policy=policy,
            waiver=waiver_by_case.get(case_id),
            as_of=as_of,
            is_no_op=is_no_op,
        )
        comparisons.append(comparison)
        failures.extend(case_failures)
        if waiver_used:
            used_waivers.add(case_id)
        if waiver_expired:
            expired_waivers.add(case_id)

    declared = set(policy.categories)
    for comparison in comparisons:
        if comparison.category not in declared:
            failures.append(
                Failure(
                    code="unknown_category",
                    scope=comparison.case_id,
                    detail=(
                        f"case declares category {comparison.category!r}, which the "
                        "policy does not list; a mistyped category silently removes "
                        "the case from the category it was meant to defend"
                    ),
                )
            )

    categories, category_failures = _build_categories(comparisons, policy)
    failures.extend(category_failures)

    category_means = [
        (report.baseline_mean, report.candidate_mean)
        for report in categories
        if report.baseline_mean is not None and report.candidate_mean is not None
    ]
    overall_baseline = _mean([pair[0] for pair in category_means])
    overall_candidate = _mean([pair[1] for pair in category_means])
    overall_delta = (
        _quantise(overall_candidate - overall_baseline)
        if overall_baseline is not None and overall_candidate is not None
        else None
    )
    if overall_delta is not None and -overall_delta > policy.max_overall_mean_drop:
        failures.append(
            Failure(
                code="overall_mean_drop",
                scope="overall",
                detail=(
                    f"category-balanced mean fell {-overall_delta:.6f}, over the "
                    f"{policy.max_overall_mean_drop} cap"
                ),
            )
        )

    # Reported for contrast only. If this number and `overall_candidate_mean`
    # disagree, one large category is carrying the suite -- which is precisely
    # the situation the per-category gates exist for.
    case_weighted = _mean(
        [c.candidate_goodness for c in comparisons if c.candidate_goodness is not None]
    )

    unused = tuple(
        sorted(case_id for case_id in waiver_by_case if case_id not in used_waivers)
    )

    verdict = Verdict.PASS if not failures else Verdict.FAIL
    return GateReport(
        verdict=verdict,
        baseline_models=baseline_models,
        candidate_models=candidate_models,
        is_no_op=is_no_op,
        cases=tuple(comparisons),
        categories=categories,
        overall_baseline_mean=overall_baseline,
        overall_candidate_mean=overall_candidate,
        overall_delta=overall_delta,
        case_weighted_candidate_mean=case_weighted,
        failures=tuple(sorted(failures, key=Failure.sort_key)),
        unused_waivers=unused,
        expired_waivers=tuple(sorted(expired_waivers)),
        policy=policy,
        evaluated_as_of=as_of,
    )


def _compare_case(
    *,
    case: BenchmarkCase,
    baseline_result: CaseResult | None,
    candidate_result: CaseResult | None,
    baseline_models: ModelSet | None,
    candidate_models: ModelSet,
    policy: GatePolicy,
    waiver: Waiver | None,
    as_of: date,
    is_no_op: bool,
) -> tuple[CaseComparison, list[Failure], bool, bool]:
    failures: list[Failure] = []
    notes: list[str] = []
    waiver_used = False
    waiver_expired = False

    baseline_value = baseline_result.value if baseline_result else None
    candidate_value = candidate_result.value if candidate_result else None
    baseline_goodness = (
        case.metric.goodness(baseline_value) if baseline_value is not None else None
    )
    candidate_goodness = (
        case.metric.goodness(candidate_value) if candidate_value is not None else None
    )
    spread = candidate_result.spread if candidate_result else 0.0
    # The baseline is checked too. A committed baseline whose own repeats
    # disagree set the bar wherever that run happened to land, and every delta
    # measured against it afterwards inherits the noise -- with nothing in the
    # report saying so, because only the candidate side was ever inspected.
    baseline_spread = baseline_result.spread if baseline_result else 0.0
    repeats = len(candidate_result.samples) if candidate_result else 0

    baseline_meets = (
        baseline_goodness >= case.expected_goodness if baseline_goodness is not None else None
    )
    candidate_meets = (
        candidate_goodness >= case.expected_goodness if candidate_goodness is not None else None
    )

    # --- structural statuses first: they are not deltas ---------------------
    if candidate_result is None and baseline_result is None:
        status = CaseStatus.UNRUN
        notes.append("neither run scored this case")
    elif candidate_result is None:
        status = CaseStatus.MISSING_CANDIDATE
        failures.append(
            Failure(
                code="case_missing_candidate",
                scope=case.case_id,
                detail=(
                    "the baseline scored this case and the candidate did not; a "
                    "dropped case is the cheapest way to make a suite green"
                ),
            )
        )
    elif baseline_result is None:
        status = CaseStatus.NO_BASELINE
        if not policy.allow_new_cases:
            failures.append(
                Failure(
                    code="case_new_without_baseline",
                    scope=case.case_id,
                    detail=(
                        "no baseline result; a new case must be baselined in a "
                        "separate non-gating run before it can gate a swap"
                    ),
                )
            )
        else:
            notes.append("new case admitted without a baseline by policy")
    elif max(spread, baseline_spread) > policy.nondeterminism_tolerance:
        # Checked before the delta is classified: a delta computed from a
        # number the model cannot repeat is not a measurement, so it must not
        # be reported as IMPROVED or REGRESSED.
        status = CaseStatus.NONDETERMINISTIC
        side = "candidate" if spread >= baseline_spread else "baseline"
        failures.append(
            Failure(
                code="case_nondeterministic",
                scope=case.case_id,
                detail=(
                    f"{side} repeats span {max(spread, baseline_spread):.9f}, over "
                    f"the {policy.nondeterminism_tolerance} tolerance"
                ),
            )
        )
    else:
        delta = _quantise(candidate_goodness - baseline_goodness)  # type: ignore[operator]
        if delta > policy.case_regression_tolerance:
            status = CaseStatus.IMPROVED
        elif -delta > policy.case_regression_tolerance:
            status = CaseStatus.REGRESSED
        else:
            status = CaseStatus.UNCHANGED

    delta = (
        _quantise(candidate_goodness - baseline_goodness)
        if baseline_goodness is not None and candidate_goodness is not None
        else None
    )

    # --- the per-case relative cap, and the only thing a waiver touches -----
    # Checked on the DROP, not on the label. This used to read
    # `status is CaseStatus.REGRESSED and ...`, which put the hard cap behind
    # `case_regression_tolerance`: raise the tolerance above the cap -- a
    # reasonable-looking edit to quiet a noisy metric, and one GatePolicy used
    # to accept -- and a case could fall further than the cap while being
    # labelled UNCHANGED, so the cap never fired. GatePolicy now refuses that
    # ordering, and this condition no longer depends on it either; a cliff-edge
    # protection that can be disabled from another knob is not a protection.
    #
    # The statuses excluded here are excluded for a reason, not for tidiness:
    # NONDETERMINISTIC has a delta that is not a measurement (decision 6, and
    # failing it twice would blame quality for an unrepeatable number), and the
    # structural statuses have no delta at all.
    delta_is_a_measurement = status in (
        CaseStatus.IMPROVED,
        CaseStatus.UNCHANGED,
        CaseStatus.REGRESSED,
    )
    drop = -delta if delta is not None else None
    if delta_is_a_measurement and drop is not None and drop > policy.max_case_drop:
        if waiver is None:
            failures.append(
                Failure(
                    code="case_drop_exceeds_cap",
                    scope=case.case_id,
                    detail=f"dropped {drop:.6f}, over the {policy.max_case_drop} cap",
                )
            )
        # baseline_models is never None here: reaching REGRESSED required a
        # baseline result, which required a baseline model set. Spelled out
        # rather than written as `baseline_models or candidate_models`, because
        # that fallback would quietly compare the waiver's baseline against the
        # CANDIDATE digests and match waivers it was never reviewed for.
        elif not waiver.covers(baseline=baseline_models, candidate=candidate_models):
            notes.append(
                "waiver ignored: it was approved against a different model pair"
            )
            failures.append(
                Failure(
                    code="case_drop_exceeds_cap",
                    scope=case.case_id,
                    detail=(
                        f"dropped {drop:.6f}, over the {policy.max_case_drop} cap; the "
                        "waiver on file was approved against different digests"
                    ),
                )
            )
        elif as_of > waiver.expires_on:
            waiver_expired = True
            notes.append(f"waiver expired on {waiver.expires_on.isoformat()}")
            failures.append(
                Failure(
                    code="case_drop_exceeds_cap",
                    scope=case.case_id,
                    detail=(
                        f"dropped {drop:.6f}, over the {policy.max_case_drop} cap; the "
                        f"waiver expired on {waiver.expires_on.isoformat()}"
                    ),
                )
            )
        elif drop > waiver.max_drop:
            notes.append(
                f"waiver caps the forgiven drop at {waiver.max_drop}; actual drop is larger"
            )
            failures.append(
                Failure(
                    code="case_drop_exceeds_cap",
                    scope=case.case_id,
                    detail=(
                        f"dropped {drop:.6f}, over both the {policy.max_case_drop} cap "
                        f"and the waiver's own {waiver.max_drop} limit"
                    ),
                )
            )
        else:
            waiver_used = True
            notes.append(
                f"waived by {waiver.approved_by} until {waiver.expires_on.isoformat()}: "
                f"{waiver.reason}"
            )

    # --- the absolute promise, which no waiver may touch --------------------
    if policy.enforce_expected and candidate_meets is False:
        detail = (
            f"candidate {case.metric.name}={candidate_value} misses the expected "
            f"{case.expected}"
        )
        if baseline_meets is False:
            # Said out loud so nobody reads this as "the swap broke it". The
            # suite is reporting a promise that was already unmet.
            detail += "; the baseline missed it too, so this case was already failing"
        failures.append(
            Failure(code="case_below_expected", scope=case.case_id, detail=detail)
        )

    # --- proof of repeatability, when the policy demands it -----------------
    if candidate_result is not None and repeats < policy.min_repeats:
        failures.append(
            Failure(
                code="case_insufficient_repeats",
                scope=case.case_id,
                detail=(
                    f"{repeats} repeat(s); policy requires {policy.min_repeats} to "
                    "demonstrate the number is reproducible"
                ),
            )
        )

    # --- the sharpest determinism check we have -----------------------------
    if is_no_op and delta is not None and abs(delta) > policy.nondeterminism_tolerance:
        failures.append(
            Failure(
                code="no_op_drift",
                scope=case.case_id,
                detail=(
                    f"candidate and baseline pin identical digests yet the score moved "
                    f"by {delta:+.6f}; something outside the model changed"
                ),
            )
        )

    comparison = CaseComparison(
        case_id=case.case_id,
        category=case.category,
        metric=case.metric.name,
        status=status,
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        baseline_goodness=baseline_goodness,
        candidate_goodness=candidate_goodness,
        delta=delta,
        baseline_meets_expected=baseline_meets,
        candidate_meets_expected=candidate_meets,
        spread=spread,
        baseline_spread=baseline_spread,
        repeats=repeats,
        waived=waiver_used,
        waiver=waiver if waiver_used else None,
        notes=tuple(notes),
    )
    return comparison, failures, waiver_used, waiver_expired


def _build_categories(
    comparisons: Sequence[CaseComparison], policy: GatePolicy
) -> tuple[tuple[CategoryReport, ...], list[Failure]]:
    """Per-category aggregates and the gates that only exist at this level."""
    failures: list[Failure] = []
    grouped: dict[str, list[CaseComparison]] = {}
    for comparison in comparisons:
        grouped.setdefault(comparison.category, []).append(comparison)

    names = sorted(set(grouped) | set(policy.categories))
    reports: list[CategoryReport] = []
    for name in names:
        members = sorted(grouped.get(name, []), key=lambda c: c.case_id)
        # Only cases measured on BOTH sides may enter either mean. A case
        # present in one mean and absent from the other moves the delta by an
        # amount that has nothing to do with model quality.
        comparable = [c for c in members if c.comparable]
        baseline_mean = _mean([c.baseline_goodness for c in comparable])
        candidate_mean = _mean([c.candidate_goodness for c in comparable])
        mean_delta = (
            _quantise(candidate_mean - baseline_mean)
            if baseline_mean is not None and candidate_mean is not None
            else None
        )
        floor = policy.category_floors.get(name)
        meets_floor = (
            candidate_mean >= floor
            if floor is not None and candidate_mean is not None
            else None
        )
        # Waived cases still count as regressed here. A waiver forgives one
        # case; it does not buy a category the right to slide.
        regressed = tuple(
            c.case_id for c in members if c.status is CaseStatus.REGRESSED
        )
        waived = tuple(c.case_id for c in members if c.waived)
        regressed_fraction = (
            _quantise(len(regressed) / len(comparable)) if comparable else None
        )

        if name in policy.categories:
            if len(comparable) < policy.min_cases_per_category:
                failures.append(
                    Failure(
                        code="category_unrepresented",
                        scope=name,
                        detail=(
                            f"{len(comparable)} comparable case(s); policy requires "
                            f"{policy.min_cases_per_category}. A mean over a category "
                            "the run did not cover is not a measurement"
                        ),
                    )
                )
            if mean_delta is not None and -mean_delta > policy.max_category_mean_drop:
                failures.append(
                    Failure(
                        code="category_mean_drop",
                        scope=name,
                        detail=(
                            f"category mean fell {-mean_delta:.6f}, over the "
                            f"{policy.max_category_mean_drop} cap"
                        ),
                    )
                )
            if meets_floor is False:
                failures.append(
                    Failure(
                        code="category_below_floor",
                        scope=name,
                        detail=f"category mean {candidate_mean} is under the {floor} floor",
                    )
                )
            if (
                regressed_fraction is not None
                and regressed_fraction > policy.max_regressed_fraction_per_category
            ):
                failures.append(
                    Failure(
                        code="category_regressed_fraction",
                        scope=name,
                        detail=(
                            f"{len(regressed)}/{len(comparable)} cases regressed, over "
                            f"the {policy.max_regressed_fraction_per_category} fraction; "
                            "many small losses hidden by a few large wins"
                        ),
                    )
                )

        reports.append(
            CategoryReport(
                category=name,
                case_ids=tuple(c.case_id for c in members),
                comparable_case_ids=tuple(c.case_id for c in comparable),
                baseline_mean=baseline_mean,
                candidate_mean=candidate_mean,
                mean_delta=mean_delta,
                floor=floor,
                meets_floor=meets_floor,
                regressed_case_ids=regressed,
                waived_case_ids=waived,
                regressed_fraction=regressed_fraction,
            )
        )
    return tuple(reports), failures


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _index_results(
    results: Sequence[CaseResult], role: str, cases: Mapping[str, BenchmarkCase]
) -> dict[str, CaseResult]:
    indexed: dict[str, CaseResult] = {}
    for result in results:
        if result.case_id in indexed:
            raise SuiteError(
                f"{role} run reports {result.case_id} twice; which one is the "
                "measurement is not recoverable"
            )
        if result.case_id not in cases:
            raise UnknownCase(
                f"{role} run reports {result.case_id!r}, which the suite does not "
                "contain; ignoring it would mean comparing against a different suite"
            )
        indexed[result.case_id] = result
    return indexed


def _single_model_set(
    results: Mapping[str, CaseResult], role: str
) -> ModelSet | None:
    """One run, one model set.

    A run where half the cases were scored by the old weights still resident in
    the runtime and half by the new ones produces a mean that describes no model
    at all -- and it looks completely normal.
    """
    chosen: ModelSet | None = None
    for case_id in sorted(results):
        models = results[case_id].models
        if chosen is None:
            chosen = models
        elif models.identity != chosen.identity:
            raise ModelSetMismatch(
                f"{role} run mixes model sets: {case_id} was scored by "
                f"[{models.describe()}] while earlier cases used "
                f"[{chosen.describe()}]"
            )
    return chosen


def _require_matching_inputs(
    cases: Mapping[str, BenchmarkCase], results: Mapping[str, CaseResult], role: str
) -> None:
    for case_id in sorted(results):
        expected = cases[case_id].inputs_digest
        actual = results[case_id].inputs_digest
        if actual != expected:
            raise InputsDigestMismatch(
                f"{case_id}: {role} run scored inputs {actual} but the case declares "
                f"{expected}; same model on different data is not a delta"
            )


def _index_waivers(waivers: Sequence[Waiver]) -> dict[str, Waiver]:
    indexed: dict[str, Waiver] = {}
    for waiver in waivers:
        if waiver.case_id in indexed:
            raise SuiteError(
                f"two waivers for {waiver.case_id}; which cap applies would be "
                "decided by list order, and the looser one would always win"
            )
        indexed[waiver.case_id] = waiver
    return indexed


def _mean(values: Sequence[float | None]) -> float | None:
    """Mean of the present values, or None when there are none.

    math.fsum is exactly rounded, so the result does not depend on the order the
    values arrive in; no sort is needed to make this reproducible, and adding
    one would be decoration rather than a guarantee.
    """
    present = [value for value in values if value is not None]
    if not present:
        return None
    return _quantise(math.fsum(present) / len(present))


def _quantise(value: float) -> float:
    """Six decimals, matching the precision the contract's Score stores.

    Also normalises -0.0 to 0.0: they compare equal but serialise differently,
    which would give two identical runs two different report digests.
    """
    result = round(value + 0.0, 6)
    return 0.0 if result == 0 else result


def _require_unit(name: str, value: object) -> None:
    """Every score in this harness is a contract Unit: a finite number in [0,1].

    bool is rejected explicitly because it is an int subclass: `True` would
    sail through as 1.0 and a broken runner emitting pass/fail booleans would
    read as a perfect score.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SuiteError(f"{name} must be a number in [0,1], got {value!r}")
    if not math.isfinite(value):
        raise SuiteError(f"{name} must be finite, got {value!r}")
    if not 0.0 <= value <= 1.0:
        raise SuiteError(
            f"{name}={value} is outside [0,1]; scores are normalised Units, and a "
            "raw-scale value would dominate every mean it entered"
        )


def format_report(report: GateReport) -> str:
    """Human-readable summary for a CI log.

    A gate whose reasons are only in a JSON blob gets overridden by whoever is
    in a hurry, because nobody reads the blob.
    """
    lines = [
        f"verdict: {report.verdict.value.upper()}",
        f"baseline: [{report.baseline_models.describe() if report.baseline_models else 'none'}]",
        f"candidate: [{report.candidate_models.describe()}]",
    ]
    if report.is_no_op:
        lines.append("note: candidate pins the same digests as the baseline (no-op run)")
    for category in report.categories:
        delta = "n/a" if category.mean_delta is None else f"{category.mean_delta:+.6f}"
        lines.append(
            f"  {category.category}: {len(category.comparable_case_ids)} case(s), "
            f"mean {category.candidate_mean} ({delta}), "
            f"{len(category.regressed_case_ids)} regressed"
        )
    delta = "n/a" if report.overall_delta is None else f"{report.overall_delta:+.6f}"
    lines.append(f"  overall (category-balanced): {report.overall_candidate_mean} ({delta})")
    if report.unused_waivers:
        lines.append(
            "  unused waivers (the regression they covered is gone; remove them "
            "before they forgive the next one): " + ", ".join(report.unused_waivers)
        )
    for failure in report.failures:
        lines.append(f"  FAIL {failure.code} [{failure.scope}]: {failure.detail}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Gate input: the file a CI job hands this module
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GateInput:
    """One complete gate invocation, loaded from one file.

    Everything `evaluate` needs, including `as_of`. The date lives in the file
    rather than on the command line on purpose: a `--as-of` flag is a waiver
    extension mechanism that leaves no trace in the repository, and the whole
    point of the waiver rules is that moving an expiry is a reviewed edit.
    """

    suite: BenchmarkSuite
    baseline: tuple[CaseResult, ...]
    candidate: tuple[CaseResult, ...]
    policy: GatePolicy
    waivers: tuple[Waiver, ...]
    as_of: date


@dataclass(frozen=True)
class GateOutcome:
    """The result of running the gate, including "there is no result".

    `verdict` is PASS, FAIL or SKIP; `report` is present for the first two and
    absent for the third, and that invariant is enforced rather than trusted,
    because "SKIP with a report attached" is exactly the shape that lets a
    refusal get read as a measurement.
    """

    source: str
    verdict: Verdict
    report: GateReport | None
    refusal: str | None
    refusal_type: str | None

    def __post_init__(self) -> None:
        if self.verdict is Verdict.SKIP:
            if self.report is not None or not self.refusal or not self.refusal_type:
                raise SuiteError(
                    "a SKIP outcome carries a refusal and no report; anything else "
                    "would let a refused comparison be read as a measured one"
                )
        elif self.report is None or self.refusal is not None:
            raise SuiteError(
                f"a {self.verdict.value.upper()} outcome must carry the report it "
                "came from and no refusal"
            )

    @property
    def exit_code(self) -> int:
        return self.verdict.exit_code

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "verdict": self.verdict.value,
            "exit_code": self.exit_code,
            "refusal": self.refusal,
            "refusal_type": self.refusal_type,
            "report": self.report.to_dict() if self.report is not None else None,
            "report_digest": self.report.digest() if self.report is not None else None,
        }


def run_gate(gate: GateInput, *, source: str = "<memory>") -> GateOutcome:
    """Evaluate a gate input and name the refusal case instead of raising it.

    This is the ONLY place `ComparisonRefused` becomes a value. `SuiteError` is
    deliberately not caught: a malformed suite is not "the models could not be
    compared", it is "this gate was never set up", which is EXIT_USAGE and a
    different person's problem.
    """
    try:
        report = evaluate(
            gate.suite,
            gate.baseline,
            gate.candidate,
            gate.policy,
            gate.waivers,
            as_of=gate.as_of,
        )
    except ComparisonRefused as refused:
        return GateOutcome(
            source=source,
            verdict=Verdict.SKIP,
            report=None,
            refusal=str(refused),
            refusal_type=type(refused).__name__,
        )
    return GateOutcome(
        source=source,
        verdict=report.verdict,
        report=report,
        refusal=None,
        refusal_type=None,
    )


def run_gate_file(path: Path | str) -> GateOutcome:
    """Load a gate file and run it. Raises SuiteError on a malformed file."""
    path = Path(path)
    return run_gate(load_gate_file(path), source=str(path))


# --------------------------------------------------------------------------
# Loading a gate file
# --------------------------------------------------------------------------


def load_gate_file(path: Path | str) -> GateInput:
    """Parse a gate file from disk.

    OSError is left to the caller: "the file is not there" and "the file is not
    a gate" are different mistakes and the command reports them differently.
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as bad:
        raise SuiteError(f"{path}: not valid JSON: {bad}") from bad
    return load_gate_input(document, where=str(path))


def load_gate_input(document: object, *, where: str = "gate") -> GateInput:
    """Build a GateInput from a parsed JSON document.

    Unknown fields are refused everywhere, at every level. A gate file with
    `"max_case_drop_"` in it is a gate file whose cap silently took the default
    -- the policy in the repository would say one thing and the policy that ran
    would be another, which is the failure mode this whole module exists to
    stop one level up.
    """
    fields = _fields(
        where,
        document,
        required=("as_of", "suite", "baseline", "candidate"),
        optional=("policy", "waivers", "description"),
    )
    suite_fields = _fields(f"{where}.suite", fields["suite"], required=("cases",))
    cases = [
        _load_case(f"{where}.suite.cases[{index}]", entry)
        for index, entry in enumerate(_load_list(f"{where}.suite.cases", suite_fields["cases"]))
    ]
    baseline = tuple(
        _load_result(f"{where}.baseline[{index}]", entry)
        for index, entry in enumerate(_load_list(f"{where}.baseline", fields["baseline"]))
    )
    candidate = tuple(
        _load_result(f"{where}.candidate[{index}]", entry)
        for index, entry in enumerate(_load_list(f"{where}.candidate", fields["candidate"]))
    )
    waivers = tuple(
        _load_waiver(f"{where}.waivers[{index}]", entry)
        for index, entry in enumerate(_load_list(f"{where}.waivers", fields.get("waivers", [])))
    )
    policy = _load_policy(f"{where}.policy", fields.get("policy"))
    return GateInput(
        suite=BenchmarkSuite(cases),
        baseline=baseline,
        candidate=candidate,
        policy=policy,
        waivers=waivers,
        as_of=_load_date(f"{where}.as_of", fields["as_of"]),
    )


def _fields(
    where: str,
    value: object,
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise SuiteError(f"{where} must be a JSON object, got {type(value).__name__}")
    keys = set(value)
    missing = sorted(set(required) - keys)
    if missing:
        raise SuiteError(f"{where} is missing required field(s): {', '.join(missing)}")
    unknown = sorted(keys - set(required) - set(optional))
    if unknown:
        raise SuiteError(
            f"{where} has unknown field(s): {', '.join(unknown)}. A misspelled knob "
            "would take its default silently, so the gate that ran would not be the "
            "gate that was reviewed"
        )
    return dict(value)


def _load_list(where: str, value: object) -> list[object]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        raise SuiteError(f"{where} must be a JSON array, got {type(value).__name__}")
    return list(value)


def _load_str(where: str, value: object) -> str:
    if not isinstance(value, str):
        raise SuiteError(f"{where} must be a string, got {type(value).__name__}")
    return value


def _load_number(where: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SuiteError(f"{where} must be a number, got {value!r}")
    return float(value)


def _load_int(where: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SuiteError(f"{where} must be an integer, got {value!r}")
    return value


def _load_bool(where: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise SuiteError(f"{where} must be true or false, got {value!r}")
    return value


def _load_date(where: str, value: object) -> date:
    text = _load_str(where, value)
    try:
        return date.fromisoformat(text)
    except ValueError as bad:
        raise SuiteError(f"{where} must be an ISO date (YYYY-MM-DD), got {text!r}") from bad


def _load_digest_or_null(where: str, value: object) -> str | None:
    if value is None:
        return None
    return _load_str(where, value)


def _load_pin(where: str, value: object) -> ModelPin:
    fields = _fields(
        where,
        value,
        required=("model_id", "version", "weights_blake3", "config_blake3"),
    )
    return ModelPin(
        model_id=_load_str(f"{where}.model_id", fields["model_id"]),
        version=_load_str(f"{where}.version", fields["version"]),
        weights_blake3=_load_digest_or_null(
            f"{where}.weights_blake3", fields["weights_blake3"]
        ),
        config_blake3=_load_digest_or_null(
            f"{where}.config_blake3", fields["config_blake3"]
        ),
    )


def _load_model_set(where: str, value: object) -> ModelSet:
    entries = _load_list(where, value)
    return ModelSet(
        _load_pin(f"{where}[{index}]", entry) for index, entry in enumerate(entries)
    )


def _load_metric(where: str, value: object) -> Metric:
    fields = _fields(where, value, required=("name", "direction"))
    raw = _load_str(f"{where}.direction", fields["direction"])
    try:
        direction = Direction(raw)
    except ValueError as bad:
        allowed = ", ".join(member.value for member in Direction)
        raise SuiteError(
            f"{where}.direction must be one of: {allowed}; got {raw!r}. A metric "
            "with the wrong direction reads a regression as an improvement"
        ) from bad
    return Metric(name=_load_str(f"{where}.name", fields["name"]), direction=direction)


def _load_case(where: str, value: object) -> BenchmarkCase:
    fields = _fields(
        where,
        value,
        required=(
            "case_id",
            "category",
            "inputs_digest",
            "baseline_models",
            "metric",
            "expected",
        ),
        optional=("description",),
    )
    return BenchmarkCase(
        case_id=_load_str(f"{where}.case_id", fields["case_id"]),
        category=_load_str(f"{where}.category", fields["category"]),
        inputs_digest=_load_str(f"{where}.inputs_digest", fields["inputs_digest"]),
        baseline_models=_load_model_set(f"{where}.baseline_models", fields["baseline_models"]),
        metric=_load_metric(f"{where}.metric", fields["metric"]),
        expected=_load_number(f"{where}.expected", fields["expected"]),
        description=_load_str(f"{where}.description", fields.get("description", "")),
    )


def _load_result(where: str, value: object) -> CaseResult:
    fields = _fields(
        where, value, required=("case_id", "models", "inputs_digest", "samples")
    )
    samples = _load_list(f"{where}.samples", fields["samples"])
    return CaseResult(
        case_id=_load_str(f"{where}.case_id", fields["case_id"]),
        models=_load_model_set(f"{where}.models", fields["models"]),
        inputs_digest=_load_str(f"{where}.inputs_digest", fields["inputs_digest"]),
        samples=[
            _load_number(f"{where}.samples[{index}]", sample)
            for index, sample in enumerate(samples)
        ],
    )


def _load_waiver(where: str, value: object) -> Waiver:
    fields = _fields(
        where,
        value,
        required=(
            "case_id",
            "baseline_models",
            "candidate_models",
            "max_drop",
            "reason",
            "approved_by",
            "approved_on",
            "expires_on",
        ),
    )
    return Waiver(
        case_id=_load_str(f"{where}.case_id", fields["case_id"]),
        baseline_models=_load_model_set(
            f"{where}.baseline_models", fields["baseline_models"]
        ),
        candidate_models=_load_model_set(
            f"{where}.candidate_models", fields["candidate_models"]
        ),
        max_drop=_load_number(f"{where}.max_drop", fields["max_drop"]),
        reason=_load_str(f"{where}.reason", fields["reason"]),
        approved_by=_load_str(f"{where}.approved_by", fields["approved_by"]),
        approved_on=_load_date(f"{where}.approved_on", fields["approved_on"]),
        expires_on=_load_date(f"{where}.expires_on", fields["expires_on"]),
    )


def _load_policy(where: str, value: object) -> GatePolicy:
    """An absent policy means the defaults, which are the strict ones."""
    if value is None:
        return GatePolicy()
    fields = _fields(
        where,
        value,
        required=(),
        optional=(
            "categories",
            "category_floors",
            "case_regression_tolerance",
            "max_case_drop",
            "max_category_mean_drop",
            "max_regressed_fraction_per_category",
            "max_overall_mean_drop",
            "nondeterminism_tolerance",
            "min_repeats",
            "min_cases_per_category",
            "allow_new_cases",
            "enforce_expected",
        ),
    )
    kwargs: dict[str, object] = {}
    if "categories" in fields:
        kwargs["categories"] = tuple(
            _load_str(f"{where}.categories[{index}]", entry)
            for index, entry in enumerate(
                _load_list(f"{where}.categories", fields["categories"])
            )
        )
    if "category_floors" in fields:
        floors = fields["category_floors"]
        if not isinstance(floors, Mapping):
            raise SuiteError(f"{where}.category_floors must be a JSON object")
        kwargs["category_floors"] = {
            _load_str(f"{where}.category_floors key", name): _load_number(
                f"{where}.category_floors[{name}]", floor
            )
            for name, floor in floors.items()
        }
    for name in (
        "case_regression_tolerance",
        "max_case_drop",
        "max_category_mean_drop",
        "max_regressed_fraction_per_category",
        "max_overall_mean_drop",
        "nondeterminism_tolerance",
    ):
        if name in fields:
            kwargs[name] = _load_number(f"{where}.{name}", fields[name])
    for name in ("min_repeats", "min_cases_per_category"):
        if name in fields:
            kwargs[name] = _load_int(f"{where}.{name}", fields[name])
    for name in ("allow_new_cases", "enforce_expected"):
        if name in fields:
            kwargs[name] = _load_bool(f"{where}.{name}", fields[name])
    return GatePolicy(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def format_outcome(outcome: GateOutcome) -> str:
    """The CI log block for one gate file, refusals included."""
    header = f"gate: {outcome.source}"
    if outcome.verdict is Verdict.SKIP:
        return "\n".join(
            [
                header,
                "verdict: SKIP",
                f"refused: {outcome.refusal_type}: {outcome.refusal}",
                (
                    "  no comparison exists. This is NOT a quality signal and cannot "
                    "be waived: fix the pins, the inputs or the baseline and run the "
                    "gate again."
                ),
            ]
        )
    report = outcome.report
    if report is None:  # unreachable: GateOutcome.__post_init__ guarantees it
        raise SuiteError(f"{outcome.verdict.value} outcome without a report")
    return "\n".join([header, format_report(report), f"digest: {report.digest()}"])


# Which code wins when several gate files are run at once. A real FAIL outranks
# a refusal because it is the more actionable answer, and a malformed input
# outranks everything because nothing else in the run can be trusted. Written
# down rather than reached with max(), which would silently make "could not
# check" louder than "the model got worse".
_EXIT_PRECEDENCE: tuple[int, ...] = (EXIT_USAGE, EXIT_FAIL, EXIT_REFUSED, EXIT_PASS)

_EXIT_NAMES: dict[int, str] = {
    EXIT_PASS: "pass",
    EXIT_FAIL: "fail",
    EXIT_REFUSED: "refused (no comparison exists)",
    EXIT_USAGE: "usage (the gate could not be set up)",
}


def _worst(codes: Sequence[int]) -> int:
    for code in _EXIT_PRECEDENCE:
        if code in codes:
            return code
    return EXIT_PASS


class _GateArgumentParser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error; here 2 means "comparison refused".

    Two different things sharing an exit code is the bug this whole command
    exists to fix, so the parser is not allowed to reuse that number either.
    """

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one or more gate files and return the exit code CI should see."""
    parser = _GateArgumentParser(
        prog="python3 -m memory_engine_eval.harness",
        description=(
            "Run the model-swap regression gate (CLAUDE.md hard rule 7) over one or "
            "more gate files."
        ),
        epilog=(
            "exit codes: 0 pass; 1 fail (measured regression); 2 refused (no "
            "comparison exists -- mismatched digests, mixed model sets, unpinned "
            "weights); 3 usage (malformed or unreadable gate file). 2 and 3 are "
            "failures too: 'I could not check' must never be green."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "gate_files",
        nargs="+",
        metavar="GATE_FILE",
        help="JSON gate file: suite, baseline, candidate, policy, waivers, as_of",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="PATH",
        help="write the machine-readable outcomes here (one JSON array)",
    )
    args = parser.parse_args(argv)

    codes: list[int] = []
    outcomes: list[GateOutcome] = []
    for raw_path in args.gate_files:
        path = Path(raw_path)
        try:
            outcome = run_gate_file(path)
        except SuiteError as malformed:
            print(f"gate: {path}\nverdict: UNUSABLE\n  {malformed}", file=sys.stderr)
            codes.append(EXIT_USAGE)
            continue
        except OSError as unreadable:
            print(f"gate: {path}\nverdict: UNUSABLE\n  {unreadable}", file=sys.stderr)
            codes.append(EXIT_USAGE)
            continue
        outcomes.append(outcome)
        codes.append(outcome.exit_code)
        stream = sys.stdout if outcome.verdict is Verdict.PASS else sys.stderr
        print(format_outcome(outcome), file=stream)

    if args.json_path is not None:
        payload = json.dumps(
            [outcome.to_dict() for outcome in outcomes], sort_keys=True, indent=2
        )
        try:
            Path(args.json_path).write_text(payload + "\n", encoding="utf-8")
        except OSError as unwritable:
            print(f"could not write {args.json_path}: {unwritable}", file=sys.stderr)
            codes.append(EXIT_USAGE)

    code = _worst(codes)
    print(
        f"gate result: exit {code} -- {_EXIT_NAMES[code]}",
        file=sys.stdout if code == EXIT_PASS else sys.stderr,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
