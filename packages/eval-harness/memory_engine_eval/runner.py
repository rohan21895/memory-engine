"""The command that MEASURES, then gates. The half `harness.py` cannot supply.

`harness.py` is a comparator: hand it two sets of recorded numbers and it says,
carefully, whether the second is worse than the first. It never produces a
number. Before this module existed, both committed gate files carried a
`candidate` array of literals, so CI compared one committed list against another
committed list -- and a comparison between two files that only change together
passes forever. CLAUDE.md rule 7 ("model swaps are gated by the eval harness in
CI") rested on that.

This module closes the loop:

    suite declaration  ->  probes MEASURE, here, now, against code on disk
                       ->  {suite, committed baseline, fresh candidate}
                       ->  harness.evaluate
                       ->  exit code

The candidate side is never written by a human. `record` exists to move a
baseline, and it is a separate, explicit command whose output is a diff a
reviewer reads.

WHAT THE RUN'S MODEL SET IS, AND WHY IT IS THE WHOLE SUITE'S

`harness._single_model_set` requires every result in one run to carry the same
ModelSet, and it is right to: a run where half the cases were scored by the old
weights still resident in the runtime describes no model at all. So a suite's
ModelSet is one pin per probe the suite uses:

    model_id        the probe id
    version         the suite id, so two suites over one probe never collide
    weights_blake3  BLAKE3 of the probe's declared source files -- the code IS
                    the model here, and this is the honest answer to "what
                    produced this number"
    config_blake3   BLAKE3 of every (case_id, params) pair in this suite that
                    uses the probe -- the operating points, all of them

Both consequences are wanted. Edit `dedupe.py` and every case in the suite is
compared as a real delta, so a change that drops burst recovery fails the gate
in the PR that makes it. Edit nothing and the run is a no-op, which arms the
sharpest check the harness has: identical digests, so any movement at all means
something outside the code moved.

It also means changing one case's params invalidates the baseline of every case
sharing that probe. That is loud rather than silent, and loud is the right side
of this trade: the alternative is a per-case pin under which a parameter change
looks like a quality change.

THREE ANSWERS, THE SAME THREE THE HARNESS GIVES

    0  every suite ran and passed.
    1  a suite ran and something regressed. A quality signal.
    2  nothing was measured: a probe could not run (no benchmark library on
       this machine, no ONNX weights, a missing recorded input), or the
       comparison itself was refused. NOT a quality signal, and never green.
    3  a suite, library declaration or gate could not be set up at all.

A missing prerequisite lands on 2 and not on 0, which is the entire point. A
runner that skipped the library-backed cases when the library was absent would
report a green build for a suite nobody ran -- the exact failure
`docs/architecture.md` records three separate times ("a skip must never share an
exit code with a pass").
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from . import benchmarks as bench
from . import harness as hz
from . import library as lib
from . import probes as probe_module

__all__ = [
    "MeasuredCase",
    "MeasuredSuite",
    "SuiteOutcome",
    "build_gate_input",
    "main",
    "measure_suite",
    "model_set_for",
    "run_suite",
]


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasuredCase:
    case_id: str
    samples: tuple[float, ...]
    inputs_digest: str


@dataclass(frozen=True)
class MeasuredSuite:
    suite: bench.SuiteDeclaration
    models: hz.ModelSet
    cases: tuple[MeasuredCase, ...]


def model_set_for(
    suite: bench.SuiteDeclaration, *, sources: Mapping[str, str] | None = None
) -> hz.ModelSet:
    """One pin per probe used by the suite. See the module docstring.

    `sources` overrides the probe source digests, and exists for exactly one
    caller: reconstructing the BASELINE's model set from what was recorded. A
    baseline pin has to be the digests that were true when the baseline was
    measured, not the digests that are true now -- otherwise every comparison
    would be a no-op and the no-op drift check would fire on real changes.
    """
    pins: list[hz.ModelPin] = []
    for probe_id in sorted({case.probe_id for case in suite.cases}):
        probe = probe_module.PROBES[probe_id]
        params_parts: list[str] = []
        for case in sorted(suite.cases, key=lambda c: c.case_id):
            if case.probe_id != probe_id:
                continue
            params_parts.append(case.case_id)
            params_parts.append(bench.canonical_params(case.params))
        weights = (
            sources[probe_id] if sources is not None else probe.sources_digest()
        )
        pins.append(
            hz.ModelPin(
                # The probe id verbatim: `harness._SLUG` permits underscores, and
                # a pin whose model_id is a prettified version of the probe id is
                # a name a reader has to translate back before they can grep for
                # it.
                model_id=probe_id,
                version=suite.suite_id,
                weights_blake3=weights,
                config_blake3=probe_module.digest_strings(
                    [probe_id, suite.suite_id, *params_parts]
                ),
            )
        )
    return hz.ModelSet(pins)


def measure_suite(
    suite: bench.SuiteDeclaration, context: probe_module.ProbeContext
) -> MeasuredSuite:
    """Run every case in the suite, `repeats` times each.

    A `ProbeError` propagates. It is never converted into a score: "we could not
    measure this" and "the code is completely broken" must not land on the same
    axis, or a waiver written for the second silently forgives the first.
    """
    measured: list[MeasuredCase] = []
    for case in sorted(suite.cases, key=lambda c: c.case_id):
        probe = probe_module.PROBES[case.probe_id]
        inputs, inputs_digest = probe.load(context, case.params)
        samples = tuple(
            _require_unit(case.case_id, probe.measure(inputs, case.params))
            for _ in range(case.repeats)
        )
        measured.append(
            MeasuredCase(
                case_id=case.case_id, samples=samples, inputs_digest=inputs_digest
            )
        )
    return MeasuredSuite(
        suite=suite, models=model_set_for(suite), cases=tuple(measured)
    )


def _require_unit(case_id: str, value: object) -> float:
    """A probe returns a Unit or the run stops.

    A probe that returned a raw-scale number -- a beat error of 47.0ms, a count
    of 12 -- would dominate every mean it entered while the category still
    looked measured. `harness._require_unit` would catch it too, but by then it
    is a malformed gate file rather than a named probe, and the message would
    point at the wrong thing.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise probe_module.ProbeError(
            f"{case_id}: probe returned {value!r}; a probe must return a number in [0,1]"
        )
    if not 0.0 <= float(value) <= 1.0:
        raise probe_module.ProbeError(
            f"{case_id}: probe returned {value!r}, outside [0,1]"
        )
    return float(value)


