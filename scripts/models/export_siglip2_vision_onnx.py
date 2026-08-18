#!/usr/bin/env python3
"""Export the SigLIP 2 so400m/384 VISION TOWER to ONNX, and prove it runs.

WHY THIS EXISTS
---------------

Issue #79. `google/siglip2-so400m-patch14-384` publishes safetensors and
nothing else, so `models/configs/siglip2-so400m-384.json` named an ONNX file
that no URL served. `scripts/models/fetch_weights.py` reported the entry
UNAVAILABLE, analysis reported `siglip2-so400m-384 (weights_missing)`, and
album planning and render-print refused. Every album produced up to that point
came from the test suite's stand-in embedder: the chain from AlbumSpec to paper
was proven, the taste of what went on the page was not.

The two ways out were to adopt a third-party conversion or to perform our own.
This is our own, for one reason: **a conversion we perform is one we can pin
and audit.** The community export at `onnx-community/siglip2-so400m-patch14-384-ONNX`
exposes `last_hidden_state` / `pooler_output` where this repository's config
binds `image_embeds`, and this repository has twice shipped a config bound to an
output name that did not exist in the graph (#36 for SCRFD, ArcFace in #69).
Adopting somebody else's export decisions is how that happens a third time.

WHAT REPRODUCIBILITY MEANS HERE, AND WHAT IT DOES NOT
-----------------------------------------------------

Byte-identical re-export is NOT claimed and is not achievable: the graph is
emitted by whatever PyTorch is installed, and PyTorch 2.9.1 and 2.13.0 produce
files that differ in size. What is reproducible is the *input* and the
*procedure*:

  * the input is one file at one immutable revision, and its SHA-256 is checked
    before anything is built from it -- a repository whose maintainer replaces
    the weights fails here rather than silently producing a different model;
  * the procedure is this script;
  * the resulting artifact is verified against the config's own declarations by
    `--verify`, which needs only onnxruntime, numpy and Pillow, so a reviewer
    can re-run the check without PyTorch and without re-exporting.

Record the toolchain and the measured digest in `docs/model-registry.md`. The
digest is PRINTED, never written: see "no pin is written" below.

VISION TOWER ONLY
-----------------

The text tower is not used at inference today. Search is text-conditioned via
the prompt engine and a frontier model, not via SigLIP's text encoder, and
nothing in ranking, dedupe refinement, diversity or the planned safety head
reads text embeddings. Exporting both would roughly double the artifact for a
graph the product never calls.

The one honest caveat: the registry entry lists `zero_shot_tags` in
`required_for`, and zero-shot tagging with SigLIP *does* need text embeddings.
Nothing implements it yet. When it lands, the text tower is a SECOND artifact
and a second registry entry, with its own digest and its own licence line --
not a wider version of this one. Two graphs that can be pinned separately are
better than one that can only be pinned together.

`image_embeds` is upstream's own name for this tensor: `SiglipModel.forward`
computes `image_embeds = vision_outputs.pooler_output` and then L2-normalises
it. This graph stops *before* the normalisation, because the config's
`postprocessing.steps` is `["l2_normalize"]` and the host applies it. Moving it
into the graph would normalise twice or not at all depending on which side
someone edited last.

PRECISION
---------

The config declares `quantization: fp16`, and this export honours that: every
initializer in the graph is float16 (428M parameters, ~857MB, against ~1.71GB
for the same tower in fp32).

That sentence used to be a measurement somebody took once and wrote down.
`check_precision()` now reads the stored dtype of every FLOAT-KIND initializer
off the graph and compares it against `weights.quantization`, so a declaration
and an artifact that disagree fail instead of being believed -- an export whose
dtype was changed, or a future transformers that leaves a block in float32, is
caught by the same check that caught nothing before. Integer and boolean
initializers are graph plumbing rather than weights and are counted and
reported, not failed: this graph carries none today, and a rule that demanded
they be float16 too would fail the next legitimate re-export for a reason with
nothing to do with precision. It needs the `onnx` package,
which is always present when `torch.onnx.export` runs and is often absent on a
machine that only verifies; on export it is REQUIRED, and on `--verify` a
missing `onnx` makes the run exit 3 (incomplete) rather than report a pass over
a check that never ran.

The graph's INPUT AND OUTPUT are float32 by deliberate choice. `weights.
quantization` describes the stored weights, and the boundary is a separate
question: the host's preprocessing produces float32 and the embedding store
holds float32, so an fp16 boundary would add a lossy cast on each side for no
benefit. `workers/ml-runtime` reads the dtype from the session and would feed
either, so this is a choice about clarity, not compatibility.

The cast is expressed in the exported module (`pixel_values.to(float16)` in,
`.to(float32)` out) rather than by post-hoc conversion of an fp32 graph.
`onnxconverter_common.float16.convert_float_to_float16(keep_io_types=True)` was
tried first and produced a graph ONNX Runtime refuses to load -- it leaves the
patch-embedding Conv with a float32 input and float16 weights:

    Type Error: Type parameter (T) of Optype (Conv) bound to different types
    (tensor(float) and tensor(float16)) in node (/vm/embeddings/patch_embedding/Conv)

which is worth recording because that failure is loud, and the same tool's
quiet failures are the ones that would have hurt.

NO PIN IS WRITTEN
-----------------

Like `fetch_weights.py`, this script measures the BLAKE3 of what it produced
and prints it for a human to paste. A pin the producer writes certifies only
that it hashed the bytes it just wrote, which is true of any bytes. A pin means
a person decided this artifact is the one the product runs.

USAGE
-----

    # export (needs torch + transformers + onnx; see docs/model-registry.md)
    python3 scripts/models/export_siglip2_vision_onnx.py

    # verify an artifact that is already installed (onnxruntime + numpy + PIL;
    # add onnx to check the stored precision too, or the run exits 3)
    python3 scripts/models/export_siglip2_vision_onnx.py --verify

    # export, then additionally compare against the PyTorch reference
    python3 scripts/models/export_siglip2_vision_onnx.py --parity

EXIT CODES

    0  the artifact exists and passed every check
    3  every check that ran passed, and one was asked for that could not run
       (`--parity` alongside `--verify`, which has no source snapshot; or the
       precision check on a machine without the `onnx` package)
    4  a check FAILED -- the artifact is wrong, and is not installed
    5  the script could not run at all: a dependency is missing, the config is
       unreadable, or the destination already exists and --force was not given
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_ROOT = REPO_ROOT / "models"
CONFIG_PATH = MODELS_ROOT / "configs" / "siglip2-so400m-384.json"
DEFAULT_WEIGHTS_DIR = MODELS_ROOT / "weights"

MODEL_ID = "siglip2-so400m-384"

# The input, pinned. A branch name or `main` would let the bytes this artifact
# is built from change underneath the digest that describes it.
HF_REPO = "google/siglip2-so400m-patch14-384"
HF_REVISION = "e8e487298228002f3d8a82e0cd5c8ea9c567f57f"
HF_WEIGHTS_FILE = "model.safetensors"
HF_WEIGHTS_SHA256 = "9f4f4a49f908ef0c979bce8ff5a5c0e88882dc6c5dc4304387cbbd152558e2c2"
HF_WEIGHTS_BYTES = 4_544_143_072

OPSET = 17

# The only real photograph committed to this repository. The demo library is
# synthetic by design -- its "faces" are ovals with arcs for mouths -- and a
# synthetic image cannot tell you whether an embedder is loaded correctly,
# because a randomly initialised network also returns different numbers for
# different inputs. See `--parity` and the zero-shot check in
# docs/model-registry.md for what actually establishes that.
DEFAULT_IMAGE = REPO_ROOT / "apps" / "desktop" / "src" / "assets" / "onboarding-memory-table.jpg"

EXIT_OK = 0
EXIT_INCOMPLETE = 3
EXIT_FAILED = 4
EXIT_PRECONDITION = 5


class Precondition(RuntimeError):
    """The script cannot run."""


class CheckFailed(RuntimeError):
    """The artifact disagrees with the config, or with arithmetic."""


@dataclass(frozen=True)
class Declared:
    """What models/configs/siglip2-so400m-384.json says the artifact must be.

    Read from the config rather than restated here on purpose. A script that
    carried its own copy of the output name would agree with itself while
    disagreeing with the config, which is the shape of #36 and #69.
    """

    filename: str
    input_name: str
    width: int
    height: int
    output_names: tuple[str, ...]
    dimensions: int
    quantization: str

    @classmethod
    def read(cls, path: Path = CONFIG_PATH) -> "Declared":
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise Precondition(f"cannot read {path}: {error}") from error
        weights = config["weights"]
        pre = config["preprocessing"]
        outputs = config["outputs"]
        if len(outputs) != 1:
            raise Precondition(
                f"{path} declares {len(outputs)} outputs; this exporter builds a "
                "single-output graph and will not guess which one is the embedding"
            )
        size = pre["input_size"]
        return cls(
            filename=str(weights["filename"]),
            input_name=str(pre["input_name"]),
            width=int(size["width"]),
            height=int(size["height"]),
            output_names=(str(outputs[0]["name"]),),
            dimensions=int(outputs[0]["dimensions"]),
            quantization=str(weights.get("quantization", "fp32")),
        )


# --------------------------------------------------------------------------
# digests
# --------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def blake3_file(path: Path) -> str:
    try:
        from blake3 import blake3  # type: ignore
    except ImportError as error:  # pragma: no cover - environment dependent
        raise Precondition(
            "the blake3 package is required to report a digest: pip install blake3"
        ) from error
    hasher = blake3()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# --------------------------------------------------------------------------
# the export
# --------------------------------------------------------------------------


def fetch_source(cache_dir: Path | None, log) -> Path:
    """The pinned safetensors, downloaded if needed, SHA-256 checked always."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:
        raise Precondition(
            "huggingface_hub is required to obtain the source weights: "
            "pip install huggingface_hub"
        ) from error

    if cache_dir is not None:
        os.environ.setdefault("HF_HOME", str(cache_dir))
    log(f"source: {HF_REPO} at {HF_REVISION}")
    snapshot = Path(
        snapshot_download(
            HF_REPO,
            revision=HF_REVISION,
            allow_patterns=["config.json", "preprocessor_config.json", HF_WEIGHTS_FILE],
        )
    )
    weights = snapshot / HF_WEIGHTS_FILE
    if not weights.is_file():
        raise Precondition(f"{HF_WEIGHTS_FILE} is not in the downloaded snapshot")

    size = weights.stat().st_size
    if size != HF_WEIGHTS_BYTES:
        raise CheckFailed(
            f"{HF_WEIGHTS_FILE} is {size} bytes, pinned at {HF_WEIGHTS_BYTES}"
        )
    digest = sha256_file(weights)
    if digest != HF_WEIGHTS_SHA256:
        raise CheckFailed(
            f"{HF_WEIGHTS_FILE} SHA-256 is {digest}, pinned at {HF_WEIGHTS_SHA256}. "
            "The source has changed; nothing is exported from bytes this script "
            "cannot identify."
        )
    log(f"source verified: sha256 {digest} ({size} bytes)")
    return snapshot


