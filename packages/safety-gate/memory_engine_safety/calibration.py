"""Per-class Platt scaling: six numbers that make the threshold mean something.

WHY THIS EXISTS AT ALL

A zero-shot SigLIP head emits cosine-like scores, not probabilities. Their range
depends on the prompt bank, on how many prompts are in each class, and on the
temperature the encoder was trained at. `sigmoid(raw_score) >= 0.3` therefore
compares a number to a threshold chosen for a probability, which is not a small
error -- it is a threshold with no meaning producing verdicts that look
calibrated. The 0.3 is described in the model config as "the one number in this
file that is a policy decision"; a policy decision applied to an uncalibrated
score is not a policy, it is an accident.

So: one scale and one bias per class, six parameters total.

    probability_c = sigmoid(scale_c * logit_c + bias_c)

THE REASON THIS IS THE WHOLE PLAN AND NOT A FINISHING TOUCH

Six parameters need a few hundred labelled examples per class. A full 1152x3
logistic refit needs tens of thousands. `docs/safety-classifier-decision.md`
§6.2 is explicit that this is the entire argument for zero-shot initialisation:
it moves the data requirement from "a corpus we cannot legally or ethically
obtain" to "an evaluation set we can". The `explicit` class is the one we still
cannot fully source (§6.3) -- there is no permissively licensed, model-released
explicit photographic corpus available -- and the honest position recorded there
is to validate that class against boundary material and report measured recall
on photographic pornography as UNKNOWN, in the eval report, as a gap. Not as a
pass.

THE FIT

Two-parameter logistic regression by Newton's method, with Platt's own
prior-corrected targets:

    t+ = (N+ + 1) / (N+ + 2)        t- = 1 / (N- + 2)

Regressing on 1 and 0 with a few hundred points overfits the extremes and
produces probabilities pinned at 0 and 1 -- which for a gate means a
confidently wrong verdict rather than an uncertain one. The correction is from
Platt (1999) and Lin, Lin & Weng (2007); it is four lines and it is the
difference between a calibrated head and a head that always says yes or no.

DETERMINISM

`math.fsum` for every accumulation, a fixed iteration cap, a fixed convergence
tolerance, and no randomness anywhere. Fitting the same table twice gives
byte-identical parameters, which is what makes the fitted artifact hashable and
therefore pinnable in `models/configs/*.json`.

REFUSALS

* Fitting a class with no positives, or with no negatives, raises. The fit
  would "converge" to a constant, and a constant probability is a class that
  never fires or always fires -- the two silent failures a safety gate has.
* Fitting fewer than `MINIMUM_EXAMPLES_PER_CLASS` examples raises unless the
  caller explicitly says it is doing something other than producing a shipping
  calibration. A calibration fitted on twelve photographs is a number that looks
  like a measurement.
* `probabilities()` on a calibration whose class order is not the contract's
  raises. Same defect as everywhere else in this package: three numbers in the
  wrong order are still three numbers.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from memory_engine_safety.classes import CLASS_ORDER, check_class_order

__all__ = [
    "CALIBRATION_ARTIFACT_VERSION",
    "MINIMUM_EXAMPLES_PER_CLASS",
    "CalibrationError",
    "PlattScaling",
    "fit_platt",
    "fit_per_class",
    "load_calibration",
    "sigmoid",
]

CALIBRATION_ARTIFACT_VERSION = 1

#: Below this, a per-class fit is refused as a shipping calibration. Two
#: parameters over a few hundred points is comfortable; over a few dozen it is a
#: number that looks like a measurement. Not a law of statistics -- a floor,
#: chosen so that "we did calibrate it" cannot mean twelve photographs.
MINIMUM_EXAMPLES_PER_CLASS = 200

_MAX_ITERATIONS = 200
_TOLERANCE = 1e-10


class CalibrationError(ValueError):
    """A fit that would produce a plausible-looking meaningless number."""


def sigmoid(value: float) -> float:
    """Numerically stable logistic.

    The naive form overflows for value < -709 and returns exactly 0.0 or 1.0 at
    the extremes, which then breaks the log-likelihood with a log(0). Written
    branchwise so both tails stay finite; this is standard and is spelled out
    only because getting it wrong shows up as a fit that silently stops
    converging.
    """
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


@dataclass(frozen=True)
class PlattScaling:
    """Six numbers, in class order, and the evidence behind them."""

    class_order: tuple[str, ...]
    scale: tuple[float, ...]
    bias: tuple[float, ...]
    #: How many examples each class was fitted on, positives first. Carried so a
    #: reader can tell a calibration from a gesture without opening the corpus.
    support: tuple[tuple[int, int], ...]
    corpus_manifest: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        check_class_order(self.class_order, where="calibration artifact")
        for name, values in (("scale", self.scale), ("bias", self.bias)):
            if len(values) != len(self.class_order):
                raise CalibrationError(
                    f"{len(values)} {name} terms against "
                    f"{len(self.class_order)} classes"
                )
            for index, value in enumerate(values):
                if value != value or value in (float("inf"), float("-inf")):
                    raise CalibrationError(
                        f"{name}[{index}] is not finite; every comparison against "
                        "NaN is False, so it would take whichever branch reads as "
                        "'fine'"
                    )
        if len(self.support) != len(self.class_order):
            raise CalibrationError("support must carry one (positives, negatives) "
                                   "pair per class")

    def probabilities(self, logits: Sequence[float]) -> tuple[float, ...]:
        """Calibrated per-class probabilities, in `class_order`.

        Independent sigmoids, NOT a softmax. A breastfeeding photograph should
        legitimately score high on `medical_or_artistic` and non-trivially on
        `suggestive` at once; making them compete for probability mass rebuilds
        the one-flag failure with extra columns.
        """
        row = tuple(float(value) for value in logits)
        if len(row) != len(self.class_order):
            raise CalibrationError(
                f"{len(row)} logits against {len(self.class_order)} classes"
            )
        return tuple(
            sigmoid(scale * value + bias)
            for scale, value, bias in zip(self.scale, row, self.bias, strict=True)
        )

    def to_artifact(self) -> dict:
        return {
            "artifact_version": CALIBRATION_ARTIFACT_VERSION,
            "class_order": list(self.class_order),
            "scale": list(self.scale),
            "bias": list(self.bias),
            "support": [list(pair) for pair in self.support],
            "corpus_manifest": self.corpus_manifest,
            "note": self.note,
        }


def fit_platt(
    logits: Sequence[float],
    labels: Sequence[bool],
    *,
    where: str,
    minimum_examples: int = MINIMUM_EXAMPLES_PER_CLASS,
) -> tuple[float, float]:
    """One class's (scale, bias) by Newton's method on the log-likelihood.

    Returns the pair such that `sigmoid(scale * logit + bias)` is a calibrated
    probability of the label being true.
    """
    values = [float(value) for value in logits]
    truths = [bool(label) for label in labels]
    if len(values) != len(truths):
        raise CalibrationError(f"{where}: {len(values)} logits against {len(truths)} labels")
    for index, value in enumerate(values):
        if value != value or value in (float("inf"), float("-inf")):
            raise CalibrationError(f"{where}: logit {index} is not finite")

    positives = sum(1 for label in truths if label)
    negatives = len(truths) - positives
    if positives == 0 or negatives == 0:
        raise CalibrationError(
            f"{where}: {positives} positives and {negatives} negatives. A "
            "single-class fit converges to a constant probability, which is a "
            "class that either never fires or always fires -- the two silent "
            "failures a gate has."
        )
    if len(truths) < minimum_examples:
        raise CalibrationError(
            f"{where}: {len(truths)} examples, below the {minimum_examples} floor. "
            "Two parameters fitted on a few dozen points is a number that looks "
            "like a measurement. Pass minimum_examples explicitly if you are "
            "deliberately fitting something other than a shipping calibration."
        )

    # Platt's prior-corrected targets. Regressing on hard 0/1 with a few hundred
    # points pins the fitted probabilities at the extremes, so a wrong verdict
    # arrives at 0.999 rather than at 0.6.
    target_positive = (positives + 1.0) / (positives + 2.0)
    target_negative = 1.0 / (negatives + 2.0)
    targets = [target_positive if label else target_negative for label in truths]

    # Start where Platt starts: zero slope, and a bias at the log-odds of the
    # prior, so iteration begins from the best constant predictor.
    scale = 0.0
    bias = math.log((negatives + 1.0) / (positives + 1.0))

    for _ in range(_MAX_ITERATIONS):
        gradient_scale = 0.0
        gradient_bias = 0.0
        hessian_ss = 0.0
        hessian_sb = 0.0
        hessian_bb = 0.0
        gs_terms: list[float] = []
        gb_terms: list[float] = []
        hss_terms: list[float] = []
        hsb_terms: list[float] = []
        hbb_terms: list[float] = []
        for value, target in zip(values, targets, strict=True):
            probability = sigmoid(scale * value + bias)
            residual = probability - target
            weight = probability * (1.0 - probability)
            gs_terms.append(residual * value)
            gb_terms.append(residual)
            hss_terms.append(weight * value * value)
            hsb_terms.append(weight * value)
            hbb_terms.append(weight)
        gradient_scale = math.fsum(gs_terms)
        gradient_bias = math.fsum(gb_terms)
        # Ridge term. The logistic Hessian is positive SEMI-definite, so it can
        # be singular -- every logit identical, or every weight underflowed to
        # zero once the fit is confident. The ridge makes it positive definite
        # and keeps the Newton step finite.
        #
        # Note that a perfectly separable class does NOT reach the guard below:
        # Platt's prior-corrected targets are 1/(N+2) and (N+1)/(N+2) rather
        # than 0 and 1, and a finite target has a finite optimum. That is the
        # correction's whole purpose, and it is tested rather than assumed --
        # see test_a_perfectly_separable_class_does_not_produce_a_verdict_of_1_or_0.
        hessian_ss = math.fsum(hss_terms) + 1e-12
        hessian_sb = math.fsum(hsb_terms)
        hessian_bb = math.fsum(hbb_terms) + 1e-12

        determinant = hessian_ss * hessian_bb - hessian_sb * hessian_sb
        if determinant <= 0.0:
            # Defensive, and it stays: an unsolvable step is one of the few ways
            # this loop could return a number nobody could reproduce, and the
            # alternative to raising is dividing by zero.
            raise CalibrationError(
                f"{where}: the Newton step is unsolvable -- the Hessian is singular "
                "even with the ridge term, which means this logit carries no usable "
                "variation for the labels given. A fit here would be a constant "
                "probability: a class that either never fires or always does."
            )
        step_scale = -(hessian_bb * gradient_scale - hessian_sb * gradient_bias) / determinant
        step_bias = -(hessian_ss * gradient_bias - hessian_sb * gradient_scale) / determinant

        scale += step_scale
        bias += step_bias
        if abs(step_scale) < _TOLERANCE and abs(step_bias) < _TOLERANCE:
            return (scale, bias)

    raise CalibrationError(
        f"{where}: Newton's method did not converge in {_MAX_ITERATIONS} "
        "iterations. Returning the last iterate would be a calibration nobody "
        "can reproduce."
    )


def fit_per_class(
    logits_by_item: Sequence[Sequence[float]],
    labels_by_item: Sequence[Mapping[str, bool]],
    *,
    class_order: Sequence[str] = CLASS_ORDER,
    corpus_manifest: str | None = None,
    minimum_examples: int = MINIMUM_EXAMPLES_PER_CLASS,
    note: str = "",
) -> PlattScaling:
    """Fit all three classes from a table of (logits, labels) rows.

    `labels_by_item` is a mapping per item rather than a positional vector, on
    purpose. This is the one function whose whole job is to line up columns with
    class names, and taking labels positionally would mean the caller could
    transpose them here -- reintroducing the exact defect, one layer up, in the
    place where it would be baked into the fitted parameters and never seen
    again.
    """
    order = check_class_order(tuple(class_order), where="fit_per_class")
    if len(logits_by_item) != len(labels_by_item):
        raise CalibrationError(
            f"{len(logits_by_item)} logit rows against {len(labels_by_item)} label rows"
        )
    if not logits_by_item:
        raise CalibrationError("nothing to fit")

    scales: list[float] = []
    biases: list[float] = []
    support: list[tuple[int, int]] = []
    for index, name in enumerate(order):
        column: list[float] = []
        truths: list[bool] = []
        for row, labels in zip(logits_by_item, labels_by_item, strict=True):
            if len(row) != len(order):
                raise CalibrationError(
                    f"a logit row has {len(row)} columns against {len(order)} classes"
                )
            if name not in labels:
                raise CalibrationError(
                    f"an item carries no label for {name!r}; a missing label is not "
                    "a negative, and treating it as one is how a class quietly "
                    "learns that everything is fine"
                )
            column.append(float(row[index]))
            truths.append(bool(labels[name]))
        scale, bias = fit_platt(
            column, truths, where=f"class {name!r}", minimum_examples=minimum_examples
        )
        scales.append(scale)
        biases.append(bias)
        positives = sum(1 for label in truths if label)
        support.append((positives, len(truths) - positives))

    return PlattScaling(
        class_order=order,
        scale=tuple(scales),
        bias=tuple(biases),
        support=tuple(support),
        corpus_manifest=corpus_manifest,
        note=note,
    )


def load_calibration(source: Path | Mapping) -> PlattScaling:
    document = (
        json.loads(Path(source).read_text(encoding="utf-8"))
        if isinstance(source, (str, Path))
        else dict(source)
    )
    version = document.get("artifact_version")
    if version != CALIBRATION_ARTIFACT_VERSION:
        raise CalibrationError(
            f"calibration artifact version {version!r} is not "
            f"{CALIBRATION_ARTIFACT_VERSION}; denied rather than parsed"
        )
    return PlattScaling(
        class_order=tuple(document.get("class_order", ())),
        scale=tuple(float(v) for v in document.get("scale", ())),
        bias=tuple(float(v) for v in document.get("bias", ())),
        support=tuple(tuple(int(v) for v in pair) for pair in document.get("support", ())),
        corpus_manifest=document.get("corpus_manifest"),
        note=str(document.get("note", "")),
    )
