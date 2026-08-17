"""services/pipeline — the end-to-end job runner.

A folder goes in; a searchable, deduplicated, analysed library and whatever
finished outputs the machine can actually produce come out. Every unit of work
is a JobSpec with a content-addressed id, every stage is resumable, and every
stage that cannot run says so instead of producing an empty success.

Nothing is re-exported here on purpose -- see `stages/__init__.py`. Import what
you need:

    from memory_engine_pipeline.runner import run_pipeline
    from memory_engine_pipeline.stages.base import Settings

WHY THIS FILE TOUCHES sys.path

The intelligence packages are consumed as libraries and none of them is
installed: the repository has no Python install step, and CI runs each
component's tests in place with `python3 -m unittest discover`. Every existing
consumer solves this the same way (`scripts/demo/run_demo.py` inserts the two
package directories it needs), so this does it once, in the one place every
import passes through, rather than in eight modules and every test file.

The insert is APPENDED, not prepended. A `memory_engine_media_db` that the
operator has genuinely installed -- in a venv, in a packaged desktop build --
must win over the checkout, or a shipped application would silently run
whatever happened to be sitting next to it on disk.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

__version__ = "0.1.0"

_REPO_ROOT = _Path(__file__).resolve().parents[3]

_LIBRARY_PATHS = (
    _REPO_ROOT / "packages" / "media-db",
    _REPO_ROOT / "packages" / "ranking-engine",
    _REPO_ROOT / "packages" / "album-engine",
    _REPO_ROOT / "packages" / "story-engine",
    _REPO_ROOT / "packages" / "prompt-engine",
    # `workers/video-analysis` is on this list even though it is a worker rather
    # than a package: it runs IN PROCESS (pure Python plus an FFmpeg
    # subprocess), it is the only producer of a `FeatureStream`, and the story
    # stage consumes it as a library exactly the way it consumes story-engine.
    # A second copy of the feature producers inside this service would be a
    # second answer to what a video's motion, shake and loudness are.
    _REPO_ROOT / "workers" / "video-analysis",
)

for _path in _LIBRARY_PATHS:
    _text = str(_path)
    if _path.is_dir() and _text not in _sys.path:
        _sys.path.append(_text)
