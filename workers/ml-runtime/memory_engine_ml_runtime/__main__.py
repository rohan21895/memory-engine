"""Command-line entry point for the local model host."""

from __future__ import annotations

import argparse
from pathlib import Path

from .catalog import DEFAULT_REPO_ROOT, ModelCatalog
from .media_db import MediaDbProxyResolver
from .service import MlRuntimeService, start_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--weights-dir", type=Path)
    parser.add_argument(
        "--database",
        type=Path,
        help="media-db SQLite file used for proxy_id resolution",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.database is not None and not args.database.is_file():
        raise SystemExit("--database must name an existing media-db file")
    catalog = ModelCatalog(repo_root=args.repo_root, weights_dir=args.weights_dir)
    resolver = (
        MediaDbProxyResolver(args.repo_root, args.database)
        if args.database is not None
        else None
    )
    running = start_server(
        MlRuntimeService(catalog, proxy_resolver=resolver), port=args.port
    )
    print(f"ml-runtime serving on {running.address} ({catalog.mode} gate)", flush=True)
    try:
        running.server.wait_for_termination()
    except KeyboardInterrupt:
        running.stop(grace=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