def export(snapshot: Path, declared: Declared, destination: Path, log) -> None:
    try:
        import torch
        from transformers import SiglipVisionModel
    except ImportError as error:
        raise Precondition(
            "torch and transformers are required to export: "
            "pip install torch transformers onnx"
        ) from error

    if declared.quantization != "fp16":
        raise Precondition(
            f"the config declares quantization {declared.quantization!r}; this "
            "exporter produces fp16 weights. Change one of them deliberately -- "
            "a declaration and an artifact that disagree is the defect."
        )

    log("loading the vision tower (the text tower is not exported)")
    vision = SiglipVisionModel.from_pretrained(
        snapshot, dtype=torch.float16, attn_implementation="eager"
    ).eval()

    class VisionTower(torch.nn.Module):
        """fp16 weights, float32 boundary. See the module docstring."""

        def __init__(self, model) -> None:
            super().__init__()
            self.model = model

        def forward(self, pixel_values):
            pooled = self.model(
                pixel_values=pixel_values.to(torch.float16)
            ).pooler_output
            return pooled.to(torch.float32)

    module = VisionTower(vision)
    example = torch.zeros(1, 3, declared.height, declared.width, dtype=torch.float32)
    with torch.no_grad():
        traced = module(example)
    if tuple(traced.shape) != (1, declared.dimensions):
        raise CheckFailed(
            f"the PyTorch module returns {tuple(traced.shape)}; the config "
            f"declares {declared.dimensions} dimensions"
        )

    log(f"exporting to ONNX opset {OPSET} (a few minutes)")
    torch.onnx.export(
        module,
        (example,),
        str(destination),
        input_names=[declared.input_name],
        output_names=list(declared.output_names),
        dynamic_axes={
            declared.input_name: {0: "batch"},
            declared.output_names[0]: {0: "batch"},
        },
        opset_version=OPSET,
        do_constant_folding=True,
        # The TorchScript exporter, explicitly. It is deprecated in favour of
        # the torch.export path, and it is chosen anyway because it takes
        # `output_names` literally: the name the config binds is the name in
        # the graph, not a name the tracer derived. A future PyTorch that
        # removes it will fail here loudly, which is the correct outcome --
        # the alternative is a graph whose output is called something else and
        # a host that cannot find the embedding.
        dynamo=False,
    )
    log(f"exported: {destination.stat().st_size} bytes")


