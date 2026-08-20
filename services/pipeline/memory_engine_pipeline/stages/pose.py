"""Body-pose clustering: RTMO keypoints -> joint-angle signatures -> pose ids.

WHAT THIS STAGE DECIDES: nothing about a photo's quality or who is in it. It
answers one question the image embedding cannot -- "is this the same body pose
as that one" -- and writes `<workdir>/pose-clusters.json` (media_id -> a cluster
string like "solo#3"). The album selection engine reads that file to reward pose
breadth; absence of a key means "no pose signal", which selection treats as a
singleton. All of the geometry lives in `memory_engine_album.pose`, a pure,
dependency-free module the album package owns and tests on its own -- this stage
only runs the model and buckets the results.

WHY BUCKET BY FACE COUNT FIRST. RTMO merges tight embraces into a single body,
so its own person-count is unreliable for "solo vs couple vs group". The face
detector's count is the honest one, so poses are clustered *within* a face-count
bucket and never across it -- a solo portrait and one half of a couple never
land in the same pose id even when the visible limbs match.

IDEMPOTENT. Keypoints are cached under `<workdir>/outputs/pose/keypoints.json`,
keyed by media_id (which is content-addressed over the original), so a rerun
skips every image already inferred and only re-buckets, which is microseconds.
The cache stores keypoint arrays only -- never a file path -- so it is safe in a
public workdir.

DETERMINISTIC. Media are processed and clustered in a fixed order (capture time
then media_id), and RTMO on the ONNX Runtime CPU provider is deterministic for a
given input, so the same library yields byte-identical clusters every run.

THE MODEL. RTMO-m (body7, Apache-2.0) via `rtmlib`, which downloads the ONNX to
`~/.cache/rtmlib` on first use and reuses it thereafter -- see
`docs/model-registry.md` and `models/configs/rtmo-m-body7.json`. Nothing is
downloaded at import time; the fetch happens on the first inference of the first
run and never again while the cache stands.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import (
    StageContext,
    StageResult,
    StageStatus,
    blocked_by,
    write_json_atomically,
)

STAGE = "pose"

# rtmlib's prebuilt RTMO-m body7 SDK export. Pinned here (and audited in the
# model registry) so a swap is a deliberate edit, not whatever rtmlib defaults
# to. rtmlib caches the extracted ONNX under ~/.cache/rtmlib after first fetch.
_RTMO_ONNX = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/"
    "rtmo-m_16xb16-600e_body7-640x640-39e78cc4_20231211.zip"
)
_INPUT_SIZE = (640, 640)
_CLUSTER_THRESHOLD = 15.0  # degrees; matches the validated spike.
_CHECKPOINT_EVERY = 50     # flush the keypoint cache this often during inference.


def run(ctx: StageContext) -> StageResult:
    # Face counts come from the `face` table, which the faces stage populates.
    # When faces is not part of this invocation (`--stages pose` over an already
    # analysed library) require() returns None and we read whatever the table
    # already holds -- the album stage's real guard is the same.
    upstream = ctx.require("faces")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    from memory_engine_album import pose as geometry  # noqa: PLC0415

    media = _ordered_media(ctx)
    if not media:
        return StageResult(
            stage=STAGE, status=StageStatus.SKIPPED, detail="the library is empty"
        )

    cache_path = ctx.path("outputs", "pose", "keypoints.json")
    cache = _load_cache(cache_path)
    todo = [mid for mid, _ in media if mid not in cache]

    inferred = 0
    unreadable = 0
    if todo:
        detector = _load_detector(ctx)
        if detector is None:
            return StageResult(
                stage=STAGE,
                status=StageStatus.UNAVAILABLE,
                detail=(
                    "rtmlib / onnxruntime is not installed, so body pose cannot be "
                    "inferred; `pip install rtmlib onnxruntime opencv-python-headless`"
                ),
            )
        import cv2  # noqa: PLC0415

        ctx.reporter.event(
            STAGE,
            "stage_start",
            f"{len(media)} media, {len(cache)} cached, {len(todo)} to infer",
            units_total=len(todo),
        )
        for i, media_id in enumerate(todo):
            path = _proxy_path(ctx, media_id)
            image = cv2.imread(path) if path else None
            if image is None:
                # No readable pixels: recorded as "looked, found no body" so a
                # rerun does not keep retrying an unreadable proxy forever.
                cache[media_id] = None
                unreadable += 1
                continue
            cache[media_id] = _people_of(detector, image)
            inferred += 1
            if (i + 1) % _CHECKPOINT_EVERY == 0:
                write_json_atomically(cache_path, cache)
                ctx.reporter.event(
                    STAGE, "progress", f"inferred {i + 1}/{len(todo)}", units_done=i + 1
                )
        write_json_atomically(cache_path, cache)

    # ---- cluster (cheap; always recomputed for determinism) ----------------
    face_counts = _face_counts(ctx)
    items = [
        (media_id, _pose_of(geometry, cache.get(media_id)), _bucket(face_counts.get(media_id, 0)))
        for media_id, _ in media
    ]
    bodies_found = sum(1 for _, p, _ in items if p is not None)

    clusters: dict[str, str] = {}
    per_bucket: dict[str, int] = {}
    for bucket in ("solo", "couple", "group", "none"):
        sub = [(mid, pose) for mid, pose, b in items if b == bucket]
        labels, groups = geometry.cluster(sub, threshold=_CLUSTER_THRESHOLD)
        assigned = 0
        for media_id, label in labels.items():
            if label >= 0:  # a body with no measurable pose gets no key
                clusters[media_id] = f"{bucket}#{label}"
                assigned += 1
        if assigned:
            per_bucket[bucket] = len([g for g in groups if g])

    out_path = Path(ctx.workdir) / "pose-clusters.json"
    write_json_atomically(out_path, clusters)

    counts = {
        "media": len(media),
        "inferred": inferred,
        "cached": len(media) - inferred - unreadable,
        "unreadable": unreadable,
        "bodies_found": bodies_found,
        "assigned": len(clusters),
        "distinct_clusters": len(set(clusters.values())),
        **{f"buckets_{k}": v for k, v in per_bucket.items()},
    }
    detail = (
        f"{len(clusters)} media across {counts['distinct_clusters']} pose clusters"
        if todo
        else f"pose clusters unchanged: {counts['distinct_clusters']} across {len(clusters)} media"
    )
    ctx.reporter.event(STAGE, "stage_done", detail, **counts)
    return StageResult(
        stage=STAGE,
        status=StageStatus.COMPLETED,
        detail=detail,
        counts=counts,
        outputs=(str(out_path),),
    )


def _ordered_media(ctx: StageContext) -> list[tuple[str, str | None]]:
    """(media_id, captured_utc) in a fixed order: capture time, then id.

    Undated media sort first as an empty string, exactly as the spike did, so
    the greedy clusterer sees the same sequence on every run.
    """
    return [
        (row[0], row[1])
        for row in ctx.database.connection.execute(
            "SELECT media_id, captured_utc FROM media "
            "ORDER BY COALESCE(captured_utc, ''), media_id"
        ).fetchall()
    ]


def _face_counts(ctx: StageContext) -> dict[str, int]:
    """media_id -> people count, from the `face` table with the media summary
    as a fallback.

    The face table is the per-detection truth; a media with no rows there falls
    back to the record's own `face_count`, so a library analysed before faces
    were split into their own table still buckets sensibly.
    """
    counts = {
        row[0]: int(row[1])
        for row in ctx.database.connection.execute(
            "SELECT media_id, COUNT(*) FROM face GROUP BY media_id"
        ).fetchall()
    }
    for row in ctx.database.connection.execute(
        "SELECT media_id, face_count FROM media"
    ).fetchall():
        counts.setdefault(row[0], int(row[1] or 0))
    return counts


def _bucket(face_count: int) -> str:
    if face_count == 1:
        return "solo"
    if face_count == 2:
        return "couple"
    if face_count >= 3:
        return "group"
    return "none"


def _proxy_path(ctx: StageContext, media_id: str) -> str | None:
    """The 512px thumbnail proxy to run RTMO on, or the original as a fallback.

    Pose runs on the low-res proxy like the rest of local perception -- joint
    angles are scale-free, so the thumbnail carries the pose. Only when no
    thumbnail exists does it fall back to the source path.
    """
    proxies = ctx.database.proxies_for_media(media_id, kind="thumbnail_512")
    path = proxies[0].get("path") if proxies else None
    if path and Path(path).is_file():
        return path
    original = ctx.database.resolve_path(media_id)
    return original if original and Path(original).is_file() else None


def _people_of(detector: Any, image: Any) -> dict[str, Any] | None:
    """Every detected body as plain lists, with each one's keypoint-bbox area so
    the clusterer can pick the main subject in a group shot. None when the frame
    holds no body."""
    keypoints, scores = detector(image)
    people = []
    for person in range(keypoints.shape[0]):
        xs = keypoints[person, :, 0]
        ys = keypoints[person, :, 1]
        area = float((xs.max() - xs.min()) * (ys.max() - ys.min()))
        people.append(
            {
                "kpts": keypoints[person].tolist(),
                "scores": scores[person].tolist(),
                "area": area,
            }
        )
    return {"people": people} if people else None


def _pose_of(geometry: Any, record: dict[str, Any] | None) -> Any | None:
    """The pose signature of the largest body in a cached keypoint record."""
    if not record or not record.get("people"):
        return None
    person = max(record["people"], key=lambda p: p["area"])
    return geometry.make(person["kpts"], person["scores"])


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_detector(ctx: StageContext) -> Any | None:
    """Load RTMO once. Returns None when the inference deps are absent, which the
    caller reports as UNAVAILABLE (a build/install remedy, not a blocked run)."""
    try:
        from rtmlib import RTMO  # noqa: PLC0415
    except Exception as error:  # noqa: BLE001 - any import failure means "not built"
        ctx.reporter.event(STAGE, "note", f"rtmlib unavailable: {error}")
        return None
    return RTMO(
        onnx_model=_RTMO_ONNX,
        model_input_size=_INPUT_SIZE,
        backend="onnxruntime",
        device="cpu",
    )
