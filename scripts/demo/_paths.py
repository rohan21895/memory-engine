"""Where the demo scripts refuse to operate.

Shared by `make_library.py` (which must not WRITE into a real photo folder) and
`run_demo.py` (which must not READ one). Kept in its own module with no
third-party imports so `run_demo` does not acquire a Pillow/numpy dependency
just to reuse ten lines of guard.

This is a guardrail, not a security boundary: it stops the obvious accident of
pointing the demo at `~/Pictures` or `~/Downloads`. Anyone who genuinely wants
to scan their own library will find `--i-know-this-is-my-real-library`, and at
that point it is a decision rather than a mistake.
"""

from __future__ import annotations

from pathlib import Path

FORBIDDEN_DIR_NAMES = {"Downloads", "Pictures", "Photos", "Movies", "DCIM"}
FORBIDDEN_SUFFIXES = {".photoslibrary", ".aplibrary", ".migratedphotolibrary"}


def real_media_location(path: Path) -> str | None:
    """Return a human explanation if `path` looks like real personal media."""
    resolved = path.expanduser().resolve()
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):  # pragma: no cover - no home directory
        home = None

    for part in [resolved, *resolved.parents]:
        if home is not None and part == home:
            break
        if part.name in FORBIDDEN_DIR_NAMES:
            return f"{part} is where real photographs live"
        if part.suffix.lower() in FORBIDDEN_SUFFIXES:
            return f"{part} is a photo library bundle"
    return None
