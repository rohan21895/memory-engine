"""Narrow adapter from the runtime to media-db's public proxy-only lookup."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class MediaDbProxyResolver:
    """Resolve one proxy with a short-lived, thread-safe media-db handle."""

    def __init__(self, repo_root: Path, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        package_root = (repo_root / "packages" / "media-db").resolve()
        if str(package_root) not in sys.path:
            sys.path.insert(0, str(package_root))
        from memory_engine_media_db import Database

        self._database_type = Database

    def __call__(self, proxy_id: str) -> Mapping[str, Any] | None:
        # Database's query API owns the lookup.  This worker never reaches into
        # its tables and never calls resolve_path(), which can return originals.
        with self._database_type.open(self.database_path, migrate=False) as database:
            return database.resolve_proxy(proxy_id)
