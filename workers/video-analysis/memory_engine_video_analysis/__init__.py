"""workers/video-analysis — the producers behind `plan_moments`.

`packages/story-engine`'s `plan_moments` consumes a `FeatureStream`. Until this
worker existed, nothing produced one, so film and reel — two of the three
outputs this product promises — could not run at all, and
`services/pipeline`'s story stage reported `unavailable` and named the missing
producers.

This package produces that stream, from the 480p proxy `workers/ingest`
already writes, using the exact `FeatureStream` / `Frame` / `Shot` / `Word` /
`AudioEvent` types defined in `memory_engine_story.moments`. It does not define
a parallel set: a second definition of the stream would be two contracts, and
the one that drifted would be discovered by a wrong reel rather than by a test.

WHAT RUNS TODAY AND WHAT IS A SEAM

    proxy + frame index    workers/ingest (Rust, hardware decode). REUSED, not
                           reimplemented — see `proxy.py`.
    photometry, motion,    `visual.py`. Classical, no weights, runs today.
    shake, novelty
    loudness / onsets /    `audio.py`. K-weighted (BS.1770-4) loudness, real
    silence                LUFS, runs today.
    shot boundaries        `shots.py`. Classical content detection runs today;
                           TransNetV2 is wired behind the model load gate and
                           refuses for want of weights.
    faces, smiles          NOT PRODUCED. SCRFD runs in the model host and the
                           host is not wired here, so `face_presence`,
                           `smile_intensity` and `max_face_area_ratio` are left
                           None — not measured — and the fusion renormalises
                           them away rather than scoring a fabricated zero.
    transcript             `transcript.py`. Interface + a null backend that
                           reports "no transcript available". No STT model is
                           shipped and none is faked.

Nothing here is re-exported. Importing any submodule must not drag in every
submodule — `memory_engine_story/__init__.py` records two suites that died that
way. Import what you need:

    from memory_engine_video_analysis.stream import analyse_proxy

WHY THIS FILE TOUCHES sys.path

Same reason `services/pipeline` does, and by the same rule: the intelligence
packages are consumed as libraries, none of them is installed, and CI runs each
component in place with `python3 -m unittest discover`. The insert is APPENDED,
so a genuinely installed `memory_engine_story` wins over the checkout.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

__version__ = "0.1.0"

_REPO_ROOT = _Path(__file__).resolve().parents[3]

_LIBRARY_PATHS = (_REPO_ROOT / "packages" / "story-engine",)

for _path in _LIBRARY_PATHS:
    _text = str(_path)
    if _path.is_dir() and _text not in _sys.path:
        _sys.path.append(_text)
