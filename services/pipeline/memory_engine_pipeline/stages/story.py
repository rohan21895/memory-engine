"""`score_moments` / `plan_reel` — declared, gated, and NOT faked.

`packages/story-engine` is built and tested: `plan_moments` turns a
`FeatureStream` into MomentRecords, `plan_reel` turns selected moments into an
EDL, `beats` locks cuts to a grid. None of that is the missing piece.

THE MISSING PIECE IS THE FEATURE STREAM.

`plan_moments` consumes per-frame motion, shake, exposure stability,
sharpness, luma, face presence, smile intensity, loudness, speech and novelty,
plus shot boundaries, word timestamps and audio events. Producing those
requires the 480p video proxy (ingest can make one, via `generate_video_proxy`)
AND a run of TransNetV2, SCRFD, CLAP and a transcriber through the model host.
Not one of those steps is wired in this runner, and the model host itself is
not running.

SO THIS STAGE REPORTS `UNAVAILABLE` AND STOPS.

The alternative -- synthesising a FeatureStream from whatever the records
happen to contain -- would produce a real EDL full of moments that were never
measured. It would render. It would look like a reel. That is the exact failure
mode this pipeline is built to make impossible, and it is worse here than
anywhere else because a video artifact is the hardest output for a person to
audit against the source.

What this stage DOES do is tell the truth about what is missing, in enough
detail to be actionable, and count the video the library actually holds so the
report is about this library rather than about the general case.
"""

from __future__ import annotations

from .base import StageContext, StageResult, StageStatus, blocked_by

STAGE = "story"

REQUIRED_STEPS = (
    "generate_video_proxy (workers/ingest, not invoked by this runner)",
    "detect_shots / transnetv2 (model host)",
    "detect_faces / scrfd (model host)",
    "audio_events / clap (model host)",
    "transcribe_audio / faster-whisper (model host)",
)


def run(ctx: StageContext) -> StageResult:
    upstream = ctx.require("ingest")
    if upstream is not None:
        return blocked_by(STAGE, upstream)

    videos = ctx.database.connection.execute(
        "SELECT COUNT(*) FROM media WHERE kind = 'video'"
    ).fetchone()[0]
    if videos == 0:
        return StageResult(
            stage=STAGE,
            status=StageStatus.SKIPPED,
            detail="the library holds no video, so there is nothing to score",
            counts={"videos": 0},
        )

    detail = (
        f"{videos} video(s) in the library and no way to score them yet: moment "
        "scoring needs a feature stream, and these producers are not wired — "
        + "; ".join(REQUIRED_STEPS)
    )
    ctx.reporter.event(STAGE, "stage_unavailable", detail, videos=videos)
    return StageResult(
        stage=STAGE,
        status=StageStatus.UNAVAILABLE,
        detail=detail,
        counts={"videos": videos, "missing_producers": list(REQUIRED_STEPS)},
    )