# --------------------------------------------------------------------------
# Composing the gate
# --------------------------------------------------------------------------


def build_gate_input(measured: MeasuredSuite, *, as_of: date | None = None) -> hz.GateInput:
    """Compose {suite, committed baseline, fresh candidate} for the comparator.

    A case with no committed baseline is carried into the suite with an EMPTY
    baseline result rather than being dropped, so the harness reports
    `case_new_without_baseline` and fails. Dropping it would make "add a case
    and forget to record it" a silent no-op, and a case nobody baselined is a
    case nobody is gating.
    """
    suite = measured.suite
    by_id = suite.by_id()

    baseline_models = _baseline_model_set(suite, measured)

    cases: list[hz.BenchmarkCase] = []
    baseline_results: list[hz.CaseResult] = []
    candidate_results: list[hz.CaseResult] = []

    for entry in sorted(measured.cases, key=lambda c: c.case_id):
        declaration = by_id[entry.case_id]
        recorded = declaration.baseline
        # The case's declared inputs_digest is the BASELINE's, when there is
        # one: it is the assertion under review ("these numbers came from these
        # inputs"). With no baseline there is nothing to assert against, so the
        # freshly measured digest is used and the harness fails the case for
        # having no baseline rather than for a digest mismatch -- one failure,
        # naming the real problem.
        cases.append(
            hz.BenchmarkCase(
                case_id=entry.case_id,
                category=declaration.category,
                inputs_digest=(
                    recorded.inputs_digest if recorded else entry.inputs_digest
                ),
                baseline_models=baseline_models,
                metric=hz.Metric(
                    name=probe_module.PROBES[declaration.probe_id].metric_name,
                    direction=hz.Direction(
                        probe_module.PROBES[declaration.probe_id].direction
                    ),
                ),
                expected=declaration.expected,
                description=declaration.description(),
            )
        )
        if recorded is not None:
            baseline_results.append(
                hz.CaseResult(
                    case_id=entry.case_id,
                    models=baseline_models,
                    inputs_digest=recorded.inputs_digest,
                    samples=list(recorded.samples),
                )
            )
        candidate_results.append(
            hz.CaseResult(
                case_id=entry.case_id,
                models=measured.models,
                inputs_digest=entry.inputs_digest,
                samples=list(entry.samples),
            )
        )

    return hz.GateInput(
        suite=hz.BenchmarkSuite(cases),
        baseline=tuple(baseline_results),
        candidate=tuple(candidate_results),
        policy=bench.load_policy(suite),
        waivers=suite.waivers,
        as_of=as_of or suite.as_of,
    )