# --------------------------------------------------------------------------
# verification -- no PyTorch, so a reviewer can re-run it
# --------------------------------------------------------------------------


def preprocess(image_path: Path, declared: Declared, config: dict):
    """The config's declared preprocessing, applied with Pillow.

    This deliberately does not import `workers/ml-runtime`: its preprocessing
    goes through OpenCV, and using the host's code here would make the check
    agree with the host rather than with the config.
    """
    import numpy as np
    from PIL import Image

    pre = config["preprocessing"]
    resamples = {
        "nearest": Image.Resampling.NEAREST,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "lanczos": Image.Resampling.LANCZOS,
    }
    name = str(pre["interpolation"])
    if name not in resamples:
        raise Precondition(f"no Pillow resampling filter for interpolation {name!r}")
    if pre["resize"] != "stretch":
        raise Precondition(
            f"this check implements resize 'stretch'; the config says {pre['resize']!r}"
        )
    if pre["color_order"] != "rgb" or pre["layout"] != "nchw":
        raise Precondition(
            f"this check implements rgb/nchw; the config says "
            f"{pre['color_order']}/{pre['layout']}"
        )

    with Image.open(image_path) as handle:
        image = handle.convert("RGB").resize(
            (declared.width, declared.height), resamples[name]
        )
    array = np.asarray(image, dtype=np.float32) * float(pre["scale"])
    array = (array - np.asarray(pre["mean"], dtype=np.float32)) / np.asarray(
        pre["std"], dtype=np.float32
    )
    return np.ascontiguousarray(array.transpose(2, 0, 1)[None])


