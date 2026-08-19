#!/usr/bin/env python3
"""Build the zero-shot expression/type head from SigLIP 2's text tower.

Selection criteria v2: the album selector needs to know what no classical
measure can tell it -- are the eyes open, is anyone smiling, is this a
peacefully sleeping baby or an adult caught mid-blink, is the frame a
composed photograph or an awkward mid-movement candid. The library already
carries a SigLIP 2 image embedding for every photo, so these become dot
products against text anchors built ONCE here, exactly like the safety head
(scripts/models/build_safety_head.py): the text tower is loaded at build
time, embeds the committed prompt bank below, and never ships.

Each axis stores a POSITIVE and a NEGATIVE direction (L2-normalised mean of
its prompts). The selector scores cos(image, positive) - cos(image, negative)
and then RANKS the result within the library rather than thresholding it:
the modality gap keeps absolute similarities meaningless (the safety-head
calibration lesson), but the contrast ORDERING over one library's photos is
exactly the signal selection needs -- which of these frames smiles hardest,
which eyes are shut.

Usage:
    python scripts/models/build_expression_head.py \
        --model-dir <HF snapshot dir or model id> \
        --out-dir models/weights/expression-head
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

TEXT_TOWER = "google/siglip2-so400m-patch14-384"
#: Same revision models/configs/siglip2-so400m-384.json pins for the vision export.
REVISION = "e8e487298228002f3d8a82e0cd5c8ea9c567f57f"
SPACE = "siglip2_so400m_1152"

#: The committed prompt bank. Changing ANY string changes the head, so the
#: bank's digest is recorded in the artifact's provenance.
AXES: dict[str, dict[str, list[str]]] = {
    # Smiling beats crying on an album page (owner's rule). Not a rejection
    # axis: a crying baby is still a memory, it just ranks below the laugh.
    "smile": {
        "positive": [
            "a photo of a smiling laughing person",
            "a happy baby giggling with a big smile",
            "people grinning joyfully at a celebration",
        ],
        "negative": [
            "a crying baby with a scrunched up face",
            "an upset person frowning",
            "a baby wailing in distress",
        ],
    },
    # Open, engaged eyes versus shut ones. Combined with `sleeping` below to
    # tell an adult mid-blink (reject) from a sleeping newborn (a cherished
    # photo TYPE that is merely capped, never rejected).
    "awake": {
        "positive": [
            "a portrait with bright open eyes looking at the camera",
            "an alert baby with wide open curious eyes",
            "a person making eye contact with the camera",
        ],
        "negative": [
            "a person with their eyes closed",
            "someone caught mid-blink with eyes shut",
            "a face with closed eyes",
        ],
    },
    "sleeping": {
        "positive": [
            "a baby peacefully sleeping",
            "a sleeping newborn swaddled in a blanket",
            "a serene sleeping child at rest",
        ],
        "negative": [
            "an awake alert person with open eyes",
            "people standing and talking",
            "an active playing child",
        ],
    },
    # Aesthetic appeal: the axis that separates "the best photo of this
    # moment" from "a photo of this moment". Deliberately about light,
    # composition and clutter -- properties a phone burst varies frame to
    # frame -- not about the subject, which selection's other axes own.
    "aesthetic": {
        "positive": [
            "a stunning professional photograph with beautiful soft light",
            "a beautifully composed portrait, clean background, gorgeous natural light",
            "a magazine quality family photo with rich pleasing colors",
        ],
        "negative": [
            "a cluttered messy snapshot with harsh flash and distracting background",
            "a dim murky phone photo with washed out colors",
            "a badly framed picture with clutter and poor lighting",
        ],
    },
    # A composed photograph versus a frame nobody would print: limbs
    # mid-gesture, subject adjusting hair, motion-smeared candid.
    "composed": {
        "positive": [
            "a well composed flattering portrait, the subject posed and still",
            "a clean posed family photograph",
            "a calm deliberate photo of a person at rest",
        ],
        "negative": [
            "an awkward candid photo caught mid-movement",
            "a person adjusting their hair caught off guard",
            "motion blurred arms mid gesture in a snapshot",
        ],
    },
}


def _bank_digest() -> str:
    canonical = json.dumps(AXES, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class HfTextEncoder:
    def __init__(self, model_dir: str) -> None:
        import torch  # noqa: PLC0415
        from transformers import AutoModel, AutoProcessor  # noqa: PLC0415

        revision = REVISION if model_dir == TEXT_TOWER else None
        self._torch = torch
        self._model = AutoModel.from_pretrained(model_dir, revision=revision)
        self._model.eval()
        self._processor = AutoProcessor.from_pretrained(model_dir, revision=revision)

    def direction(self, prompts: list[str]) -> list[float]:
        """L2-normalised mean of the prompts' L2-normalised embeddings."""
        torch = self._torch
        with torch.no_grad():
            tokens = self._processor(
                text=prompts, padding="max_length", return_tensors="pt"
            )
            output = self._model.get_text_features(**tokens)
            features = output.pooler_output if hasattr(output, "pooler_output") else output
            features = features / features.norm(dim=-1, keepdim=True)
            mean = features.mean(dim=0)
            mean = mean / mean.norm()
        return [float(value) for value in mean]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", default=TEXT_TOWER,
                        help="local snapshot dir or HF model id")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "models/weights/expression-head"))
    args = parser.parse_args()

    encoder = HfTextEncoder(args.model_dir)
    axes = [
        {
            "name": name,
            "positive": encoder.direction(bank["positive"]),
            "negative": encoder.direction(bank["negative"]),
        }
        for name, bank in AXES.items()
    ]

    artifact = {
        "artifact_version": 1,
        "space": SPACE,
        "axes": axes,
        "provenance": {
            "method": "text_tower_zero_shot",
            "text_tower": TEXT_TOWER,
            "prompt_bank_digest": _bank_digest(),
            "note": (
                "Selection ranking signal only, never a safety decision. Built by "
                f"scripts/models/build_expression_head.py from {TEXT_TOWER}@{REVISION}. "
                "Scores are contrast differences ranked WITHIN a library; absolute "
                "values are not calibrated and must not be thresholded."
            ),
        },
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "expression-siglip-head.v1.json"
    out_path.write_text(json.dumps(artifact, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({len(axes)} axes, bank {_bank_digest()[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
