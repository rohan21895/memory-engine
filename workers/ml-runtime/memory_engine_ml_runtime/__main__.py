"""Command-line entry point for the local model host."""

from __future__ import annotations

import argparse
from pathlib import Path

from .catalog import DEFAULT_REPO_ROOT, ModelCatalog
from .service import MlRuntimeService, start_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--weights-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog = ModelCatalog(repo_root=args.repo_root, weights_dir=args.weights_dir)
    running = start_server(MlRuntimeService(catalog), port=args.port)
    print(f"ml-runtime serving on {running.address} ({catalog.mode} gate)", flush=True)
    try:
        running.server.wait_for_termination()
    except KeyboardInterrupt:
        running.stop(grace=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
