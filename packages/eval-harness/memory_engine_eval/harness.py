"""The regression gate that stands between a model swap and a shipped regression.

CLAUDE.md hard rule 7: "Model swaps are gated by the eval harness in CI." This
module is that gate. It defines a benchmark case, compares a candidate run
against a baseline run, and returns PASS/FAIL against a written policy.

WHY THIS IS NOT "COMPARE THE MEAN"

The failure this harness exists to catch does not look like a failure. A new
image encoder improves the aggregate score by two points and destroys low-light
indoor family photos. The mean says +0.02 and the swap ships, and the regression
surfaces months later as "the app is bad at baby photos" -- by which point
nobody connects it to a model swap, because the gate was green.

So the mean is advisory here and cannot pass anything. The gate is per-category:
every one of the benchmark categories (build plan: Indian weddings, festivals,
GoPro/adventure, drone, baby/family, travel) is checked independently against an
absolute floor and against its own baseline, and one category failing fails the
gate no matter what the others did. `GateReport.mean_masked_regression` exists
purely to name the situation out loud when it happens.

SEVEN DECISIONS, AND WHAT THE ALTERNATIVE BREAKS

1. A COMPARISON ACROSS A DIGEST MISMATCH IS REFUSED, NOT REPORTED.
   `ModelRef` carries `weights_blake3` AND `config_blake3` because weights alone
   do not pin behaviour -- input size, normalisation constants, score threshold,
   NMS IoU and the alignment template all live in the config, and changing any
   of them changes every downstream decision while the weights hash stays
   byte-identical (see models/policy/digest.py, where that lesson was learned
   from a preprocessing defect that touched no weights byte and never raised).

   A candidate is compared against a baseline as a controlled experiment: ONE
   thing changed. So every role in the pipeline that is not declared
   `under_test` must have identical behaviour digests on both sides. If it does
   not, the reported delta is the sum of two changes attributed to one of them,
   which is worse than no measurement -- it is a measurement that will be
   believed. `evaluate` raises rather than returning FAIL, because a refusal and
   a failure need different fixes and conflating them lets someone make a
   refusal disappear by tuning the model.

2. AND A ROLE DECLARED UNDER TEST THAT DID NOT ACTUALLY CHANGE IS ALSO REFUSED.
   The nastiest CI wiring bug in this class: the job points at the new model, a
   path falls back, the old weights load, the candidate is byte-identical to the
   baseline, every delta is 0.0 and the gate reports a confident PASS for a
   model that never ran. A green gate certifying nothing is the exact shape of
   failure this repo keeps finding. If you declared a swap, the digests must
   differ.

3. THE VERSION STRING IS NOT PART OF MODEL IDENTITY. THE DIGESTS ARE.
   Straight out of the contract's own ModelRef comment: "the same version of a
   HuggingFace repo has changed weights under people before". Comparing on the
   label would let a silent weight change pass as unchanged, and would flag a
   pure re-tag as a swap. Identity is (model_id, weights_blake3, config_blake3).

4. AN UNPINNED MODEL CANNOT GATE ANYTHING.
   `weights_blake3: null` is legal in the contract because development mode
   permits unpinned weights, and it is exactly what makes a record
   non-reproducible. A PASS recorded against an unpinned baseline cannot be
   reproduced or re-checked later, so it is not evidence. Refused.

5. A CASE THE CANDIDATE DID NOT PRODUCE IS A FAILURE, AND CATEGORY MEANS ARE
   COMPUTED OVER PAIRED CASES ONLY.
   The single easiest way to fake an improvement is to drop the hard case. If a
   candidate crashes on the one 3am wedding-reception case and the harness
   quietly averages the remaining four, the category mean IMPROVES and the gate
   goes green on a candidate that literally cannot process the input. So: a
   baseline case with no candidate result is a hard, non-waivable violation, and
   every mean on both sides is computed over the same set of cases. Comparing a
   mean over five cases with a mean over four is not a delta, it is a different
   question.

6. NONDETERMINISM WIDER THAN THE THRESHOLD MEANS THE GATE IS BLIND.
   Cases may be run with replicates. If a case's run-to-run spread exceeds the
   regression threshold the gate is checking, then the gate cannot distinguish
   the regression it exists to detect from noise, and a PASS from it means
   nothing. That is reported as a violation of the measurement rather than
   silently averaged away, and `Policy.validate` refuses a policy whose noise
   allowance is not strictly smaller than its regression thresholds.

7. A HUMAN CAN SAY "THIS REGRESSION IS ACCEPTABLE" WITHOUT THAT BECOMING A
   BYPASS. See `Waiver`. Four constraints make it a statement about one specific
   observed regression rather than a switch: it names exactly one target and one
   violation kind (no wildcards), it authorises a maximum magnitude (a waiver
   for -0.03 does not cover a later -0.40), it is bound to the digest of the
   exact baseline->candidate pair it was written for (so the next swap is not
   pre-approved), and it expires. It also cannot touch the structural violations
   -- a waiver says a quality change is acceptable, it cannot say a broken
   measurement is acceptable.

DETERMINISM
CLAUDE.md hard rule 3. Every aggregate sums its inputs in sorted order and
quantises to six decimals, because float addition is not associative and a
category mean that differs in the last bit between two machines flips a
boundary comparison and makes the gate's verdict depend on which runner picked
up the job. Every collection in the report is sorted by a total key: category
then case id, kind then target. Two runs over the same inputs in a different
order produce byte-identical reports, which the tests assert.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Sequence

# The benchmark libraries named in the build plan. This is the DEFAULT policy
# set and it is deliberately exhaustive: `Policy.categories` is both the set of
# categories a case may declare and the set that must be present in the case
# list. A typo'd category is otherwise the perfect hidden regression -- the
# mistyped cases quietly form a new category of their own, the category they
# were meant to protect is then averaged without them, and nothing reports it.
BENCHMARK_CATEGORIES: tuple[str, ...] = (
    "baby_family",
    "drone",
    "gopro_adventure",
    "indian_festivals",
    "indian_weddings",
    "travel",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Six decimals, matching contracts' Score precision and ranking-engine fusion.
_QUANT = 6


class HarnessError(ValueError):
    """Base for everything this module refuses to do."""


class PolicyError(HarnessError):
    """A policy that cannot decide anything, or decides something it never sees."""


class CaseDefinitionError(HarnessError):
    """A benchmark case that cannot be evaluated as written."""


class GateRefused(HarnessError):
    """The comparison is not possible, which is not the same as FAIL.

    FAIL means the candidate is worse. REFUSED means the two runs are not
    measuring the same experiment, so no verdict about the candidate exists.
    Keeping them distinct matters because a FAIL is fixed by changing the model
    and a REFUSAL is fixed by changing the run -- and because a single "failed"
    state lets someone tune a model until a refusal goes away.
    """


class MetricDirection(str, Enum):
    """Which way is better.

    A gate that assumes higher-is-better reports a rise in the face
    false-match rate as an improvement, and CLAUDE.md hard rule 5 makes that the
    most expensive possible mistake. Not every benchmark metric is a Unit:
    beat-alignment error in milliseconds and false-match rate are both
    lower-is-better, and both are on the gate's list.
    """

    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class ViolationKind(str, Enum):
    """Every way a run can fail the gate. Values are stable strings so a CI log
    line and a waiver file refer to the same thing."""

    CASE_REGRESSION = "case_regression"
    CATEGORY_REGRESSION = "category_regression"
    CATEGORY_FLOOR = "category_floor"
    NEW_CASE_BELOW_FLOOR = "new_case_below_floor"
    CASE_NOT_RUN = "case_not_run"
    BASELINE_DRIFT = "baseline_drift"
    NONDETERMINISTIC = "nondeterministic"
    THIN_CATEGORY = "thin_category"
    CATEGORY_MISSING = "category_missing"


# Only quality judgements are waivable. The rest say the MEASUREMENT is broken:
# a case that did not run, a baseline that no longer reproduces its own expected
# value, a metric noisier than the threshold it is checked against, a category
# with too few cases to detect anything, a category that has vanished from the
# benchmark set. "This regression is acceptable" is a claim a human is entitled
# to make. "This broken measurement is acceptable" is not a claim at all.
WAIVABLE_KINDS: frozenset[ViolationKind] = frozenset(
    {
        ViolationKind.CASE_REGRESSION,
        ViolationKind.CATEGORY_REGRESSION,
        ViolationKind.CATEGORY_FLOOR,
        ViolationKind.NEW_CASE_BELOW_FLOOR,
    }
)


class MissingBaseline(str, Enum):
    """What to do with a candidate case that has no baseline result.

    REFUSE is the default because the usual cause is a stale baseline file, and
    proceeding means the gate silently checks fewer cases than the case list
    claims. ADMIT_AS_NEW is for the legitimate case of adding a benchmark case:
    a delta is impossible, so the case is checked against its category's
    absolute floor instead and reported separately. It is never averaged into
    the category delta -- a mean over "baseline cases" and a mean over
    "baseline cases plus a new one" are not comparable numbers.
    """

    REFUSE = "refuse"
    ADMIT_AS_NEW = "admit_as_new"


def _q(value: float) -> float:
    """Quantise to the precision the contract stores.

    `+ 0.0` normalises -0.0 to 0.0: a category delta of exactly zero printed as
    "-0.0" reads as a regression to a human skimming CI output.
    """
    return round(value + 0.0, _QUANT)


def _mean(values: Sequence[float]) -> float:
    """Mean, summed in sorted order.

    Float addition is not associative. Summing in input order makes a category
    mean depend on the order results happened to be listed in, and two orderings
    that differ in the last bit straddle a floor boundary differently -- so the
    same candidate passes on one machine and fails on another, with no
    difference anyone can see in the report.
    """
    if not values:
        raise HarnessError("mean of no values")
    total = 0.0
    for value in sorted(values):
        total += value
    return _q(total / len(values))


def _require_finite(label: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarnessError(f"{label}={value!r} is not a number")
    if not math.isfinite(value):
        # NaN compares False to every threshold, so a NaN score passes every
        # floor and every regression check untouched and the gate reports PASS.
        # This is the same defect the ranking engine had; it does not raise, it
        # just quietly answers "fine" to every question.
        raise HarnessError(
            f"{label}={value!r} is not finite. NaN compares False to every "
            "threshold, so it would pass every check in this module silently."
        )
    return float(value)


def _require_digest(label: str, value: str | None, *, allow_none: bool) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise GateRefused(f"{label} is null")
    if not isinstance(value, str) or not _HEX64.match(value):
        raise HarnessError(f"{label}={value!r} is not a lowercase hex BLAKE3-256 digest")
    return value


@dataclass(frozen=True)
class ModelPin:
    """A pin to one exact model, mirroring contracts' ModelRef.

    Not the generated pydantic type: this module has no runtime dependencies on
    purpose (see pyproject), and it needs only the three fields that determine
    behaviour. Build one from a contract ModelRef with `ModelPin.from_ref`.
    """

    model_id: str
    version: str
    weights_blake3: str | None
    config_blake3: str | None = None

    def __post_init__(self) -> None:
        if not _SLUG.match(self.model_id):
            raise HarnessError(f"model_id={self.model_id!r} is not a slug")
        if not self.version:
            raise HarnessError("version is empty")
        _require_digest(f"{self.model_id}.weights_blake3", self.weights_blake3, allow_none=True)
        _require_digest(f"{self.model_id}.config_blake3", self.config_blake3, allow_none=True)

    @classmethod
    def from_ref(cls, ref: Mapping[str, object]) -> "ModelPin":
        """Adapter from a contract ModelRef mapping.

        Exists because the ranking engine shipped a package that referenced the
        contract in prose and bypassed it in practice, and the adapter was the
        thing that had been left out. One is provided here from the start.
        """
        return cls(
            model_id=str(ref["model_id"]),
            version=str(ref["version"]),
            weights_blake3=ref.get("weights_blake3"),  # type: ignore[arg-type]
            config_blake3=ref.get("config_blake3"),  # type: ignore[arg-type]
        )

    @property
    def behaviour_key(self) -> tuple[str, str | None, str | None]:
        """What actually determines the output.

        The version STRING is excluded deliberately -- see decision 3 in the
        module docstring. Comparing on the label would let a silent weight
        change read as "unchanged" and a pure re-tag read as a swap.
        """
        return (self.model_id, self.weights_blake3, self.config_blake3)

    @property
    def pinned(self) -> bool:
        return self.weights_blake3 is not None


@dataclass(frozen=True)
class ModelSet:
    """Every model that participated in a run, keyed by its role in the pipeline.

    Roles, not model ids, because the point of a swap is that the role stays and
    the model changes: role "image_embedding" is SigLIP 2 base today and
    something else tomorrow, and the gate has to be able to say "one role
    changed, the rest are held fixed".
    """

    pins: tuple[tuple[str, ModelPin], ...]

    @classmethod
    def of(cls, mapping: Mapping[str, ModelPin]) -> "ModelSet":
        return cls(tuple(sorted(mapping.items(), key=lambda row: row[0])))

    def __post_init__(self) -> None:
        roles = [role for role, _ in self.pins]
        if len(set(roles)) != len(roles):
            raise HarnessError(f"duplicate roles in model set: {sorted(roles)}")
        if list(roles) != sorted(roles):
            raise HarnessError("model set pins must be sorted by role for determinism")

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(role for role, _ in self.pins)

    def get(self, role: str) -> ModelPin | None:
        for name, pin in self.pins:
            if name == role:
                return pin
        return None

    def unpinned_roles(self) -> tuple[str, ...]:
        return tuple(sorted(role for role, pin in self.pins if not pin.pinned))


def model_set_digest(models: ModelSet) -> str:
    """Stable digest over a model set's BEHAVIOUR, for binding waivers.

    Formatted as fixed text and hashed as bytes rather than re-serialised as
    JSON, for the reason models/policy/digest.py spells out at length: JSON
    writers disagree across languages about numbers and key order, and a digest
    the Rust host cannot reproduce is not a digest.
    """
    parts = []
    for role, pin in models.pins:  # already sorted by __post_init__
        parts.append(
            f"{role}={pin.model_id}:{pin.weights_blake3 or '-'}/{pin.config_blake3 or '-'}"
        )
    return hashlib.blake2b(";".join(parts).encode("utf-8"), digest_size=16).hexdigest()


def comparison_digest(baseline: ModelSet, candidate: ModelSet) -> str:
    """Identity of one specific baseline->candidate comparison.

    A waiver binds to this, not to the candidate alone. "This regression is
    acceptable" is a claim about a delta, and the delta changes when either side
    changes -- so re-baselining must invalidate the waiver too, otherwise a
    waiver written against an old baseline keeps excusing a drop that is now
    measured from somewhere else entirely.
    """
    payload = f"{model_set_digest(baseline)}>{model_set_digest(candidate)}"
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


@dataclass(frozen=True)
class BenchmarkCase:
    """One benchmark case: inputs, the pins that produced the baseline, the
    expected outcome.

    `inputs_digest` is a BLAKE3 over the canonical inputs actually fed to the
    pipeline (media ids, parameters). It is recorded rather than the inputs
    themselves because the harness must not hold pixels, and because a case
    whose inputs changed without its id changing is a case whose history is a
    lie. `expected` + `tolerance` are checked against the BASELINE run: if the
    baseline no longer reproduces what the case says it produces, the case is
    stale and every delta computed from it is measured from the wrong place.
    """

    case_id: str
    category: str
    metric: str
    direction: MetricDirection
    inputs_digest: str
    baseline_pins: ModelSet
    expected: float
    tolerance: float = 0.0

    def __post_init__(self) -> None:
        if not _SLUG.match(self.case_id):
            raise CaseDefinitionError(f"case_id={self.case_id!r} is not a slug")
        if not self.metric:
            raise CaseDefinitionError(f"{self.case_id}: metric is empty")
        if not isinstance(self.direction, MetricDirection):
            raise CaseDefinitionError(f"{self.case_id}: direction must be a MetricDirection")
        _require_digest(f"{self.case_id}.inputs_digest", self.inputs_digest, allow_none=False)
        _require_finite(f"{self.case_id}.expected", self.expected)
        _require_finite(f"{self.case_id}.tolerance", self.tolerance)
        if self.tolerance < 0.0:
            raise CaseDefinitionError(f"{self.case_id}: tolerance must not be negative")
        if not self.baseline_pins.pins:
            # A case that pins nothing records no provenance for its expected
            # value, which is the whole reason the field exists.
            raise CaseDefinitionError(
                f"{self.case_id}: baseline_pins is empty. A case must record which "
                "models produced its expected outcome or the outcome is unattributable."
            )


@dataclass(frozen=True)
class CaseResult:
    """What one run measured for one case, one value per replicate.

    Replicates are a tuple rather than a single float because the interesting
    question about a benchmark metric is not only its value but whether it is
    stable enough for a delta against it to mean anything. One replicate is
    allowed and means "asserted deterministic"; the gate then cannot check that
    claim, which is stated in the report rather than assumed.
    """

    case_id: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not self.values:
            raise HarnessError(f"{self.case_id}: no measured values")
        for index, value in enumerate(self.values):
            _require_finite(f"{self.case_id}.values[{index}]", value)

    @property
    def value(self) -> float:
        return _mean(self.values)

    @property
    def spread(self) -> float:
        return _q(max(self.values) - min(self.values))

    @property
    def replicates(self) -> int:
        return len(self.values)


@dataclass(frozen=True)
class RunReport:
    """One execution of the whole benchmark set under one model set."""

    label: str
    models: ModelSet
    results: tuple[CaseResult, ...]

    def by_case(self) -> dict[str, CaseResult]:
        """Results keyed by case id, refusing duplicates.

        A dict comprehension over the tuple would let a duplicated case id
        silently overwrite the first result -- and the one that survives is
        whichever was listed last, i.e. arbitrary. Two results for one case
        means the runner emitted the case twice, which is a bug worth seeing.
        """
        indexed: dict[str, CaseResult] = {}
        for result in self.results:
            if result.case_id in indexed:
                raise HarnessError(
                    f"{self.label}: duplicate result for case {result.case_id!r}; "
                    "one of the two measurements would have been silently discarded"
                )
            indexed[result.case_id] = result
        return indexed


@dataclass(frozen=True)
class CategoryPolicy:
    """The gate for one benchmark category.

    Both halves are needed and neither substitutes for the other.
    `worst_acceptable_mean` is an ABSOLUTE bar: it stops a category being ratchet-
    ed down one acceptable-looking step at a time, where every individual swap
    regresses by less than the delta threshold and after six swaps the category
    is unusable. `max_mean_regression` is a RELATIVE bar: it catches a single
    swap that drops a category which was previously far above the floor and is
    still, after the drop, above it.
    """

    worst_acceptable_mean: float | None = None
    max_mean_regression: float = 0.02


@dataclass(frozen=True)
class Policy:
    """The stated policy. PASS/FAIL comes from this, not from a judgement call.

    `policy_id` and `digest()` are recorded in every report for the same reason
    fusion records its weights id: a verdict you cannot attribute to a specific
    policy is not reproducible, and "the gate passed" means nothing without
    "against which thresholds".
    """

    policy_id: str = "default-v1"
    categories: tuple[str, ...] = BENCHMARK_CATEGORIES
    per_category: Mapping[str, CategoryPolicy] = field(default_factory=dict)
    default_category: CategoryPolicy = field(default_factory=CategoryPolicy)
    max_case_regression: float = 0.05
    min_cases_per_category: int = 3
    max_nondeterminism: float = 0.005
    on_missing_baseline: MissingBaseline = MissingBaseline.REFUSE

    def for_category(self, category: str) -> CategoryPolicy:
        return self.per_category.get(category, self.default_category)

    def validate(self) -> None:
        """Refuse a policy that cannot detect what it claims to gate on."""
        if not self.categories:
            raise PolicyError("policy declares no categories: nothing would be gated")
        if list(self.categories) != sorted(self.categories):
            raise PolicyError("policy categories must be sorted for determinism")
        if len(set(self.categories)) != len(self.categories):
            raise PolicyError("policy categories contain duplicates")
        unknown = sorted(set(self.per_category) - set(self.categories))
        if unknown:
            # A floor keyed to a category that does not exist is a floor that
            # never runs. It reads in review as protection and provides none.
            raise PolicyError(
                f"per_category floors for undeclared categories {unknown}: these "
                "thresholds would never be applied to anything"
            )
        for name, value in (
            ("max_case_regression", self.max_case_regression),
            ("max_nondeterminism", self.max_nondeterminism),
        ):
            _require_finite(name, value)
            if value < 0.0:
                raise PolicyError(f"{name} must not be negative")
        if self.min_cases_per_category < 1:
            raise PolicyError("min_cases_per_category must be at least 1")

        thresholds = [("max_case_regression", self.max_case_regression)]
        for category in self.categories:
            policy = self.for_category(category)
            _require_finite(f"{category}.max_mean_regression", policy.max_mean_regression)
            if policy.max_mean_regression < 0.0:
                raise PolicyError(f"{category}: max_mean_regression must not be negative")
            if policy.worst_acceptable_mean is not None:
                _require_finite(f"{category}.worst_acceptable_mean", policy.worst_acceptable_mean)
            thresholds.append((f"{category}.max_mean_regression", policy.max_mean_regression))

        for name, threshold in thresholds:
            if self.max_nondeterminism >= threshold:
                # If run-to-run noise is as large as the regression the gate is
                # looking for, the gate cannot tell them apart. It will still
                # emit PASS and FAIL, and both will be coin flips.
                raise PolicyError(
                    f"max_nondeterminism={self.max_nondeterminism} is not smaller than "
                    f"{name}={threshold}: a threshold inside the noise floor cannot "
                    "detect the regression it exists to detect"
                )

    def digest(self) -> str:
        """Digest over fixed-format text, not JSON.

        Same reasoning as models/policy/digest.py and the ranking engine's
        weight digest: Python writes 1.0 where JavaScript writes 1, so a JSON
        round-trip digest differs between the pipeline and the desktop shell for
        policies containing whole numbers.
        """
        parts = [
            f"policy_id={self.policy_id}",
            f"categories={','.join(self.categories)}",
            f"max_case_regression={self.max_case_regression:.6f}",
            f"min_cases_per_category={self.min_cases_per_category:d}",
            f"max_nondeterminism={self.max_nondeterminism:.6f}",
            f"on_missing_baseline={self.on_missing_baseline.value}",
        ]
        for category in self.categories:
            policy = self.for_category(category)
            floor = (
                "none"
                if policy.worst_acceptable_mean is None
                else f"{policy.worst_acceptable_mean:.6f}"
            )
            parts.append(
                f"{category}:floor={floor},max_mean_regression={policy.max_mean_regression:.6f}"
            )
        return hashlib.blake2b(";".join(parts).encode("utf-8"), digest_size=16).hexdigest()


class WaiverScope(str, Enum):
    CASE = "case"
    CATEGORY = "category"


@dataclass(frozen=True)
class Waiver:
    """A human recording that ONE observed regression is acceptable.

    The design problem is that "acceptable regression" is exactly the mechanism
    by which a gate stops gating. Four constraints keep it a statement about one
    observation rather than a switch:

      * NO WILDCARDS. One scope, one target, one violation kind. There is no
        "all cases" and no "any kind", so a waiver cannot be widened by editing
        one field, only by writing more waivers -- each of which is visible in
        review.
      * A MAGNITUDE CEILING. The waiver authorises a regression of at most
        `max_magnitude`. Approving a 0.03 drop does not approve the 0.40
        collapse that lands next month on the same case.
      * BOUND TO THE COMPARISON. `comparison` is the digest of the exact
        baseline->candidate model pair (see `comparison_digest`). The next
        swap, and any re-baseline, produces a different digest and the waiver
        stops applying. A waiver cannot pre-approve a model nobody has run yet.
      * EXPIRY IS MANDATORY. There is no "no expiry" value. Every waiver dies.

    And `WAIVABLE_KINDS` keeps it away from the structural violations: a waiver
    is a statement about quality, and it is not entitled to certify that a case
    which did not run, or a metric noisier than its own threshold, is fine.

    A waived violation still appears in the report with `waived=True` and the
    reason attached. It is not removed -- "no silent anything" applies to the
    thing a human chose to accept just as much as to the thing they did not.
    """

    scope: WaiverScope
    target: str
    kind: ViolationKind
    max_magnitude: float
    reason: str
    approver: str
    expires_at: datetime
    comparison: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, WaiverScope):
            raise HarnessError("waiver scope must be a WaiverScope")
        if not isinstance(self.kind, ViolationKind):
            raise HarnessError("waiver kind must be a ViolationKind")
        if not self.target or self.target in {"*", "all"}:
            raise HarnessError(
                f"waiver target={self.target!r}: a waiver names exactly one target. "
                "A wildcard waiver is a disabled gate wearing a gate's name."
            )
        if self.kind not in WAIVABLE_KINDS:
            raise HarnessError(
                f"{self.kind.value} is not waivable: it says the measurement is broken, "
                "not that a quality change is acceptable"
            )
        expected_kinds = (
            {ViolationKind.CASE_REGRESSION, ViolationKind.NEW_CASE_BELOW_FLOOR}
            if self.scope is WaiverScope.CASE
            else {ViolationKind.CATEGORY_REGRESSION, ViolationKind.CATEGORY_FLOOR}
        )
        if self.kind not in expected_kinds:
            # Scope/kind mismatch would simply never match a violation, so the
            # waiver would look applied in review and do nothing at gate time.
            raise HarnessError(
                f"waiver scope={self.scope.value} cannot carry kind={self.kind.value}"
            )
        _require_finite("waiver.max_magnitude", self.max_magnitude)
        if self.max_magnitude < 0.0:
            raise HarnessError("waiver.max_magnitude must not be negative")
        if not self.reason.strip():
            raise HarnessError("waiver.reason is empty: an unexplained waiver is a bypass")
        if not self.approver.strip():
            raise HarnessError("waiver.approver is empty: someone has to own this")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            # A naive expiry is compared against whatever the runner thinks
            # local time is, so a waiver expires at different instants on
            # different machines -- or raises mid-comparison.
            raise HarnessError("waiver.expires_at must carry an explicit UTC offset")
        if not isinstance(self.comparison, str) or not re.fullmatch(
            r"[0-9a-f]{32}", self.comparison
        ):
            raise HarnessError(
                "waiver.comparison must be a comparison_digest() value "
                "(32 hex chars); a waiver not bound to one comparison is a bypass"
            )


@dataclass(frozen=True)
class Violation:
    """One reason the gate failed, or would have failed without a waiver."""

    kind: ViolationKind
    target: str
    category: str
    magnitude: float
    detail: str
    waived: bool = False
    waiver_reason: str | None = None
    waiver_approver: str | None = None

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        """Total order, including `detail`.

        (kind, category, target) alone is NOT total: one case can raise
        NONDETERMINISTIC for the baseline run and again for the candidate run,
        and those three fields are identical for both. `sorted` is stable, so
        the tie would be broken by generation order -- which is deterministic
        today and would stop being deterministic the moment anyone reorders the
        checks. Detail carries the run label and makes the order total now.
        """
        return (self.kind.value, self.category, self.target, self.detail)


@dataclass(frozen=True)
class CaseDelta:
    """Per-case comparison. `regression` is direction-adjusted and positive
    means worse, so every threshold in this module reads the same way regardless
    of which end of the metric is good."""

    case_id: str
    category: str
    metric: str
    direction: MetricDirection
    baseline: float
    candidate: float
    delta: float
    regression: float
    baseline_spread: float
    candidate_spread: float


@dataclass(frozen=True)
class CategoryReport:
    """Per-category comparison over the PAIRED cases only."""

    category: str
    metric: str
    direction: MetricDirection
    n_cases: int
    baseline_mean: float
    candidate_mean: float
    delta: float
    regression: float
    floor: float | None
    floor_ok: bool
    passed: bool


@dataclass(frozen=True)
class GateReport:
    """The verdict, and everything needed to argue with it."""

    passed: bool
    policy_id: str
    policy_digest: str
    comparison: str
    swapped_roles: tuple[str, ...]
    categories: tuple[CategoryReport, ...]
    case_deltas: tuple[CaseDelta, ...]
    violations: tuple[Violation, ...]
    new_cases: tuple[str, ...] = ()
    unused_waivers: tuple[str, ...] = ()
    expired_waivers: tuple[str, ...] = ()
    overall_baseline_mean: float | None = None
    overall_candidate_mean: float | None = None
    mean_masked_regression: bool = False

    @property
    def blocking(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if not v.waived)

    def summary(self) -> str:
        """Deterministic human-readable summary for the CI log.

        The mean line says "advisory" in every report, including the ones where
        it improved, because the whole point is that it is never the verdict.
        """
        lines = [
            f"{'PASS' if self.passed else 'FAIL'}  policy={self.policy_id} "
            f"digest={self.policy_digest} comparison={self.comparison}",
            f"  swapped roles: {', '.join(self.swapped_roles) or '(none)'}",
        ]
        for report in self.categories:
            floor = "-" if report.floor is None else f"{report.floor:.4f}"
            lines.append(
                f"  {report.category:<18} n={report.n_cases} "
                f"{report.baseline_mean:.4f} -> {report.candidate_mean:.4f} "
                f"regression {report.regression:+.4f} floor {floor} "
                f"{'ok' if report.passed else 'FAIL'}"
            )
        for violation in self.violations:
            mark = "WAIVED" if violation.waived else "BLOCK "
            lines.append(
                f"  {mark} {violation.kind.value:<22} {violation.target}: {violation.detail}"
            )
        if self.overall_baseline_mean is not None and self.overall_candidate_mean is not None:
            lines.append(
                f"  overall mean (advisory, never the verdict): "
                f"{self.overall_baseline_mean:.4f} -> {self.overall_candidate_mean:.4f}"
            )
        if self.mean_masked_regression:
            lines.append(
                "  NOTE: the overall mean IMPROVED while at least one category "
                "regressed. This is the failure this gate exists to catch."
            )
        return "\n".join(lines)


def _regression(baseline: float, candidate: float, direction: MetricDirection) -> float:
    """Positive means worse, in whichever direction the metric runs."""
    if direction is MetricDirection.HIGHER_IS_BETTER:
        return _q(baseline - candidate)
    return _q(candidate - baseline)


def _floor_violated(value: float, floor: float, direction: MetricDirection) -> bool:
    """Whether `value` is on the wrong side of the absolute floor.

    Exactly ON the floor passes: the floor is the worst ACCEPTABLE value, and a
    strict comparison here would make a policy stating "0.75 is acceptable"
    reject 0.75.
    """
    if direction is MetricDirection.HIGHER_IS_BETTER:
        return value < floor
    return value > floor


def _index_cases(
    cases: Sequence[BenchmarkCase], policy: Policy
) -> dict[str, BenchmarkCase]:
    indexed: dict[str, BenchmarkCase] = {}
    for case in cases:
        if case.case_id in indexed:
            raise CaseDefinitionError(f"duplicate case id {case.case_id!r}")
        if case.category not in policy.categories:
            raise CaseDefinitionError(
                f"{case.case_id}: category {case.category!r} is not declared by policy "
                f"{policy.policy_id} {list(policy.categories)}. A category the policy does "
                "not know has no floor, and its cases are missing from the category they "
                "were meant to protect."
            )
        indexed[case.case_id] = case
    if not indexed:
        raise CaseDefinitionError("no benchmark cases: the gate would pass vacuously")
    return indexed


def _category_axes(
    cases: Mapping[str, BenchmarkCase]
) -> dict[str, tuple[str, MetricDirection]]:
    """The single (metric, direction) each category is measured on.

    Refuses a category whose cases disagree. Averaging beat-alignment error in
    milliseconds together with a reel-acceptance rate produces a number with no
    meaning, and the mean of a higher-is-better and a lower-is-better metric
    cancels a real regression against an unrelated improvement.
    """
    axes: dict[str, tuple[str, MetricDirection]] = {}
    for case_id in sorted(cases):
        case = cases[case_id]
        axis = (case.metric, case.direction)
        existing = axes.setdefault(case.category, axis)
        if existing != axis:
            raise CaseDefinitionError(
                f"category {case.category!r} mixes metrics: {existing[0]}"
                f"/{existing[1].value} and {axis[0]}/{axis[1].value}. A mean over two "
                "different metrics is not a measurement of anything."
            )
    return axes


def _check_comparable(
    baseline: RunReport,
    candidate: RunReport,
    cases: Mapping[str, BenchmarkCase],
    under_test: Sequence[str],
) -> tuple[str, ...]:
    """Refuse comparisons that are not a controlled experiment. Returns the
    roles that actually changed."""
    declared = sorted(set(under_test))
    if len(declared) != len(list(under_test)):
        raise GateRefused(f"under_test contains duplicates: {list(under_test)}")

    baseline_roles = set(baseline.models.roles)
    candidate_roles = set(candidate.models.roles)
    if baseline_roles != candidate_roles:
        added = sorted(candidate_roles - baseline_roles)
        removed = sorted(baseline_roles - candidate_roles)
        raise GateRefused(
            f"pipeline shape differs: roles added {added}, roles removed {removed}. "
            "A pipeline with a different set of models is a different pipeline, and "
            "the delta would be attributed to whichever model happens to be named."
        )

    for run in (baseline, candidate):
        unpinned = run.models.unpinned_roles()
        if unpinned:
            raise GateRefused(
                f"{run.label}: roles {list(unpinned)} have weights_blake3=null. An "
                "unpinned model makes the run unreproducible, so a PASS recorded "
                "against it is not evidence of anything and cannot be re-checked."
            )

    changed = tuple(
        sorted(
            role
            for role in sorted(baseline_roles)
            if baseline.models.get(role).behaviour_key  # type: ignore[union-attr]
            != candidate.models.get(role).behaviour_key  # type: ignore[union-attr]
        )
    )

    unknown = sorted(set(declared) - baseline_roles)
    if unknown:
        raise GateRefused(f"under_test names roles that are not in the pipeline: {unknown}")

    uncontrolled = sorted(set(changed) - set(declared))
    if uncontrolled:
        raise GateRefused(
            f"roles {uncontrolled} changed but were not declared under test. Their "
            "weights or config digests differ between baseline and candidate, so any "
            "delta is the sum of several changes attributed to one of them. Re-run the "
            "baseline with the same pins, or declare them under test."
        )

    inert = sorted(set(declared) - set(changed))
    if inert:
        raise GateRefused(
            f"roles {inert} were declared under test but are byte-identical in both "
            "runs. Either the candidate loaded the baseline's weights (a wiring bug "
            "that produces a confident PASS for a model that never ran) or the swap "
            "is not the one you think it is."
        )

    for case_id in sorted(cases):
        case = cases[case_id]
        for role, pin in case.baseline_pins.pins:
            actual = baseline.models.get(role)
            if actual is None:
                raise GateRefused(
                    f"{case_id} pins role {role!r}, which the baseline run does not have"
                )
            if actual.behaviour_key != pin.behaviour_key:
                raise GateRefused(
                    f"{case_id}: baseline run's {role!r} is "
                    f"{actual.weights_blake3}/{actual.config_blake3}, but the case was "
                    f"authored against {pin.weights_blake3}/{pin.config_blake3}. The "
                    "baseline was regenerated with different weights or config and the "
                    "case was not updated, so its expected outcome describes a model "
                    "that is no longer on either side of this comparison."
                )
    return changed


def _check_coverage(run: RunReport, cases: Mapping[str, BenchmarkCase]) -> dict[str, CaseResult]:
    results = run.by_case()
    extra = sorted(set(results) - set(cases))
    if extra:
        raise GateRefused(
            f"{run.label}: results for cases {extra} that are not in the case list. The "
            "run was produced against a different set of cases than the one being gated."
        )
    return results


def _apply_waiver(
    violation: Violation,
    waivers: Sequence[Waiver],
    used: set[int],
    comparison: str,
    now: datetime | None,
    expired: set[int],
) -> Violation:
    if violation.kind not in WAIVABLE_KINDS:
        return violation
    for index, waiver in enumerate(waivers):
        if waiver.kind is not violation.kind:
            continue
        target_matches = (
            waiver.target == violation.target
            if waiver.scope is WaiverScope.CASE
            else waiver.target == violation.category
        )
        if not target_matches:
            continue
        if waiver.comparison != comparison:
            # Written for a different baseline->candidate pair. Not this one.
            continue
        assert now is not None  # evaluate() refuses waivers without `now`
        if waiver.expires_at <= now:
            expired.add(index)
            continue
        if violation.magnitude > waiver.max_magnitude:
            continue
        used.add(index)
        return Violation(
            kind=violation.kind,
            target=violation.target,
            category=violation.category,
            magnitude=violation.magnitude,
            detail=violation.detail,
            waived=True,
            waiver_reason=waiver.reason,
            waiver_approver=waiver.approver,
        )
    return violation


def evaluate(
    cases: Sequence[BenchmarkCase],
    baseline: RunReport,
    candidate: RunReport,
    policy: Policy,
    *,
    under_test: Sequence[str] = (),
    waivers: Sequence[Waiver] = (),
    now: datetime | None = None,
) -> GateReport:
    """Run the gate. Raises `GateRefused` when the comparison is not possible.

    `under_test` names the roles whose swap is being evaluated. It is required
    to be explicit rather than inferred from the digests: inferring it would
    mean the harness silently accepts whatever changed as intentional, which is
    precisely the uncontrolled-experiment problem it is meant to prevent. An
    empty `under_test` with identical model sets is legitimate and gates a code
    change rather than a model swap.

    `now` is passed in rather than read from the clock so that a verdict is a
    pure function of its inputs, and so waiver expiry is testable.
    """
    if waivers and now is None:
        # Waiver expiry is the only part of the verdict that depends on the
        # clock. Reading it here instead would make the gate's answer depend on
        # when CI happened to run, with nothing in the report to show it.
        raise HarnessError(
            "waivers were supplied but `now` was not: waiver expiry cannot be "
            "evaluated against an unstated instant"
        )
    # Sorted so that when two waivers could both excuse one violation, the same
    # one is chosen regardless of the order they were listed in the waiver file
    # -- otherwise the reason recorded in the report depends on file order.
    waivers = tuple(
        sorted(
            waivers,
            key=lambda w: (
                w.scope.value,
                w.target,
                w.kind.value,
                w.expires_at,
                w.max_magnitude,
                w.approver,
                w.reason,
            ),
        )
    )
    policy.validate()
    cases_by_id = _index_cases(cases, policy)
    axes = _category_axes(cases_by_id)
    swapped = _check_comparable(baseline, candidate, cases_by_id, under_test)
    baseline_results = _check_coverage(baseline, cases_by_id)
    candidate_results = _check_coverage(candidate, cases_by_id)
    comparison = comparison_digest(baseline.models, candidate.models)

    violations: list[Violation] = []
    deltas: list[CaseDelta] = []
    new_cases: list[str] = []
    # case_id -> (baseline value, candidate value), paired cases only.
    paired: dict[str, tuple[float, float]] = {}

    for case_id in sorted(cases_by_id):
        case = cases_by_id[case_id]
        base_result = baseline_results.get(case_id)
        cand_result = candidate_results.get(case_id)

        if cand_result is None:
            # HARD FAIL, NEVER SKIPPED. Dropping the case the candidate could
            # not process is the cheapest way to manufacture an improvement:
            # remove the hardest case and every mean rises.
            violations.append(
                Violation(
                    kind=ViolationKind.CASE_NOT_RUN,
                    target=case_id,
                    category=case.category,
                    magnitude=0.0,
                    detail=(
                        f"{candidate.label} produced no result. A missing case cannot be "
                        "skipped: excluding it would raise every mean it belongs to."
                    ),
                )
            )
            continue

        if base_result is None:
            if policy.on_missing_baseline is MissingBaseline.REFUSE:
                raise GateRefused(
                    f"{case_id}: no baseline result. Usually a stale baseline file; "
                    "proceeding would gate fewer cases than the case list claims. Set "
                    "on_missing_baseline=ADMIT_AS_NEW if this case is genuinely new."
                )
            new_cases.append(case_id)
            _, direction = axes[case.category]
            floor = policy.for_category(case.category).worst_acceptable_mean
            _check_spread(case, cand_result, candidate.label, policy, violations)
            if floor is not None and _floor_violated(cand_result.value, floor, direction):
                violations.append(
                    Violation(
                        kind=ViolationKind.NEW_CASE_BELOW_FLOOR,
                        target=case_id,
                        category=case.category,
                        magnitude=_q(abs(cand_result.value - floor)),
                        detail=(
                            f"new case scored {cand_result.value:.4f} against the "
                            f"{case.category} floor of {floor:.4f}; no delta is possible "
                            "without a baseline, so only the absolute bar applies"
                        ),
                    )
                )
            continue

        _check_spread(case, base_result, baseline.label, policy, violations)
        _check_spread(case, cand_result, candidate.label, policy, violations)

        drift = abs(base_result.value - case.expected)
        if drift > case.tolerance:
            violations.append(
                Violation(
                    kind=ViolationKind.BASELINE_DRIFT,
                    target=case_id,
                    category=case.category,
                    magnitude=_q(drift),
                    detail=(
                        f"baseline measured {base_result.value:.6f} but the case expects "
                        f"{case.expected:.6f} (tolerance {case.tolerance:.6f}). The "
                        "baseline no longer reproduces the case's own recorded outcome, "
                        "so every delta from it is measured from the wrong place."
                    ),
                )
            )

        regression = _regression(base_result.value, cand_result.value, case.direction)
        deltas.append(
            CaseDelta(
                case_id=case_id,
                category=case.category,
                metric=case.metric,
                direction=case.direction,
                baseline=base_result.value,
                candidate=cand_result.value,
                delta=_q(cand_result.value - base_result.value),
                regression=regression,
                baseline_spread=base_result.spread,
                candidate_spread=cand_result.spread,
            )
        )
        paired[case_id] = (base_result.value, cand_result.value)

        if regression > policy.max_case_regression:
            violations.append(
                Violation(
                    kind=ViolationKind.CASE_REGRESSION,
                    target=case_id,
                    category=case.category,
                    magnitude=regression,
                    detail=(
                        f"{case.metric} went {base_result.value:.4f} -> "
                        f"{cand_result.value:.4f} ({case.direction.value}), a regression "
                        f"of {regression:.4f} against a limit of "
                        f"{policy.max_case_regression:.4f}"
                    ),
                )
            )

    category_reports: list[CategoryReport] = []
    for category in policy.categories:
        members = sorted(
            case_id for case_id in paired if cases_by_id[case_id].category == category
        )
        declared = [c for c in cases_by_id.values() if c.category == category]
        if not declared:
            # A category that has vanished from the benchmark set cannot regress
            # because it is no longer measured. That is the perfect hidden
            # regression, so its absence is itself a failure.
            violations.append(
                Violation(
                    kind=ViolationKind.CATEGORY_MISSING,
                    target=category,
                    category=category,
                    magnitude=0.0,
                    detail=(
                        "policy requires this category but the case list contains no "
                        "cases for it; an unmeasured category cannot fail, which is "
                        "not the same as passing"
                    ),
                )
            )
            continue

        category_policy = policy.for_category(category)
        metric, direction = axes[category]

        if len(members) < policy.min_cases_per_category:
            violations.append(
                Violation(
                    kind=ViolationKind.THIN_CATEGORY,
                    target=category,
                    category=category,
                    magnitude=float(policy.min_cases_per_category - len(members)),
                    detail=(
                        f"{len(members)} comparable case(s), policy requires "
                        f"{policy.min_cases_per_category}. A mean over too few cases is "
                        "one photo's opinion and cannot detect a category regression."
                    ),
                )
            )

        if not members:
            continue

        baseline_mean = _mean([paired[c][0] for c in members])
        candidate_mean = _mean([paired[c][1] for c in members])
        regression = _regression(baseline_mean, candidate_mean, direction)
        floor = category_policy.worst_acceptable_mean
        floor_ok = floor is None or not _floor_violated(candidate_mean, floor, direction)

        if regression > category_policy.max_mean_regression:
            violations.append(
                Violation(
                    kind=ViolationKind.CATEGORY_REGRESSION,
                    target=category,
                    category=category,
                    magnitude=regression,
                    detail=(
                        f"category mean {baseline_mean:.4f} -> {candidate_mean:.4f} "
                        f"({metric}, {direction.value}), a regression of "
                        f"{regression:.4f} against a limit of "
                        f"{category_policy.max_mean_regression:.4f}"
                    ),
                )
            )
        if not floor_ok:
            violations.append(
                Violation(
                    kind=ViolationKind.CATEGORY_FLOOR,
                    target=category,
                    category=category,
                    magnitude=_q(abs(candidate_mean - float(floor))),  # type: ignore[arg-type]
                    detail=(
                        f"category mean {candidate_mean:.4f} is on the wrong side of the "
                        f"absolute floor {float(floor):.4f} ({direction.value}). An "
                        "absolute floor is what stops a category being ratcheted down "
                        "one acceptable-looking swap at a time."
                    ),
                )
            )

        category_reports.append(
            CategoryReport(
                category=category,
                metric=metric,
                direction=direction,
                n_cases=len(members),
                baseline_mean=baseline_mean,
                candidate_mean=candidate_mean,
                delta=_q(candidate_mean - baseline_mean),
                regression=regression,
                floor=floor,
                floor_ok=floor_ok,
                passed=(
                    floor_ok and regression <= category_policy.max_mean_regression
                ),
            )
        )

    used: set[int] = set()
    expired: set[int] = set()
    resolved = tuple(
        sorted(
            (
                _apply_waiver(v, waivers, used, comparison, now, expired)
                for v in violations
            ),
            key=lambda v: v.sort_key,
        )
    )

    # The overall mean is only defined when every case measures the same thing.
    # Averaging milliseconds with acceptance rates would produce a headline
    # number that moves for reasons nobody can name -- and this number is
    # advisory anyway, so a fabricated version of it has no upside at all.
    all_axes = {axes[cases_by_id[c].category] for c in paired}
    overall_baseline: float | None = None
    overall_candidate: float | None = None
    masked = False
    if paired and len(all_axes) == 1:
        direction = next(iter(all_axes))[1]
        overall_baseline = _mean([paired[c][0] for c in sorted(paired)])
        overall_candidate = _mean([paired[c][1] for c in sorted(paired)])
        overall_regression = _regression(overall_baseline, overall_candidate, direction)
        masked = overall_regression < 0.0 and any(
            report.regression > 0.0 for report in category_reports
        )

    return GateReport(
        passed=not any(not v.waived for v in resolved),
        policy_id=policy.policy_id,
        policy_digest=policy.digest(),
        comparison=comparison,
        swapped_roles=swapped,
        categories=tuple(category_reports),
        case_deltas=tuple(sorted(deltas, key=lambda d: (d.category, d.case_id))),
        violations=resolved,
        new_cases=tuple(sorted(new_cases)),
        unused_waivers=tuple(
            sorted(
                f"{w.scope.value}:{w.target}:{w.kind.value}"
                for i, w in enumerate(waivers)
                if i not in used and i not in expired
            )
        ),
        expired_waivers=tuple(
            sorted(
                f"{w.scope.value}:{w.target}:{w.kind.value}"
                for i, w in enumerate(waivers)
                if i in expired
            )
        ),
        overall_baseline_mean=overall_baseline,
        overall_candidate_mean=overall_candidate,
        mean_masked_regression=masked,
    )


def _check_spread(
    case: BenchmarkCase,
    result: CaseResult,
    run_label: str,
    policy: Policy,
    violations: list[Violation],
) -> None:
    """Flag a case whose own noise is wider than the threshold gating it.

    Not a rejection of the value and not an averaging-away: the value is still
    reported. What is reported alongside it is that a delta of the size this
    gate cares about cannot be distinguished from re-running the same model
    twice, so a PASS on this case carries no information.
    """
    if result.replicates < 2:
        return
    if result.spread > policy.max_nondeterminism:
        violations.append(
            Violation(
                kind=ViolationKind.NONDETERMINISTIC,
                target=case.case_id,
                category=case.category,
                magnitude=result.spread,
                detail=(
                    f"{run_label}: {result.replicates} replicates spread "
                    f"{result.spread:.6f}, above the {policy.max_nondeterminism:.6f} "
                    "noise allowance. A regression smaller than the run-to-run spread "
                    "is indistinguishable from noise, so the gate is blind on this case."
                ),
            )
        )
