"""The class axis. One tuple, in one place, that everything else reads.

WHY A WHOLE MODULE FOR THREE STRINGS

Because they are three strings *in an order*, and the order was the defect.
`models/configs/nsfw-siglip-head.json` declared `sensitive_logits` with shape
`[-1, 3]` and nothing anywhere said which index was which class. Transpose two
columns and every breastfeeding photograph classifies as `explicit`: the scores
stay inside [0, 1], the 0.3 threshold still fires, the clearance manifest still
validates, and not one test in this repository fails. The only signal is a
family's photograph quietly missing from their album, months later, with no way
to trace it back.

That is the same shape as two defects this project has already found -- SCRFD's
output names (#36) and the `yunet_5` / `insightface_5` landmark schemes, where
"five points is five points" produces a plausible warp and a wrong embedding.
The pattern is an unlabelled tensor crossing a process boundary. The fix is
always the same: name the axis, pin the name, and make a disagreement fail.

WHERE THE ORDER IS PINNED, AND WHY IT IS PINNED FOUR TIMES

1. Here, as the value every producer and consumer in Python reads.
2. `models/schema/model-config.schema.json` -- a `safety_classifier` config whose
   logits output omits or reorders `class_order` is schema-invalid.
3. `contracts/schemas/safety-clearance.schema.json` -- `ClassifierPin.class_order`
   is a `const`, so a manifest that records a different mapping is rejected by
   validation rather than by review.
4. `models/tests/test_model_registry.py` -- `len(class_order) == shape[-1]`,
   which JSON Schema cannot express.

Four checks over one fact looks redundant until you notice that each of them
catches a different edit: (2) catches a hand-edited config, (3) catches a
producer that read the config and then applied a different mapping anyway, (4)
catches a head that grew a fourth column, and (1) is what makes (2) and (3)
readable as the same rule rather than as two files that happen to agree today.

WHY THESE THREE AND NOT ONE FLAG

Issue #21, restated because the reason is the design. Collapsing them produces
both classic failures at once: a breastfeeding photograph or a post-surgery
record treated as pornography, and a bikini holiday photograph treated as safe
to publish. A family library contains all three and the right handling differs
for each.

They are INDEPENDENT sigmoids, not a softmax. A breastfeeding photograph should
legitimately score high on `medical_or_artistic` and non-trivially on
`suggestive` at the same time; making them compete for probability mass rebuilds
the one-flag failure with extra columns.
"""

from __future__ import annotations

__all__ = [
    "CLASS_ORDER",
    "ClassOrderMismatch",
    "check_class_order",
    "scores_to_mapping",
]


#: The class axis, in tensor-column order. Column 0 of a `[-1, 3]` logits
#: tensor is `explicit`, column 1 is `suggestive`, column 2 is
#: `medical_or_artistic`. Read as a tuple everywhere; never rebuilt from a set,
#: never sorted, never derived from a dict's iteration order.
CLASS_ORDER: tuple[str, str, str] = ("explicit", "suggestive", "medical_or_artistic")


class ClassOrderMismatch(ValueError):
    """A declared class axis disagrees with the contract's.

    A `ValueError` rather than a returned False on purpose. Every consumer of a
    class order is about to index into a tensor with it, and a caller that
    forgets to branch on a boolean would index with the wrong one and get
    numbers rather than an error.
    """


def check_class_order(declared: object, *, where: str) -> tuple[str, ...]:
    """Return `declared` as a tuple, or raise naming what disagreed.

    `where` is required and goes in the message, because by the time this fires
    the interesting question is which file was edited -- a config, a manifest, a
    head artifact and a test fixture can all reach here.
    """
    if isinstance(declared, str) or not isinstance(declared, (list, tuple)):
        raise ClassOrderMismatch(
            f"{where}: class_order must be an ordered array of class names, "
            f"got {type(declared).__name__}"
        )
    order = tuple(declared)
    if order == CLASS_ORDER:
        return order

    if sorted(order) == sorted(CLASS_ORDER):
        # The dangerous case, called out separately: the same three names in a
        # different order is exactly the transposition that has no symptom.
        raise ClassOrderMismatch(
            f"{where}: class_order {list(order)} is the contract's three classes "
            f"TRANSPOSED (contract order is {list(CLASS_ORDER)}). Column "
            f"{order.index(CLASS_ORDER[0])} would be read as 'explicit'. This is "
            "the failure that has no symptom: every score stays in range and "
            "every threshold still fires."
        )
    raise ClassOrderMismatch(
        f"{where}: class_order {list(order)} is not the contract's "
        f"{list(CLASS_ORDER)}"
    )


def scores_to_mapping(values: object, *, where: str) -> dict[str, float]:
    """Turn a positional 3-vector into the contract's named `ClassScores`.

    THIS FUNCTION IS THE TRANSPOSITION. It is the one place in the codebase
    where a column index becomes a class name, so it is the one place the
    mistake can be made -- which is why it exists at all rather than being three
    lines inlined at each call site. Inlined, there would be three places to get
    it wrong and no single place to test.
    """
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ClassOrderMismatch(f"{where}: expected an ordered 3-vector of scores")
    row = tuple(values)
    if len(row) != len(CLASS_ORDER):
        raise ClassOrderMismatch(
            f"{where}: {len(row)} columns against {len(CLASS_ORDER)} class names "
            f"{list(CLASS_ORDER)}; a length mismatch reads the wrong column and "
            "is as silent as a transposition"
        )
    return {name: float(value) for name, value in zip(CLASS_ORDER, row, strict=True)}