# `weights.quantization` describes the dtype the WEIGHTS are stored in, which
# is what an initializer carries. The graph's input and output dtype is a
# separate decision (float32, deliberately -- see the module docstring), so
# this maps only the float precisions an export of this model can produce.
QUANTIZATION_DTYPE = {"fp32": "FLOAT", "fp16": "FLOAT16"}

# Which initializers are WEIGHTS. An ONNX graph also carries integer and
# boolean initializers -- `Reshape` shapes, `Slice` bounds, `Gather` indices --
# and those are plumbing, not parameters: they have no precision to declare and
# `quantization: fp16` says nothing about them. This export happens to fold all
# of its shape constants into `Constant` nodes and carry none, so a rule of
# "every initializer must be float16" passes today by luck and would fail the
# next legitimate re-export for a reason that has nothing to do with precision.
# The rule is therefore: every FLOAT-KIND initializer must be the declared
# dtype. That still catches the failure this check exists for -- a tower
# silently exported in fp32 -- because those weights arrive as FLOAT.
FLOAT_DTYPES = frozenset(
    {
        "FLOAT",
        "FLOAT16",
        "DOUBLE",
        "BFLOAT16",
        "FLOAT8E4M3FN",
        "FLOAT8E4M3FNUZ",
        "FLOAT8E5M2",
        "FLOAT8E5M2FNUZ",
    }
)