def _baseline_model_set(
    suite: bench.SuiteDeclaration, measured: MeasuredSuite
) -> hz.ModelSet:
    """The model set the baseline was measured under.

    Reconstructed from `suite.baseline_sources` -- the probe source digests
    recorded alongside the numbers -- and NOT from the code on disk. Reading it
    off disk would make every run a no-op, which would disable the no-op drift
    check on exactly the runs where the code changed.

    A suite with no recorded baseline at all reuses the candidate's set. There
    are no baseline results in that case, so nothing is compared against it; the
    harness fails every case with `case_new_without_baseline`, which is the
    accurate complaint.
    """
    if not suite.baseline_sources:
        return measured.models
    missing = sorted(
        {case.probe_id for case in suite.cases} - set(suite.baseline_sources)
    )
    if missing:
        raise bench.BenchmarkDeclarationError(
            f"{suite.source}: baseline_sources has no entry for probe(s) "
            f"{', '.join(missing)}; a baseline that cannot say which code "
            "produced it is not a baseline"
        )
    return model_set_for(suite, sources=dict(suite.baseline_sources))


# --------------------------------------------------------------------------
# Running one suite
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SuiteOutcome:
    suite_id: str
    source: Path
    exit_code: int
    text: str
    outcome: hz.GateOutcome | None


def run_suite(
    suite: bench.SuiteDeclaration, context: probe_module.ProbeContext
) -> SuiteOutcome:
    """Measure, compare, and report one suite."""
    header = f"suite: {suite.suite_id} ({suite.source})"
    try:
        measured = measure_suite(suite, context)
    except probe_module.ProbeError as unmeasured:
        return SuiteOutcome(
            suite_id=suite.suite_id,
            source=suite.source,
            exit_code=hz.EXIT_REFUSED,
            text="\n".join(
                [
                    header,
                    "verdict: SKIP",
                    f"unmeasured: {unmeasured}",
                    (
                        "  nothing was measured. This is NOT a pass: supply the "
                        "input the probe named and run again."
                    ),
                ]
            ),
            outcome=None,
        )

    gate = build_gate_input(measured, as_of=suite.as_of)
    outcome = hz.run_gate(gate, source=str(suite.source))
    detail = "\n".join(
        [
            header,
            f"  {len(measured.cases)} case(s) measured against code on disk",
            hz.format_outcome(outcome),
        ]
    )
    return SuiteOutcome(
        suite_id=suite.suite_id,
        source=suite.source,
        exit_code=outcome.exit_code,
        text=detail,
        outcome=outcome,
    )


# --------------------------------------------------------------------------
# Recording a baseline
# --------------------------------------------------------------------------


