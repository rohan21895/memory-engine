"""The defect with no symptom: nothing pinned which column is which class.

`docs/safety-classifier-decision.md` §6.6. `sensitive_logits` is shape [-1, 3].
Transpose two columns and every breastfeeding photograph classifies as
`explicit`: scores stay in [0, 1], the 0.3 threshold still fires, the manifest
still validates, and no test in the repository fails.

These are the tests that fail. There are four independent pins and each of them
catches a different edit, so each is exercised separately:

  1. the Python constant, which is what the other three are compared against;
  2. `models/schema/model-config.schema.json` (a hand-edited config) -- covered
     in models/tests/test_model_registry.py;
  3. `contracts/schemas/safety-clearance.schema.json` (a producer that read the
     config and then applied a different mapping) -- covered here and in the
     contract fixtures;
  4. the head's own probes (a matrix whose ROWS are in a different order from
     the order its artifact declares) -- covered here. That last one is the only
     check that looks at what the numbers DO rather than what a file says, and
     it is the only one that can catch an artifact that is internally consistent
     and wrong.
"""

from __future__ import annotations

import json
import unittest

from support import REPO_ROOT, synthetic_head  # noqa: E402

from memory_engine_safety.classes import (  # noqa: E402
    CLASS_ORDER,
    ClassOrderMismatch,
    check_class_order,
    scores_to_mapping,
)
from memory_engine_safety.textinit import (  # noqa: E402
    HeadBuild,
    load_prompt_bank,
    verify_class_axis,
)


class TestTheOrderItself(unittest.TestCase):
    def test_the_contract_and_the_code_agree(self):
        """Read from the schema file, not retyped."""
        schema = json.loads(
            (
                REPO_ROOT / "contracts" / "schemas" / "safety-clearance.schema.json"
            ).read_text(encoding="utf-8")
        )
        pinned = schema["$defs"]["ClassifierPin"]["properties"]["class_order"]["const"]
        self.assertEqual(list(CLASS_ORDER), pinned)

    def test_the_model_config_and_the_code_agree(self):
        config = json.loads(
            (REPO_ROOT / "models" / "configs" / "nsfw-siglip-head.json").read_text(
                encoding="utf-8"
            )
        )
        outputs = [o for o in config["outputs"] if o["meaning"] in {"logits", "scores"}]
        self.assertTrue(outputs, "the config declares no class axis")
        for output in outputs:
            self.assertEqual(list(CLASS_ORDER), output["class_order"])
            self.assertEqual(output["shape"][-1], len(CLASS_ORDER))

    def test_a_transposition_is_named_as_one(self):
        """The message matters: 'not equal' would not tell anyone what happened."""
        with self.assertRaises(ClassOrderMismatch) as caught:
            check_class_order(
                ["suggestive", "explicit", "medical_or_artistic"], where="test"
            )
        self.assertIn("TRANSPOSED", str(caught.exception))

    def test_a_set_is_not_an_order(self):
        for wrong in (
            ("explicit", "medical_or_artistic", "suggestive"),
            ("medical_or_artistic", "suggestive", "explicit"),
        ):
            with self.subTest(order=wrong):
                with self.assertRaises(ClassOrderMismatch):
                    check_class_order(wrong, where="test")

    def test_a_string_is_not_an_order(self):
        """`"explicit"` iterates into characters and would silently 'work'."""
        with self.assertRaises(ClassOrderMismatch):
            check_class_order("explicit", where="test")

    def test_a_short_vector_is_refused_rather_than_zipped(self):
        with self.assertRaises(ClassOrderMismatch):
            scores_to_mapping([0.1, 0.2], where="test")

    def test_scores_map_positionally_in_contract_order(self):
        self.assertEqual(
            {"explicit": 0.1, "suggestive": 0.2, "medical_or_artistic": 0.3},
            scores_to_mapping([0.1, 0.2, 0.3], where="test"),
        )


class TestTheHeadsOwnProbes(unittest.TestCase):
    """Pin 4: what the matrix DOES, not what the artifact says."""

    def _build(self, *, transposed: bool) -> HeadBuild:
        head = synthetic_head(transposed=transposed)
        # Probe i is class i's own direction, exactly as textinit builds them.
        probes = tuple(
            tuple(1.0 if column == index else 0.0 for column in range(1152))
            for index in range(len(CLASS_ORDER))
        )
        return HeadBuild(head=head, probes=probes)

    def test_a_correct_head_passes_its_own_probes(self):
        verify_class_axis(self._build(transposed=False))

    def test_a_transposed_head_is_caught_with_no_images_at_all(self):
        """THE TEST THE DEFECT WOULD HAVE FAILED.

        The artifact still declares the contract's class_order. Every schema in
        the repository still accepts it. Only running it over its own probes
        shows that row 0 is not `explicit`.
        """
        build = self._build(transposed=True)
        self.assertEqual(list(CLASS_ORDER), list(build.head.class_order))
        with self.assertRaises(ClassOrderMismatch) as caught:
            verify_class_axis(build)
        self.assertIn("has no symptom", str(caught.exception))


class TestThePromptBank(unittest.TestCase):
    """The bank is the closest thing this classifier has to training data."""

    def test_it_loads_and_covers_exactly_the_classes_plus_the_reference(self):
        bank = load_prompt_bank()
        self.assertEqual(
            sorted([*CLASS_ORDER, "benign"]), sorted(bank.prompts.keys())
        )
        for name, prompts in bank.prompts.items():
            with self.subTest(cls=name):
                self.assertGreaterEqual(
                    len(prompts), 15, f"{name} has too few prompts to average"
                )
                self.assertEqual(
                    len(set(prompts)),
                    len(prompts),
                    "a duplicate reweights the class mean without anybody deciding to",
                )

    def test_the_bank_is_keyed_by_name_so_it_cannot_be_transposed(self):
        """A JSON object has no column order. That is the point of the shape."""
        raw = json.loads(
            (
                REPO_ROOT
                / "packages"
                / "safety-gate"
                / "memory_engine_safety"
                / "prompts.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIsInstance(raw["classes"], dict)
        for name in CLASS_ORDER:
            self.assertIn(name, raw["classes"])

    def test_the_digest_is_over_the_committed_bytes(self):
        """So a head artifact identifies the exact text that produced it."""
        from memory_engine_safety.canonical import blake3_hex

        path = (
            REPO_ROOT
            / "packages"
            / "safety-gate"
            / "memory_engine_safety"
            / "prompts.json"
        )
        self.assertEqual(blake3_hex(path.read_bytes()), load_prompt_bank().digest)


if __name__ == "__main__":
    unittest.main()