def check_precision(path: Path, declared: Declared, log, *, required: bool) -> bool:
    """Does the graph store its weights in the precision the config declares?

    Returns True when the check ran. The caller decides what a check that
    could not run means; nothing here reports a pass it did not measure.

    Reads every initializer's `data_type` off the protobuf. `onnxruntime` will
    not answer this -- it reports the dtype of inputs and outputs, which are
    float32 here by design, so a graph whose weights were silently exported in
    fp32 looks identical through the session API and is twice the size on disk
    with nobody checking the number.
    """
    expected = QUANTIZATION_DTYPE.get(declared.quantization)
    if expected is None:
        raise Precondition(
            f"the config declares quantization {declared.quantization!r}; this "
            f"script knows how to check {sorted(QUANTIZATION_DTYPE)}. Extend the "
            "map deliberately rather than skipping the check."
        )
    try:
        import onnx
        from onnx import TensorProto
    except ImportError as error:
        if required:
            raise Precondition(
                "the onnx package is required to check that the artifact's "
                "stored precision matches the config: pip install onnx"
            ) from error
        log(
            "precision: NOT CHECKED -- the onnx package is not installed, so "
            f"the config's quantization {declared.quantization!r} was taken on "
            "trust. pip install onnx to check it."
        )
        return False

    # load_external_data=False: this artifact keeps its tensors inline, and a
    # future one that did not would otherwise be read twice over.
    model = onnx.load(str(path), load_external_data=False)
    counts: dict[str, int] = {}
    other: dict[str, int] = {}
    elements = 0
    for initializer in model.graph.initializer:
        name = TensorProto.DataType.Name(initializer.data_type)
        if name not in FLOAT_DTYPES:
            other[name] = other.get(name, 0) + 1
            continue
        counts[name] = counts.get(name, 0) + 1
        if name == expected:
            size = 1
            for dimension in initializer.dims:
                size *= dimension
            elements += size
    if not counts:
        raise CheckFailed(
            f"{path.name} carries no floating-point initializers at all "
            f"(non-float initializers: {other or 'none'}) -- that is not a model "
            "with weights in it"
        )
    wrong = {name: count for name, count in counts.items() if name != expected}
    if wrong:
        raise CheckFailed(
            f"the config declares quantization {declared.quantization!r} "
            f"({expected}), and the graph stores {wrong} alongside "
            f"{counts.get(expected, 0)} {expected} initializers. Match the "
            "artifact to the declaration or change the declaration; do not "
            "leave them disagreeing."
        )
    log(
        f"precision: {counts[expected]} weight initializers, all {expected}, "
        f"{elements:,} parameters -- agrees with quantization "
        f"{declared.quantization!r}"
        + (f"; {sum(other.values())} non-float initializers {other}" if other else "")
    )
    return True


def cosine(a, b) -> float:
    import numpy as np

    left = np.asarray(a, dtype=np.float64).ravel()
    right = np.asarray(b, dtype=np.float64).ravel()
    return float(
        left @ right / (np.linalg.norm(left) * np.linalg.norm(right))
    )


