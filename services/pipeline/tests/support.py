"""Shared test scaffolding: a real photo library, and a fake model host.

TWO THINGS ARE DELIBERATELY REAL HERE

* The photos are real JPEGs with real EXIF, written to a real folder, and the
  real Rust ingest binary walks them. A test that fed hand-written MediaRecords
  straight into the database would exercise none of the wiring this service
  exists to provide, and would pass while the pipeline was broken.

* The model host speaks real gRPC over a real loopback socket, using the
  generated stubs from `contracts/proto`. It is a FAKE MODEL, not a fake
  transport: the embeddings and detections are deterministic functions of the
  proxy id rather than anything learned. That boundary is the useful one --
  every message shape, error path, item-correlation rule and dimension check in
  `mlruntime.py` is exercised for real, while nothing pretends to be SigLIP.

WHY A FAKE HOST IS LEGITIMATE HERE AND WOULD NOT BE IN THE PRODUCT
The runner's job is to dispatch, checkpoint, correlate and refuse. Whether
SigLIP's embedding is good is a question for the eval harness against real
weights. What must never happen -- and what these tests exist to prevent -- is
the runner producing output when there is no host at all, which is tested
against the absence of this fake, not its presence.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import unittest
from concurrent import futures
from pathlib import Path
from typing import Any

TESTS_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = TESTS_ROOT.parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
INGEST_BINARY = REPO_ROOT / "workers/ingest/target/release/memory-engine-ingest"
PROTO_ROOT = REPO_ROOT / "contracts" / "proto"

if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


def require_ingest_binary() -> None:
    if not INGEST_BINARY.is_file():
        raise unittest.SkipTest(
            "the Rust ingest worker is not built; "
            f"cd {REPO_ROOT / 'workers/ingest'} && cargo build --release"
        )


# ------------------------------------------------------------------ photos --


def _palette(index: int) -> tuple[int, int, int]:
    """Distinct base colours so perceptual hashes do not collide by accident."""
    return (
        30 + (index * 53) % 200,
        40 + (index * 97) % 190,
        50 + (index * 149) % 180,
    )


def write_photo(
    path: Path,
    *,
    index: int,
    captured: str,
    offset: str = "+05:30",
    size: tuple[int, int] = (2400, 1800),
    blur: bool = False,
) -> Path:
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    image = Image.new("RGB", (width, height), _palette(index))
    draw = ImageDraw.Draw(image)
    # Structure, not noise: a flat colour has a Laplacian variance of zero and
    # a degenerate histogram, so every photo would score identically and the
    # selection tests would be measuring nothing.
    for step in range(0, width, 90):
        # NOT named `offset`: that is the EXIF zone parameter, and shadowing it
        # here silently wrote an integer into OffsetTimeOriginal, which the
        # ingest worker then failed to parse -- leaving every fixture photo
        # undated while the fixture claimed otherwise.
        column = (step + index * 37) % width
        draw.rectangle(
            [column, (step * 3 + index * 11) % height, column + 45, height],
            fill=(
                (index * 31 + step) % 256,
                (index * 61 + step * 3) % 256,
                (index * 17 + step * 7) % 256,
            ),
        )
    draw.ellipse(
        [width // 4, height // 4, width // 4 + width // 3, height // 4 + height // 3],
        outline=(250, 250, 250),
        width=9,
    )
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(radius=9))

    exif = Image.Exif()
    exif[0x010F] = "MemoryEngineTest"
    exif[0x0110] = "Fixture"
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = captured
    # OffsetTimeOriginal. Without it the ingest worker has a wall-clock reading
    # and no zone, so `captured_at.utc` stays null (correctly -- it must never
    # be fabricated) and the photo has no position on any timeline. A fixture
    # that omitted it would be testing the undated path while claiming to test
    # the dated one.
    exif_ifd[0x9011] = offset
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="JPEG", quality=92, exif=exif)
    return path


# Large enough that a full-bleed placement on a 300mm page clears the vendor's
# PREFERRED DPI, not merely its floor. render-print's gate refuses an AlbumSpec
# containing any check with `passed: false`, including the warning-severity
# "above the floor, below preferred" finding the album validator emits -- see
# the note in services/pipeline/README.md. A 2400px fixture lands at 300.3 DPI
# and is refused; this one lands near 400.
PRINT_SAFE_SIZE = (4800, 3600)


def make_library(
    root: Path,
    count: int = 8,
    *,
    start_index: int = 0,
    size: tuple[int, int] = (2400, 1800),
) -> list[Path]:
    """`count` photos, all inside one day so they form one event cluster."""
    paths = []
    for position in range(count):
        index = start_index + position
        minute = index % 60
        hour = 9 + (index // 60)
        captured = f"2026:03:14 {hour:02d}:{minute:02d}:00"
        paths.append(
            write_photo(
                root / f"IMG_{index:04d}.jpg", index=index, captured=captured, size=size
            )
        )
    return paths


# --------------------------------------------------------------- clips -----


def require_ffmpeg() -> str:
    """The FFmpeg the story stage will use, or a skip.

    A skip here is not the suite skipping its way to green: every number in a
    feature stream comes out of a decode, so without FFmpeg there is no such
    thing as a partially-correct run of this stage to assert against.
    """
    import shutil  # noqa: PLC0415

    found = shutil.which(os.environ.get("MEMORY_ENGINE_FFMPEG", "ffmpeg"))
    if found is None:
        raise unittest.SkipTest("ffmpeg is not on PATH; the story stage cannot decode")
    return found


def write_clip(
    path: Path,
    *,
    seconds: float = 2.0,
    rate: int = 30,
    size: tuple[int, int] = (640, 360),
    tone_hz: int = 440,
) -> Path:
    """One small, deterministic clip with a picture that MOVES and a tone.

    `testsrc2` rather than a still colour: the visual producers measure motion,
    shake, sharpness and novelty, and a static frame drives every one of them to
    the same value, so a stage that silently scored nothing would still look
    plausible. The tone gives the audio producer something to measure.
    """
    import subprocess  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        require_ffmpeg(),
        "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={size[0]}x{size[1]}:rate={rate}",
        "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000",
        "-t", str(seconds),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "64k",
        "-fps_mode", "passthrough",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not path.is_file():
        raise unittest.SkipTest(f"ffmpeg could not write a test clip: {result.stderr}")
    return path


# -------------------------------------------------------------- model host --


def _load_stubs() -> tuple[Any, Any, Any]:
    if str(PROTO_ROOT) not in sys.path:
        sys.path.insert(0, str(PROTO_ROOT))
    import grpc
    from generated.python import ml_runtime_pb2 as pb
    from generated.python import ml_runtime_pb2_grpc as pb_grpc

    return grpc, pb, pb_grpc


EMBEDDING_MODEL = "siglip2-so400m-384"
EMBEDDING_DIMENSIONS = 1152
FACE_MODEL = "scrfd-10g-bnkps"


def deterministic_embedding(proxy_id: str, dimensions: int) -> list[float]:
    """A unit vector that is a pure function of the proxy id.

    Same proxy, same vector, run after run -- which is what lets a test assert
    that a resumed run produces a byte-identical library.
    """
    seed = int(proxy_id[:16], 16)
    values = []
    state = seed | 1
    for _ in range(dimensions):
        state = (state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        values.append(((state >> 11) / float(1 << 53)) - 0.5)
    magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / magnitude for value in values]


def deterministic_face_count(proxy_id: str) -> int:
    return int(proxy_id[0], 16) % 3


class FakeMlRuntime:
    """A loopback MlRuntime host. Use as a context manager; `endpoint` is the target."""

    def __init__(
        self,
        *,
        serving: bool = True,
        models: tuple[str, ...] = (EMBEDDING_MODEL, FACE_MODEL),
        unloadable: tuple[str, ...] = (),
        embedding_dimensions: int = EMBEDDING_DIMENSIONS,
        fail_items: frozenset[str] = frozenset(),
    ) -> None:
        self._grpc, self._pb, self._pb_grpc = _load_stubs()
        self.serving = serving
        self.models = models
        self.unloadable = frozenset(unloadable)
        self.embedding_dimensions = embedding_dimensions
        self.fail_items = fail_items
        self.infer_calls: list[tuple[str, int]] = []
        self._server: Any = None
        self.endpoint = ""

    def __enter__(self) -> FakeMlRuntime:
        pb, pb_grpc, grpc = self._pb, self._pb_grpc, self._grpc
        host = self

        class Servicer(pb_grpc.MlRuntimeServicer):
            def Health(self, request, context):  # noqa: N802
                return pb.HealthResponse(
                    serving=host.serving,
                    load_mode=pb.LOAD_MODE_DEVELOPMENT,
                    loaded=[pb.ModelPin(model_id=name, version="test")
                            for name in host.models],
                    queue_depth=0,
                    uptime_seconds=1,
                )

            def ListModels(self, request, context):  # noqa: N802
                infos = []
                for name in host.models:
                    loadable = name not in host.unloadable
                    infos.append(
                        pb.ModelInfo(
                            pin=pb.ModelPin(model_id=name, version="test",
                                            weights_blake3="00" * 32,
                                            config_blake3="11" * 32),
                            task="image_embedding" if name == EMBEDDING_MODEL
                            else "face_detection",
                            loadable=loadable,
                            unloadable_reason=(
                                pb.UNLOADABLE_REASON_UNSPECIFIED if loadable
                                else pb.UNLOADABLE_REASON_WEIGHTS_MISSING
                            ),
                            currently_loaded=loadable,
                            max_batch=32,
                            output_kind=(
                                pb.OUTPUT_KIND_TENSORS if name == EMBEDDING_MODEL
                                else pb.OUTPUT_KIND_DETECTIONS
                            ),
                        )
                    )
                return pb.ListModelsResponse(
                    models=infos, load_mode=pb.LOAD_MODE_DEVELOPMENT
                )

            def Infer(self, request, context):  # noqa: N802
                host.infer_calls.append((request.model_id, len(request.items)))
                if request.model_id not in host.models:
                    return pb.InferResponse(
                        request_id=request.request_id,
                        error=pb.InferError(
                            code=pb.ERROR_CODE_MODEL_NOT_REGISTERED,
                            message="not registered",
                            retryable=False,
                        ),
                    )
                results = []
                for item in request.items:
                    if item.item_id in host.fail_items:
                        results.append(
                            pb.InferResult(
                                item_id=item.item_id,
                                error=pb.InferError(
                                    code=pb.ERROR_CODE_PROXY_NOT_FOUND,
                                    message="proxy missing",
                                    retryable=False,
                                ),
                            )
                        )
                    elif request.model_id == EMBEDDING_MODEL:
                        values = deterministic_embedding(
                            item.proxy_id, host.embedding_dimensions
                        )
                        results.append(
                            pb.InferResult(
                                item_id=item.item_id,
                                tensors=pb.TensorSet(
                                    tensors=[
                                        pb.Tensor(
                                            shape=[len(values)],
                                            dtype=pb.DTYPE_FLOAT32,
                                            data=struct.pack(
                                                f"<{len(values)}f", *values
                                            ),
                                        )
                                    ]
                                ),
                            )
                        )
                    else:
                        count = deterministic_face_count(item.proxy_id)
                        detections = [
                            pb.Detection(
                                box=pb.NormalizedBox(
                                    x=0.1 + 0.2 * face,
                                    y=0.2,
                                    w=0.12,
                                    h=0.16,
                                ),
                                score=0.9 - 0.05 * face,
                                landmark_scheme=pb.LANDMARK_SCHEME_INSIGHTFACE_5,
                            )
                            for face in range(count)
                        ]
                        results.append(
                            pb.InferResult(
                                item_id=item.item_id,
                                detections=pb.DetectionSet(
                                    detections=detections,
                                    score_threshold=0.5,
                                    nms_iou_threshold=0.4,
                                ),
                            )
                        )
                return pb.InferResponse(
                    request_id=request.request_id,
                    pin=pb.ModelPin(
                        model_id=request.model_id,
                        version="test",
                        weights_blake3="00" * 32,
                        config_blake3="11" * 32,
                    ),
                    runtime_used=pb.RUNTIME_TARGET_ONNXRUNTIME_CPU,
                    results=results,
                    duration_ms=1,
                    batch_size=len(request.items),
                )

        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        pb_grpc.add_MlRuntimeServicer_to_server(Servicer(), self._server)
        port = self._server.add_insecure_port("127.0.0.1:0")
        self.endpoint = f"127.0.0.1:{port}"
        self._server.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.stop(0).wait()
            self._server = None


def unused_endpoint() -> str:
    """An address nothing is listening on.

    Bound and immediately released so the port is real and free, which makes
    the connection fail fast with CONNECTION_REFUSED rather than hanging on a
    firewalled address.
    """
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"127.0.0.1:{port}"