def record_suite(
    suite: bench.SuiteDeclaration,
    context: probe_module.ProbeContext,
    *,
    measured_by: str,
) -> str:
    """Measure the suite and write the numbers back into its file.

    Deliberately a separate command from `run`. Moving a baseline is the one
    action that makes a red gate green without changing anything about the
    system, so it is an explicit act whose whole output is a diff a reviewer
    reads -- never a side effect of running the gate.
    """
    measured = measure_suite(suite, context)
    document = json.loads(suite.source.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    document["baseline_sources"] = {
        case.probe_id: probe_module.PROBES[case.probe_id].sources_digest()
        for case in suite.cases
    }
    by_case = {entry.case_id: entry for entry in measured.cases}
    for raw_case in document["cases"]:
        entry = by_case[raw_case["case_id"]]
        raw_case["baseline"] = {
            "samples": [round(value, 6) for value in entry.samples],
            "inputs_digest": entry.inputs_digest,
            "measured_at": now,
            "measured_by": measured_by,
        }
    suite.source.write_text(
        json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return f"recorded {len(measured.cases)} baseline(s) into {suite.source}"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


class _RunnerArgumentParser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error; here 2 means "nothing was measured"."""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(hz.EXIT_USAGE)


def _resolve_context(args: argparse.Namespace) -> probe_module.ProbeContext:
    """Turn --library / --weights into a ProbeContext, verifying as it goes.

    The library is resolved -- manifest read, every file re-hashed off the disk
    -- before any case runs. A stale library must refuse loudly at the top
    rather than produce numbers that look fine.
    """
    library = None
    if args.library is not None:
        declaration = lib.load_library_declaration(args.library_declaration)
        library = lib.resolve(declaration, args.library)
    weights = Path(args.weights).resolve() if args.weights else None
    return probe_module.ProbeContext(library=library, weights_root=weights)


def _load_suites(paths: Sequence[str]) -> list[bench.SuiteDeclaration]:
    return [bench.load_suite(Path(path)) for path in paths]


def main(argv: Sequence[str] | None = None) -> int:
    parser = _RunnerArgumentParser(
        prog="python3 -m memory_engine_eval.runner",
        description=(
            "Measure the committed benchmark suites against the code on disk and "
            "gate the result (CLAUDE.md hard rule 7)."
        ),
        epilog=(
            "exit codes: 0 pass; 1 a measured regression; 2 nothing was measured "
            "(a probe could not run, or the comparison was refused); 3 a suite "
            "could not be set up. 1, 2 and 3 all fail CI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("suites", nargs="+", metavar="SUITE")
        sp.add_argument(
            "--library",
            metavar="DIR",
            help="root of a benchmark library on this machine",
        )
        sp.add_argument(
            "--library-declaration",
            metavar="PATH",
            help="the *.library.json the --library directory must match",
        )
        sp.add_argument(
            "--weights",
            metavar="DIR",
            help="models/weights directory holding the real ONNX checkpoints",
        )

    run_parser = sub.add_parser("run", help="measure and gate")
    add_common(run_parser)
    run_parser.add_argument(
        "--ci",
        action="store_true",
        help=(
            "only run suites declaring runs_in_ci; the rest are listed as "
            "EXCLUDED with what they need, never silently dropped"
        ),
    )
    run_parser.add_argument("--json", dest="json_path", metavar="PATH")

    record_parser = sub.add_parser(
        "record", help="measure and write the numbers back as the new baseline"
    )
    add_common(record_parser)
    record_parser.add_argument("--by", required=True, metavar="NAME")

    declare_parser = sub.add_parser(
        "declare-library",
        help="compute the file_count/inventory_digest/generator a *.library.json needs",
    )
    declare_parser.add_argument("root", metavar="DIR")

    args = parser.parse_args(argv)

    if args.command == "declare-library":
        # Emitted from a script rather than typed by a human on purpose: a
        # hand-copied digest is a check on somebody's clipboard.
        try:
            print(json.dumps(lib.describe_library(Path(args.root)), indent=2))
        except (OSError, lib.LibraryError, json.JSONDecodeError) as broken:
            print(f"cannot describe {args.root}: {broken}", file=sys.stderr)
            return hz.EXIT_USAGE
        return hz.EXIT_PASS

    if args.library is not None and args.library_declaration is None:
        print(
            "--library needs --library-declaration: a directory with no "
            "declaration to check it against is an unverified pile of files",
            file=sys.stderr,
        )
        return hz.EXIT_USAGE

    try:
        suites = _load_suites(args.suites)
        context = _resolve_context(args)
    except (bench.BenchmarkDeclarationError, lib.LibraryError) as broken:
        print(f"cannot set up: {broken}", file=sys.stderr)
        return hz.EXIT_USAGE

    if args.command == "record":
        try:
            for suite in suites:
                print(record_suite(suite, context, measured_by=args.by))
        except probe_module.ProbeError as unmeasured:
            print(f"nothing recorded: {unmeasured}", file=sys.stderr)
            return hz.EXIT_REFUSED
        return hz.EXIT_PASS

    codes: list[int] = []
    payload: list[dict[str, object]] = []
    for suite in suites:
        if args.ci and not suite.runs_in_ci:
            # Named, not skipped. A suite that CI does not cover has to be
            # visible in CI's own output, or it becomes a suite nobody runs.
            print(
                f"suite: {suite.suite_id} ({suite.source})\n"
                f"  EXCLUDED from CI: {suite.ci_exclusion_reason}",
                file=sys.stdout,
            )
            continue
        result = run_suite(suite, context)
        codes.append(result.exit_code)
        print(
            result.text,
            file=sys.stdout if result.exit_code == hz.EXIT_PASS else sys.stderr,
        )
        if result.outcome is not None:
            payload.append(result.outcome.to_dict())

    if not codes:
        print(
            "no suite ran. A gate that measured nothing must not report success",
            file=sys.stderr,
        )
        return hz.EXIT_REFUSED

    if getattr(args, "json_path", None):
        try:
            Path(args.json_path).write_text(
                json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as unwritable:
            print(f"could not write {args.json_path}: {unwritable}", file=sys.stderr)
            codes.append(hz.EXIT_USAGE)

    code = hz._worst(codes)
    print(
        f"benchmark result: exit {code} -- {hz._EXIT_NAMES[code]}",
        file=sys.stdout if code == hz.EXIT_PASS else sys.stderr,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