def verify(path: Path, declared: Declared, config: dict, image_path: Path, log) -> None:
    """Run the real graph. Every claim below is measured, none is assumed."""
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError as error:
        raise Precondition(
            "onnxruntime and numpy are required to verify: "
            "pip install onnxruntime numpy pillow"
        ) from error

    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

    # --- the graph's signature, against the config's bindings ---------------
    inputs = session.get_inputs()
    if len(inputs) != 1:
        raise CheckFailed(f"the graph has {len(inputs)} inputs; the host feeds one")
    got = inputs[0]
    if got.name != declared.input_name:
        raise CheckFailed(
            f"the graph's input is {got.name!r}; the config binds "
            f"{declared.input_name!r}"
        )
    if len(got.shape) != 4 or got.shape[1] != 3:
        raise CheckFailed(f"input shape {got.shape} is not [batch, 3, h, w]")
    if (got.shape[2], got.shape[3]) != (declared.height, declared.width):
        raise CheckFailed(
            f"input is {got.shape[2]}x{got.shape[3]}; the config declares "
            f"{declared.height}x{declared.width}"
        )
    batch_axis_is_dynamic = not isinstance(got.shape[0], int)

    outputs = session.get_outputs()
    names = tuple(output.name for output in outputs)
    if names != declared.output_names:
        raise CheckFailed(
            f"the graph's outputs are {names}; the config binds "
            f"{declared.output_names}. THIS is the check that #36 and #69 needed."
        )
    embedding_shape = outputs[0].shape
    if len(embedding_shape) != 2 or embedding_shape[1] != declared.dimensions:
        raise CheckFailed(
            f"output shape {embedding_shape}; the config declares "
            f"[batch, {declared.dimensions}]"
        )
    log(f"graph in : {got.name} {got.type} {got.shape}")
    log(f"graph out: {names[0]} {outputs[0].type} {embedding_shape}")

    # --- a real photograph through it ---------------------------------------
    if not image_path.is_file():
        raise Precondition(f"no image to verify with: {image_path}")
    tensor = preprocess(image_path, declared, config)
    single = session.run(list(declared.output_names), {declared.input_name: tensor})[0]
    if tuple(single.shape) != (1, declared.dimensions):
        raise CheckFailed(f"one image in, {single.shape} out")
    if not np.isfinite(single).all():
        raise CheckFailed("the embedding contains NaN or inf")
    if float(single.std()) <= 1e-6:
        raise CheckFailed(
            f"the embedding is constant (std {float(single.std()):.3e}) -- a graph "
            "returning the same number for every dimension is not an embedder"
        )
    log(
        f"real image: {image_path.name} -> {single.shape} {single.dtype}, "
        f"std {float(single.std()):.4f}, L2 {float(np.linalg.norm(single)):.3f}"
    )

    # --- the batch claim, measured rather than assumed (issue #31) ----------
    batching = config["batching"]
    claimed = bool(batching.get("supported", True))
    max_batch = int(batching.get("max_batch", 8))
    if claimed and not batch_axis_is_dynamic:
        raise CheckFailed(
            "the config claims batching, and the graph's leading dimension is fixed"
        )
    if claimed:
        mirrored = np.ascontiguousarray(tensor[:, :, :, ::-1])
        # Alternating rows, starting with the unmirrored one: row 0 is
        # comparable to the single-image run, and row 1 must not be.
        stacked = np.concatenate(
            [tensor if index % 2 == 0 else mirrored for index in range(max_batch)]
        )
        many = session.run(
            list(declared.output_names), {declared.input_name: stacked}
        )[0]
        if tuple(many.shape) != (max_batch, declared.dimensions):
            raise CheckFailed(
                f"batch of {max_batch} in, {many.shape} out"
            )
        agreement = cosine(many[0], single)
        if agreement < 0.9999:
            raise CheckFailed(
                f"row 0 of a batch of {max_batch} differs from the same image run "
                f"alone (cosine {agreement:.6f}); batching is not transparent"
            )
        distinct = cosine(many[0], many[1])
        if distinct > 0.99999:
            raise CheckFailed(
                "a mirrored image produced the same embedding; the batch rows are "
                "not independent"
            )
        log(
            f"batch of {max_batch}: shape {many.shape}, row 0 vs single "
            f"cosine {agreement:.7f}, mirrored row cosine {distinct:.4f}"
        )
    else:
        log("batching: config claims none, so none was exercised")


