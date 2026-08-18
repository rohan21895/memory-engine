"""The benchmark content layer: how a case is declared, and what it may claim.

`harness.py` is a comparator. It takes two sets of recorded numbers and says
whether the second is worse than the first. It does not produce numbers, and
until this module existed nothing did -- the committed gate files carried a
`candidate` array of literals, so the gate compared one committed list against
another committed list and passed. That is a gate with nothing behind it:
CLAUDE.md rule 7 says model swaps are gated by the eval harness, and a
comparator fed by hand gates nothing.

This module, `probes.py` and `runner.py` are the content. A suite declares
cases; a probe MEASURES each case now, on this machine, against code that is on
disk; the runner composes {suite, committed baseline, freshly measured
candidate} and hands it to the comparator. The candidate is never written down
by a human.

SIX THINGS A CASE MUST SAY, AND WHAT EACH ONE STOPS

1. `claim_class` -- what kind of statement this number is.
   PLUMBING / DETERMINISM / QUALITY, checked against the CEILING of the library
   the case runs on and of the probe that measures it (`library.ClaimClass`). A
   face-detection number measured on `make_library.py`'s cartoon ovals is a
   plumbing number. Every file in `scripts/demo/` already says so in prose, and
   prose is obeyed until somebody is quoting a figure into a model card at
   midnight. Here it is structural: a synthetic library has
   `claim_ceiling: plumbing`, so a case declaring `claim_class: quality`
   against it does not load, the suite does not compile, and the number is
   never produced. The class is also stamped into the front of the case's
   description, which is what the gate report prints, so the artifact a reader
   opens carries the caveat rather than pointing at it.

2. `measures` and `does_not_measure` -- one sentence each, both REQUIRED, both
   non-empty. The second is the load-bearing one. "Face detection benchmark:
   0.98" is a sentence somebody will repeat; "does not measure: whether the
   detector finds real faces; the subjects are drawn ovals" is the sentence
   that has to travel with it. A case that cannot say what it does not measure
   has not been thought about.

3. `falsifications` -- the deliberate breaks this case is known to catch, and
   the score it must not exceed under each. A benchmark that has never been seen
   failing is not evidence; it is a number that happens to be printed.
   `tests/test_falsification.py` runs every one of them and asserts the case
   actually fails. A case with no falsification does not load, so it cannot be
   added without one, and a case whose falsification stops working turns the
   suite red rather than going quietly green.

   The declared set must EQUAL the set the probe implements -- not a subset.
   A subset would let somebody add a break to a probe, never bound it, and have
   the suite still compile; the unbounded break would then be the one nobody
   ever ran.

4. `probe` + `params` -- what runs. Not free text: the probe id is resolved
   against the registry at load time, so a suite naming a probe that does not
   exist fails to compile rather than reporting an unrun case.

5. `expected` -- the absolute promise, which no waiver may forgive
   (`harness` decision: `case_below_expected` is not waivable). A case whose
   candidate falls under it fails even if it did not regress.

6. `baseline` -- what a previous run measured, and, at suite level,
   `baseline_sources`: the BLAKE3 of the probe source files that produced those
   numbers. Recording the digest of the code beside the number is what lets the
   harness tell "the code changed and the score moved" from "the code did not
   change and the score moved anyway", and only the second of those is a
   catastrophe.

WHY A SUITE, NOT A DIRECTORY OF CASES

`harness._single_model_set` requires one model set per run, so the unit that
can be gated together is the unit whose code identity moves together. That unit
is the suite. It also gives the suite one place to say `runs_in_ci`, and one
place to be honest about why not.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from . import harness as hz
from .library import (
    ClaimClass,
    LibraryDeclaration,
    LibraryError,
    load_library_declaration,
)

__all__ = [
    "BENCHMARK_DIR",
    "BenchmarkDeclarationError",
    "CaseDeclaration",
    "Falsification",
    "RecordedBaseline",
    "SuiteDeclaration",
    "canonical_params",
    "library_paths",
    "load_policy",
    "load_suite",
    "suite_paths",
]

# Repository root, found from this file rather than from the current working
# directory. A benchmark that resolves its own source files relative to $PWD
# measures a different thing depending on where it was invoked from.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
BENCHMARK_DIR = PACKAGE_ROOT / "benchmarks"


class BenchmarkDeclarationError(Exception):
    """A suite or case file is not a benchmark.

    Deliberately distinct from `harness.SuiteError`: this is raised before any
    comparison exists, and the runner maps it onto EXIT_USAGE (3) -- "this was
    never set up" -- rather than onto anything that reads as a quality signal.
    """


def canonical_params(params: Mapping[str, Any]) -> str:
    """A parameter mapping as one canonical string.

    `sort_keys` and no whitespace, so a mapping written in a different key order
    digests identically, and every value rendered by `json.dumps` so a float is
    written the same way on every run. This string is digested into the run's
    `config_blake3`, which is what makes "the same code at a different operating
    point" a different model to `harness.ModelSet`.
    """
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _fields(
    where: str,
    value: object,
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> dict[str, Any]:
    """Strict field check, mirroring `harness._fields`.

    Unknown fields are refused for the reason the harness gives: a misspelled
    knob takes its default silently, so the file in the repository would
    describe a different benchmark from the one that ran.
    """
    if not isinstance(value, Mapping):
        raise BenchmarkDeclarationError(
            f"{where} must be a JSON object, got {type(value).__name__}"
        )
    keys = set(value)
    missing = sorted(set(required) - keys)
    if missing:
        raise BenchmarkDeclarationError(
            f"{where} is missing required field(s): {', '.join(missing)}"
        )
    unknown = sorted(keys - set(required) - set(optional))
    if unknown:
        raise BenchmarkDeclarationError(
            f"{where} has unknown field(s): {', '.join(unknown)}"
        )
    return dict(value)


def _sentence(where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkDeclarationError(f"{where} must be a non-empty sentence")
    if "\n" in value:
        raise BenchmarkDeclarationError(
            f"{where} must be one line; it is stamped into a report a person reads"
        )
    return value.strip()


@dataclass(frozen=True)
class Falsification:
    """The break this case is known to catch.

    `max_goodness` is an upper bound on the score under the break, on the
    harness's uniformly-higher-is-better [0,1] scale. An upper bound rather than
    an equality because a broken implementation is allowed to be broken in more
    ways than the author predicted -- pinning the exact broken score would make
    the test fail when the break got worse, which is backwards.
    """

    mode: str
    max_goodness: float
    why: str

    def __post_init__(self) -> None:
        if not self.mode:
            raise BenchmarkDeclarationError("falsification.mode must be non-empty")
        if not isinstance(self.max_goodness, (int, float)) or isinstance(
            self.max_goodness, bool
        ):
            raise BenchmarkDeclarationError(
                f"falsification {self.mode!r}: max_goodness must be a number"
            )
        if not 0.0 <= self.max_goodness < 1.0:
            # Strictly below 1.0: a falsification permitted to score a perfect
            # 1.0 is not a falsification, it is a second name for the passing
            # case, and the test that runs it would assert nothing.
            raise BenchmarkDeclarationError(
                f"falsification {self.mode!r}: max_goodness must be in [0,1); a break "
                "that may still score a perfect 1.0 demonstrates nothing"
            )


@dataclass(frozen=True)
class RecordedBaseline:
    """What a previous run measured for one case."""

    samples: tuple[float, ...]
    inputs_digest: str
    measured_at: str
    measured_by: str

    def __post_init__(self) -> None:
        if not self.samples:
            raise BenchmarkDeclarationError("baseline.samples must be non-empty")
        for value in self.samples:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise BenchmarkDeclarationError("baseline.samples must be numbers")
            if not 0.0 <= value <= 1.0:
                raise BenchmarkDeclarationError("baseline.samples must lie in [0,1]")
        if not self.measured_by.strip():
            raise BenchmarkDeclarationError(
                "baseline.measured_by must name who or what ran it"
            )


@dataclass(frozen=True)
class CaseDeclaration:
    """One benchmark case, fully declared.

    `category` is checked against the suite's own policy categories by
    `harness.evaluate`, which emits `unknown_category` for a mismatch. It is
    deliberately NOT constrained to the six library categories from the build
    plan (`harness.BENCHMARK_CATEGORIES`): nothing in this repository is
    measured on Indian weddings or on drone footage, and filing a determinism
    case under `baby_family` would put a number meaning "the code repeats" into
    the bucket that is supposed to hold "the model is good at photographs of
    babies". Suites declare their own category set; the build-plan six become
    live when a real library arrives (docs/benchmark-libraries.md).
    """

    case_id: str
    category: str
    claim_class: ClaimClass
    probe_id: str
    params: Mapping[str, Any]
    expected: float
    repeats: int
    measures: str
    does_not_measure: str
    falsifications: tuple[Falsification, ...]
    baseline: RecordedBaseline | None

    def description(self) -> str:
        """The one-line caveat that travels with the number into the report.

        Front-loaded with the claim class in brackets so it survives being
        truncated, quoted, or read in a hurry.
        """
        return (
            f"[{self.claim_class.value.upper()}] {self.measures} "
            f"DOES NOT MEASURE: {self.does_not_measure}"
        )


@dataclass(frozen=True)
class SuiteDeclaration:
    """A committed suite file: its cases, its policy, and what it needs to run."""

    suite_id: str
    description: str
    as_of: date
    policy: Mapping[str, Any]
    requires_library: LibraryDeclaration | None
    runs_in_ci: bool
    ci_exclusion_reason: str
    baseline_sources: Mapping[str, str]
    waivers: tuple[hz.Waiver, ...]
    cases: tuple[CaseDeclaration, ...]
    source: Path

    def by_id(self) -> dict[str, CaseDeclaration]:
        return {case.case_id: case for case in self.cases}


def load_policy(suite: SuiteDeclaration) -> hz.GatePolicy:
    """The suite's policy, parsed by the harness's own loader.

    Routed through `harness._load_policy` rather than reimplemented so a knob
    added to `GatePolicy` is understood here without a second edit, and so an
    unknown knob is refused by the same code that refuses it in a gate file.
    """
    return hz._load_policy(f"{suite.source}.policy", dict(suite.policy))


_SUITE_REQUIRED = (
    "suite_id",
    "description",
    "as_of",
    "policy",
    "runs_in_ci",
    "cases",
)
_SUITE_OPTIONAL = (
    "requires_library",
    "ci_exclusion_reason",
    "baseline_sources",
    "waivers",
)

_CASE_REQUIRED = (
    "case_id",
    "category",
    "claim_class",
    "probe",
    "expected",
    "repeats",
    "measures",
    "does_not_measure",
    "falsifications",
)
_CASE_OPTIONAL = ("params", "baseline")


def load_suite(path: Path | str) -> SuiteDeclaration:
    """Parse and validate a `*.suite.json`.

    Every failure here is a refusal to compile, never a warning. A suite that
    half-loaded would run the cases it understood and silently drop the rest,
    and a dropped case is the cheapest way to make a benchmark green.
    """
    # Imported here rather than at module scope: probes.py imports this module,
    # so a top-level import would be circular.
    from .probes import PROBES

    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BenchmarkDeclarationError(f"no suite at {path}") from error
    except json.JSONDecodeError as error:
        raise BenchmarkDeclarationError(f"{path} is not valid JSON: {error}") from error

    fields = _fields(
        str(path), document, required=_SUITE_REQUIRED, optional=_SUITE_OPTIONAL
    )

    try:
        as_of = date.fromisoformat(str(fields["as_of"]))
    except ValueError as error:
        raise BenchmarkDeclarationError(f"{path}: as_of must be YYYY-MM-DD") from error

    if not isinstance(fields["runs_in_ci"], bool):
        raise BenchmarkDeclarationError(
            f"{path}: runs_in_ci must be a boolean. A suite that does not say whether "
            "CI covers it will be assumed to be covered by whoever reads the directory"
        )
    exclusion = str(fields.get("ci_exclusion_reason", "")).strip()
    if fields["runs_in_ci"] and exclusion:
        raise BenchmarkDeclarationError(
            f"{path}: a suite that runs in CI must not also declare why it does not"
        )
    if not fields["runs_in_ci"] and not exclusion:
        # The reason is required so that "CI does not run this" is a sentence
        # somebody wrote and a reviewer read, rather than a false flag that
        # quietly removes a suite from every build.
        raise BenchmarkDeclarationError(
            f"{path}: runs_in_ci is false and no ci_exclusion_reason is given. A "
            "suite CI does not run must say what it needs, or it becomes a suite "
            "nobody runs"
        )

    library: LibraryDeclaration | None = None
    declared_library = fields.get("requires_library")
    if declared_library is not None:
        library_path = BENCHMARK_DIR / "libraries" / f"{declared_library}.library.json"
        try:
            library = load_library_declaration(library_path)
        except LibraryError as error:
            raise BenchmarkDeclarationError(f"{path}: {error}") from error
        if fields["runs_in_ci"]:
            raise BenchmarkDeclarationError(
                f"{path}: a suite requiring a benchmark library cannot claim to run in "
                "CI. The library is not in the repository, so every case would be "
                "unmeasured on every build, and an unmeasured case that does not turn "
                "the build red is a case nobody is running"
            )

    if not isinstance(fields["policy"], Mapping):
        raise BenchmarkDeclarationError(f"{path}: policy must be a JSON object")

    baseline_sources = fields.get("baseline_sources") or {}
    if not isinstance(baseline_sources, Mapping):
        raise BenchmarkDeclarationError(f"{path}: baseline_sources must be an object")
    for probe_id, digest in baseline_sources.items():
        if not isinstance(digest, str) or len(digest) != 64:
            raise BenchmarkDeclarationError(
                f"{path}: baseline_sources[{probe_id}] must be a BLAKE3 hex digest"
            )

    # Parsed by the harness's own loader, which enforces the cap on max_drop,
    # the 90-day horizon and the mandatory approver and reason.
    waivers = tuple(
        hz._load_waiver(f"{path}.waivers[{index}]", entry)
        for index, entry in enumerate(fields.get("waivers", []) or [])
    )
    for waiver in waivers:
        if waiver.approved_on > as_of:
            # The suite's `as_of` is the clock this gate runs against, and a
            # waiver approved in that clock's future outlives its own horizon:
            # the 90-day cap is measured from `approved_on`, so dating an
            # approval forward is how a waiver quietly becomes permanent.
            # Keeping a waiver alive must stay what it is -- a reviewed edit
            # that moves `as_of`, visible in the diff.
            raise BenchmarkDeclarationError(
                f"{path}: the waiver for {waiver.case_id} was approved on "
                f"{waiver.approved_on.isoformat()}, after the suite's as_of "
                f"{as_of.isoformat()}; a waiver cannot be approved in the future of "
                "the clock it is evaluated against"
            )

    raw_cases = fields["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise BenchmarkDeclarationError(f"{path}: cases must be a non-empty array")

    cases: list[CaseDeclaration] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_cases):
        where = f"{path}.cases[{index}]"
        case_fields = _fields(
            where, entry, required=_CASE_REQUIRED, optional=_CASE_OPTIONAL
        )
        case_id = str(case_fields["case_id"])
        if case_id in seen:
            raise BenchmarkDeclarationError(f"{path}: duplicate case_id {case_id!r}")
        seen.add(case_id)

        try:
            claim = ClaimClass(case_fields["claim_class"])
        except ValueError as error:
            raise BenchmarkDeclarationError(
                f"{where}: claim_class must be one of "
                + ", ".join(c.value for c in ClaimClass)
            ) from error

        probe_id = str(case_fields["probe"])
        probe = PROBES.get(probe_id)
        if probe is None:
            raise BenchmarkDeclarationError(
                f"{where}: no probe named {probe_id!r}. Known probes: "
                + ", ".join(sorted(PROBES))
            )

        # The ceiling checks. This is the containment that keeps a plumbing
        # number from ever being produced as a quality number.
        if library is not None and not library.permits(claim):
            raise BenchmarkDeclarationError(
                f"{where}: case claims {claim.value} on library {library.ref}, whose "
                f"ceiling is {library.claim_ceiling.value}. {library.description}"
            )
        # The probe's own ceiling binds even with no library at all: a probe
        # that runs a detector over drawn ovals is a plumbing probe wherever
        # its inputs came from.
        if not probe.claim_ceiling.permits(claim):
            raise BenchmarkDeclarationError(
                f"{where}: case claims {claim.value} but probe {probe_id!r} can only "
                f"support {probe.claim_ceiling.value}"
            )
        missing_requirements = tuple(
            need for need in probe.requires if need not in _SATISFIABLE
        )
        if missing_requirements:  # pragma: no cover - guards a typo in probes.py
            raise BenchmarkDeclarationError(
                f"{where}: probe {probe_id!r} declares an unknown requirement "
                f"{missing_requirements}"
            )
        if probe.requires and fields["runs_in_ci"]:
            raise BenchmarkDeclarationError(
                f"{where}: probe {probe_id!r} requires {', '.join(probe.requires)}, "
                "which CI does not have. A case that cannot run in CI must not sit "
                "in a suite that claims to"
            )

        raw_falsifications = case_fields["falsifications"]
        if not isinstance(raw_falsifications, list) or not raw_falsifications:
            raise BenchmarkDeclarationError(
                f"{where}.falsifications must be a non-empty array"
            )
        falsifications: list[Falsification] = []
        for position, raw in enumerate(raw_falsifications):
            falsification_fields = _fields(
                f"{where}.falsifications[{position}]",
                raw,
                required=("mode", "max_goodness", "why"),
            )
            falsifications.append(
                Falsification(
                    mode=str(falsification_fields["mode"]),
                    max_goodness=falsification_fields["max_goodness"],
                    why=_sentence(
                        f"{where}.falsifications[{position}].why",
                        falsification_fields["why"],
                    ),
                )
            )
        declared_modes = [f.mode for f in falsifications]
        if len(set(declared_modes)) != len(declared_modes):
            raise BenchmarkDeclarationError(
                f"{where}.falsifications names the same mode twice"
            )
        if set(declared_modes) != set(probe.falsifications):
            # Equality, not containment. A subset lets a break be added to a
            # probe and never bounded, and the unbounded one is then the break
            # nobody runs; a superset names a break the probe cannot apply, and
            # the test for it would pass by never executing.
            raise BenchmarkDeclarationError(
                f"{where}.falsifications declares {sorted(declared_modes)} but probe "
                f"{probe_id!r} implements {sorted(probe.falsifications)}; every break "
                "a probe implements must be bounded by the case that uses it"
            )

        repeats = case_fields["repeats"]
        if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
            raise BenchmarkDeclarationError(f"{where}: repeats must be an integer >= 1")

        expected = case_fields["expected"]
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            raise BenchmarkDeclarationError(f"{where}: expected must be a number")
        if not 0.0 <= float(expected) <= 1.0:
            raise BenchmarkDeclarationError(f"{where}: expected must lie in [0,1]")

        params = case_fields.get("params") or {}
        if not isinstance(params, Mapping):
            raise BenchmarkDeclarationError(f"{where}: params must be a JSON object")
        unknown_params = sorted(set(params) - set(probe.param_names))
        if unknown_params:
            raise BenchmarkDeclarationError(
                f"{where}: probe {probe_id!r} has no parameter(s) "
                f"{', '.join(unknown_params)}"
            )

        baseline = None
        if case_fields.get("baseline") is not None:
            baseline_fields = _fields(
                f"{where}.baseline",
                case_fields["baseline"],
                required=("samples", "inputs_digest", "measured_at", "measured_by"),
            )
            samples = baseline_fields["samples"]
            if not isinstance(samples, list):
                raise BenchmarkDeclarationError(
                    f"{where}.baseline.samples must be an array"
                )
            if len(samples) != repeats:
                # The recorded sample count and the declared repeat count are
                # the same statement made twice. If they drift, a policy
                # requiring N repeats would be satisfied by a candidate the
                # baseline never matched, and the spread comparison between the
                # two sides would be measuring different amounts of evidence.
                raise BenchmarkDeclarationError(
                    f"{where}.baseline has {len(samples)} sample(s) against "
                    f"repeats={repeats}; re-record the baseline"
                )
            baseline = RecordedBaseline(
                samples=tuple(samples),
                inputs_digest=str(baseline_fields["inputs_digest"]),
                measured_at=str(baseline_fields["measured_at"]),
                measured_by=str(baseline_fields["measured_by"]),
            )

        cases.append(
            CaseDeclaration(
                case_id=case_id,
                category=str(case_fields["category"]),
                claim_class=claim,
                probe_id=probe_id,
                params=dict(params),
                expected=float(expected),
                repeats=repeats,
                measures=_sentence(f"{where}.measures", case_fields["measures"]),
                does_not_measure=_sentence(
                    f"{where}.does_not_measure", case_fields["does_not_measure"]
                ),
                falsifications=tuple(falsifications),
                baseline=baseline,
            )
        )

    used = {case.probe_id for case in cases}
    baselined = {case.probe_id for case in cases if case.baseline is not None}
    unsourced = sorted(baselined - set(baseline_sources))
    if unsourced:
        # A recorded number with no record of the code that produced it cannot
        # be compared honestly: every run would look like a no-op, which
        # disables the no-op drift check on exactly the runs where the code
        # changed. Caught here rather than at measure time so it reads as "this
        # suite was never set up" and not as a quality signal.
        raise BenchmarkDeclarationError(
            f"{path}: cases baselined against probe(s) {', '.join(unsourced)} with no "
            "baseline_sources entry; a baseline that cannot say which code produced "
            "it is not a baseline. Re-run `runner record`"
        )
    stale = sorted(set(baseline_sources) - used)
    if stale:
        # A digest left behind for a probe the suite no longer uses is a claim
        # about code nothing measures, and it would keep validating forever.
        raise BenchmarkDeclarationError(
            f"{path}: baseline_sources names probe(s) {', '.join(stale)} that no "
            "case uses"
        )

    return SuiteDeclaration(
        suite_id=str(fields["suite_id"]),
        description=str(fields["description"]),
        as_of=as_of,
        policy=dict(fields["policy"]),
        requires_library=library,
        runs_in_ci=bool(fields["runs_in_ci"]),
        ci_exclusion_reason=exclusion,
        baseline_sources=dict(baseline_sources),
        waivers=waivers,
        cases=tuple(cases),
        source=path,
    )


# What a ProbeContext can actually supply. Kept beside the loader so a probe
# declaring a requirement nothing can satisfy fails at load rather than at
# measure time, where it would look like a missing input.
_SATISFIABLE = frozenset({"library", "weights"})


def suite_paths() -> list[Path]:
    """Every committed suite, in a stable order."""
    return sorted(BENCHMARK_DIR.glob("*.suite.json"))


def library_paths() -> list[Path]:
    """Every committed library declaration, in a stable order."""
    return sorted((BENCHMARK_DIR / "libraries").glob("*.library.json"))
