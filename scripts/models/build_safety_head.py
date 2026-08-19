#!/usr/bin/env python3
"""Build the zero-shot safety head from SigLIP 2's text tower (issue #79).

Build-time only. The text tower is loaded here, used to embed the committed
prompt bank, and never ships: the artifacts this writes are a 3x1152 linear
head (~13.5 KiB of JSON) and a six-parameter calibration. Both carry
provenance and a DEVELOPMENT-ONLY note; neither is a shipping calibration
(no calibration corpus exists yet — see calibration.py MINIMUM_EXAMPLES_PER_CLASS
and docs/safety-classifier-decision.md §6.3).

Calibration derivation, recorded here because it IS the provenance:
the six Platt parameters are anchored on text-space geometry alone.
For each class c, with d_c the class's L2-normalised mean prompt direction,
r the benign reference direction and w_c the head row (normalised d_c - r):

    logit_pos_c = w_c · d_c      (an image perfectly aligned with the class)
    logit 0                      (no contrast evidence either way)

scale_c and bias_c are the unique pair mapping logit_pos_c -> p=0.95 and
logit 0 -> p=0.05. The low anchor is at ZERO, not at the benign text
direction: image embeddings live far from every text direction (the modality
gap), so a real photograph's contrast logit is near zero regardless of
content, and a calibration centred between the two text anchors maps
"no evidence" to p~=0.5 — which fires every class on every photograph.
Anchoring zero evidence at 0.05 makes a class fire only when its similarity
decisively beats benign. Deterministic, zero third-party images, and honest
about what it is: a geometric prior, not a measurement on photographs.

Usage:
    python scripts/models/build_safety_head.py \
        --model-dir <HF snapshot dir or model id> \
        --out-dir models/weights/safety-head
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "packages" / "safety-gate"))

from memory_engine_safety import textinit  # noqa: E402
from memory_engine_safety.classes import CLASS_ORDER  # noqa: E402
from memory_engine_safety.embedding import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    EMBEDDING_SPACE,
)

TEXT_TOWER = "google/siglip2-so400m-patch14-384"
#: Same revision models/configs/siglip2-so400m-384.json pins for the vision export.
REVISION = "e8e487298228002f3d8a82e0cd5c8ea9c567f57f"

_ANCHOR_HIGH = math.log(0.95 / 0.05)  # logit of the 0.95 anchor
_ANCHOR_LOW = -_ANCHOR_HIGH


class HfTextEncoder:
    """`TextEncoder` over the checkpoint's text tower, L2-normalised outputs."""

    space = EMBEDDING_SPACE
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, model_dir: str) -> None:
        import torch
        from transformers import AutoModel, AutoProcessor

        self._torch = torch
        self._model = AutoModel.from_pretrained(model_dir, revision=None)
        self._model.eval()
        self._processor = AutoProcessor.from_pretrained(model_dir)

    def encode(self, prompts):
        torch = self._torch
        inputs = self._processor(
            text=list(prompts),
            padding="max_length",
            max_length=64,
            truncation=True,
            return_tensors="pt",
        )
        with torch.no_grad():
            features = self._model.get_text_features(**inputs)
            if not torch.is_tensor(features):  # transformers >= 5 wraps the tensor
                features = features.pooler_output
            features = features / features.norm(dim=-1, keepdim=True)
        return [[float(v) for v in row] for row in features.tolist()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default=TEXT_TOWER,
                        help="local snapshot dir or HF model id")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "models" / "weights" / "safety-head"))
    args = parser.parse_args()

    bank = textinit.load_prompt_bank()
    encoder = HfTextEncoder(args.model_dir)

    build = textinit.build_head(
        encoder,
        bank=bank,
        note=(
            "DEVELOPMENT ONLY. Built by scripts/models/build_safety_head.py from "
            f"{TEXT_TOWER}@{REVISION}. Not promoted: no license audit entry, no "
            "calibration corpus, rollout stays placeholder until #79 closes "
            "properly."
        ),
    )
    textinit.verify_class_axis(build)

    # Recompute the two anchor directions per class, exactly as build_head does.
    directions = {}
    for name in (*CLASS_ORDER, bank.reference_class):
        vectors = [tuple(map(float, row)) for row in encoder.encode(bank.prompts_for(name))]
        directions[name] = textinit._l2_normalise(
            textinit._mean(vectors, where=name), where=name
        )
    reference = directions[bank.reference_class]

    scales, biases, anchors = [], [], {}
    for name, row in zip(CLASS_ORDER, build.head.rows, strict=True):
        logit_pos = math.fsum(w * x for w, x in zip(row, directions[name], strict=True))
        logit_neg = math.fsum(w * x for w, x in zip(row, reference, strict=True))
        if not logit_pos > 0.0 > logit_neg:
            raise SystemExit(
                f"class {name!r}: anchor logits are not ordered "
                f"({logit_pos} vs {logit_neg}); the geometry is degenerate"
            )
        # Low anchor at logit 0 (no contrast evidence), not at logit_neg — see
        # the module docstring on the modality gap.
        scale = (_ANCHOR_HIGH - _ANCHOR_LOW) / logit_pos
        bias = _ANCHOR_LOW
        scales.append(scale)
        biases.append(bias)
        anchors[name] = {"logit_pos": logit_pos, "logit_neg": logit_neg}

    from memory_engine_safety.calibration import PlattScaling

    calibration = PlattScaling(
        class_order=CLASS_ORDER,
        scale=tuple(scales),
        bias=tuple(biases),
        support=tuple((0, 0) for _ in CLASS_ORDER),
        corpus_manifest=None,
        note=(
            "DEVELOPMENT ONLY — geometric anchor calibration, not a fit. "
            "sigmoid maps each class's own text direction to p=0.95 and zero "
            "contrast evidence to p=0.05 (the modality gap keeps real image "
            "logits near zero). support is (0, 0) per class because "
            "zero photographs were involved; a manifest built on this must "
            "carry load_mode=development. Anchor logits: "
            + json.dumps(anchors, sort_keys=True)
        ),
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    head_path = out_dir / "nsfw-siglip-head.v1.json"
    calibration_path = out_dir / "nsfw-siglip-head.v1.calibration.json"
    head_path.write_text(
        json.dumps(build.head.to_artifact(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    calibration_path.write_text(
        json.dumps(calibration.to_artifact(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"head: {head_path}")
    print(f"calibration: {calibration_path}")
    print(f"prompt bank digest: {bank.digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
