"""The cheap look that decides whether the expensive look is needed.

INGEST IS THE ONLY STAGE THAT CANNOT BE MADE IDEMPOTENT BY CONTENT ADDRESSING
ALONE, because content addressing needs the content, and reading 500GB to
discover that none of it changed is the thing we are trying to avoid. So the
runner keeps an inventory: for every file under the source roots, its path,
size and mtime, taken from the directory entry the walk already produced. That
is one `stat` per file -- the same syscall the walk performs anyway -- and no
bytes read.

WHAT THIS IS AND IS NOT

It is a change DETECTOR, not a change ORACLE. A file edited within the mtime
granularity of the filesystem, to exactly the same length, is not detected.
That is the same bet `rsync` makes by default and the same bet every backup
tool makes, and it is stated here rather than assumed because a silent miss is
the failure mode this repository keeps producing. `--rescan` exists for the
user who has reason to distrust it: it discards the inventory and re-hashes
everything, which is the only honest way to be certain.

WHY MTIME AND SIZE RATHER THAN INODE

Inode numbers are stable within a volume and meaningless across one. A library
restored from backup, or moved between drives, gets fresh inodes for identical
content -- and would then be entirely re-ingested for no reason. Content
addressing catches the duplication afterwards (the BLAKE3 is the same, so the
MediaRecord is the same), but the re-hash is exactly the cost we are avoiding.

WHAT A REMOVED FILE DOES

Nothing destructive. A path that has disappeared is reported and recorded; the
MediaRecord stays. Exclusion is not deletion anywhere else in this system and
it is not deletion here: a photo on an unplugged external drive has not stopped
existing, and a pipeline that pruned the library on that basis would destroy a
catalogue every time somebody ejected a disk.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ids import canonical_source_roots, digest_of

__all__ = ["Entry", "Inventory", "InventoryDelta", "walk"]

INVENTORY_VERSION = 1


@dataclass(frozen=True, slots=True)
class Entry:
    path: str
    size: int
    mtime_ns: int

    def as_row(self) -> list[Any]:
        return [self.path, self.size, self.mtime_ns]


@dataclass(frozen=True, slots=True)
class Inventory:
    version: int
    roots: tuple[str, ...]
    options_digest: str
    entries: tuple[Entry, ...]

    @property
    def digest(self) -> str:
        return digest_of(
            {
                "version": self.version,
                "roots": list(self.roots),
                "options": self.options_digest,
                "entries": [entry.as_row() for entry in self.entries],
            }
        )

    def by_path(self) -> dict[str, Entry]:
        return {entry.path: entry for entry in self.entries}

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "roots": list(self.roots),
            "options_digest": self.options_digest,
            "entries": [entry.as_row() for entry in self.entries],
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> Inventory:
        return cls(
            version=int(payload["version"]),
            roots=tuple(payload["roots"]),
            options_digest=str(payload["options_digest"]),
            entries=tuple(
                Entry(path=str(row[0]), size=int(row[1]), mtime_ns=int(row[2]))
                for row in payload["entries"]
            ),
        )


@dataclass(frozen=True, slots=True)
class InventoryDelta:
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    @property
    def to_ingest(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.added) | set(self.changed)))

    def summary(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "changed": len(self.changed),
            "removed": len(self.removed),
        }


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _iter_files(
    root: Path, *, follow_symlinks: bool, include_hidden: bool, max_depth: int
) -> Iterator[tuple[str, os.stat_result]]:
    """Depth-limited walk matching the Rust worker's `walkdir` configuration.

    `max_depth` counts the root as depth 0, exactly as `walkdir` does, so an
    entry is yielded only while its depth is at most `max_depth`. The root
    itself is never subject to the hidden-file filter -- `walkdir`'s
    `is_hidden` guard is `depth() > 0` -- which matters because a delta scan
    passes individual file paths as roots, and one of them may legitimately be
    a dotfile the user asked for by name.

    Roots arrive already resolved by `canonical_source_roots`, so a root is
    never itself a symlink and `follow_symlinks` only governs what happens
    inside the tree.
    """
    try:
        if root.is_file():
            yield str(root), root.stat()
            return
    except OSError:
        return

    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        if depth >= max_depth:
            continue
        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda entry: entry.name)
        except OSError:
            continue
        for child in children:
            if not include_hidden and _is_hidden(child.name):
                continue
            try:
                if child.is_dir(follow_symlinks=follow_symlinks):
                    stack.append((Path(child.path), depth + 1))
                    continue
                if not child.is_file(follow_symlinks=follow_symlinks):
                    continue
                yield child.path, child.stat(follow_symlinks=follow_symlinks)
            except OSError:
                continue


def walk(
    roots: Iterable[str | os.PathLike[str]],
    *,
    follow_symlinks: bool = False,
    include_hidden: bool = False,
    max_depth: int = 32,
) -> Inventory:
    canonical = canonical_source_roots(roots)
    options_digest = digest_of(
        {
            "follow_symlinks": follow_symlinks,
            "include_hidden": include_hidden,
            "max_depth": max_depth,
        }
    )
    seen: dict[str, Entry] = {}
    for root in canonical:
        for path, stat_result in _iter_files(
            Path(root),
            follow_symlinks=follow_symlinks,
            include_hidden=include_hidden,
            max_depth=max_depth,
        ):
            # NFC so that a path that round-trips through a filesystem with a
            # different normalisation is the same key, not a spurious "added".
            key = unicodedata.normalize("NFC", path)
            if key not in seen:
                seen[key] = Entry(
                    path=key, size=stat_result.st_size, mtime_ns=stat_result.st_mtime_ns
                )
    return Inventory(
        version=INVENTORY_VERSION,
        roots=tuple(canonical),
        options_digest=options_digest,
        entries=tuple(seen[key] for key in sorted(seen)),
    )


def diff(previous: Inventory | None, current: Inventory) -> InventoryDelta:
    """What changed. A missing or stale previous inventory means everything is new.

    A version bump or an options change invalidates the comparison outright
    rather than being reconciled: `include_hidden` flipping from false to true
    makes every hidden file "added", which is correct, and trying to be clever
    about which of the two walks was authoritative is how a scan silently skips
    a subtree.
    """
    if (
        previous is None
        or previous.version != current.version
        or previous.roots != current.roots
        or previous.options_digest != current.options_digest
    ):
        return InventoryDelta(
            added=tuple(entry.path for entry in current.entries), changed=(), removed=()
        )

    before = previous.by_path()
    after = current.by_path()
    added = tuple(sorted(set(after) - set(before)))
    removed = tuple(sorted(set(before) - set(after)))
    changed = tuple(
        sorted(
            path
            for path in set(before) & set(after)
            if (before[path].size, before[path].mtime_ns)
            != (after[path].size, after[path].mtime_ns)
        )
    )
    return InventoryDelta(added=added, changed=changed, removed=removed)