def parity(path: Path, snapshot: Path, declared: Declared, config: dict,
           image_path: Path, log) -> None:
    """The fp16 ONNX against the fp32 PyTorch reference, same input."""
    try:
        import torch
        from transformers import SiglipVisionModel
    except ImportError as error:
        raise Precondition("torch and transformers are required for --parity") from error
    import numpy as np
    import onnxruntime as ort

    tensor = preprocess(image_path, declared, config)
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    produced = session.run(list(declared.output_names), {declared.input_name: tensor})[0]

    reference_model = SiglipVisionModel.from_pretrained(
        snapshot, dtype=torch.float32, attn_implementation="eager"
    ).eval()
    with torch.no_grad():
        reference = reference_model(
            pixel_values=torch.from_numpy(tensor)
        ).pooler_output.numpy()

    similarity = cosine(produced, reference)
    largest = float(np.abs(produced - reference).max())
    log(
        f"parity: cosine {similarity:.8f} against the fp32 reference, "
        f"max |diff| {largest:.5f} (reference mean |value| "
        f"{float(np.abs(reference).mean()):.5f})"
    )
    if similarity < 0.9999:
        raise CheckFailed(
            f"fp16 cosine {similarity:.6f} against fp32 -- too far to be rounding"
        )


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="export_siglip2_vision_onnx.py",
        description=(
            "Export the SigLIP 2 so400m/384 vision tower to ONNX and verify it "
            "against models/configs/siglip2-so400m-384.json."
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="do not export; verify the artifact already installed",
    )
    parser.add_argument(
        "--parity",
        action="store_true",
        help="also compare the export against the PyTorch reference (needs torch)",
    )
    parser.add_argument(
        "--weights-dir",
        type=Path,
        default=DEFAULT_WEIGHTS_DIR,
        help="where the artifact is installed (default: models/weights)",
    )
    parser.add_argument(
        "--image",
        type=Path,
        default=DEFAULT_IMAGE,
        help="photograph to run through the graph (default: the repo's own)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="HF_HOME for the source download (default: the environment's)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an artifact that is already installed",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace, *, stream=sys.stdout) -> int:
    def log(message: str) -> None:
        print(message, file=stream, flush=True)

    declared = Declared.read()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    installed = args.weights_dir / declared.filename

    incomplete = False

    if args.verify:
        if not installed.is_file():
            raise Precondition(f"nothing to verify: {installed} does not exist")
        target = installed
    else:
        if installed.is_file() and not args.force:
            raise Precondition(
                f"{installed} already exists. Verify it with --verify, or pass "
                "--force to replace it. It is not overwritten silently: the file "
                "on disk may be the one somebody pinned."
            )
        snapshot = fetch_source(args.cache_dir, log)
        args.weights_dir.mkdir(parents=True, exist_ok=True)
        # Staged beside the destination so the install is an atomic rename on
        # the same filesystem, and so a failed check leaves nothing loadable
        # under the name the host reads.
        with tempfile.TemporaryDirectory(
            dir=args.weights_dir, prefix=".staging-export-"
        ) as workspace:
            staged = Path(workspace) / declared.filename
            export(snapshot, declared, staged, log)
            verify(staged, declared, config, args.image, log)
            # Required here: onnx is installed wherever torch.onnx.export ran,
            # so an export that cannot check its own precision is a broken
            # environment rather than a limited one.
            check_precision(staged, declared, log, required=True)
            if args.parity:
                parity(staged, snapshot, declared, config, args.image, log)
            shutil.move(str(staged), str(installed))
        target = installed
        log(f"installed: {installed}")

    if args.verify:
        verify(target, declared, config, args.image, log)
        # Best-effort here, and loud about it: a machine with only onnxruntime
        # can re-check every binding but not the stored dtype, and a check that
        # did not run is exit 3, not a pass.
        if not check_precision(target, declared, log, required=False):
            incomplete = True
        if args.parity:
            log(
                "--parity needs the source snapshot; run without --verify to "
                "export and compare in one pass"
            )
            incomplete = True

    digest = blake3_file(target)
    size = target.stat().st_size
    log("")
    log("UNPINNED. This digest was measured from the file this run produced.")
    log("It is not written to the config: a pin the producer generates certifies")
    log("only that it hashed its own output. Decide the artifact is the right one")
    log("-- --parity and the zero-shot check in docs/model-registry.md are what")
    log("that decision rests on -- then paste into models/configs/"
        f"{CONFIG_PATH.name}:")
    log("")
    log(f'    "blake3": "{digest}",')
    log(f'    "byte_size": {size},')
    log("")
    log("and re-run `python3 models/policy/digest.py --write` so the registry's")
    log("config_blake3 follows the config you just edited.")
    return EXIT_INCOMPLETE if incomplete else EXIT_OK


def main(argv=None) -> int:
    try:
        return run(parse_args(argv))
    except Precondition as error:
        print(f"cannot run: {error}", file=sys.stderr)
        return EXIT_PRECONDITION
    except CheckFailed as error:
        print(f"CHECK FAILED: {error}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
