#!/bin/bash
# One command: a folder of photos and videos in; an album PDF, a reel and a
# short film out.
#
#   bash scripts/run-photeo.sh /path/to/your/photos
#   bash scripts/run-photeo.sh /path/A /path/B --workdir /path/to/run
#
# What it does, in order: creates a private Python environment on first run,
# builds the Rust ingest worker if it is missing, creates the library database,
# starts the local model host pointed at that database, runs the full pipeline
# (ingest -> analysis -> faces -> ranking -> album -> story -> renders), stops
# the host, and prints where the outputs are.
#
# Everything stays on this machine. Originals are read, never written. All
# derived data lives in the workdir (default: runs/library under the repo).
set -o pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SOURCES=()
WORKDIR="$ROOT/runs/library"
EXTRA_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --workdir) WORKDIR="$2"; shift 2 ;;
    --*) EXTRA_ARGS+=("$1"); [ $# -gt 1 ] && [[ "$2" != --* ]] && [[ "$2" != /* ]] && { EXTRA_ARGS+=("$2"); shift; }; shift ;;
    *) SOURCES+=("$1"); shift ;;
  esac
done
if [ ${#SOURCES[@]} -eq 0 ]; then
  echo "usage: bash scripts/run-photeo.sh /path/to/your/photos [--workdir DIR]" >&2
  exit 2
fi
for src in "${SOURCES[@]}"; do
  [ -d "$src" ] || { echo "not a folder: $src" >&2; exit 2; }
done

step() { printf '\n== %s\n' "$1"; }

step "checking tools"
PATH="/opt/homebrew/bin:$PATH"
for tool in ffmpeg node python3; do
  command -v "$tool" >/dev/null || { echo "missing: $tool (install with Homebrew)" >&2; exit 2; }
done

step "python environment"
VENV="$ROOT/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || exit 2
  "$VENV/bin/pip" install --quiet --upgrade pip
fi
"$VENV/bin/python" - <<'EOF' 2>/dev/null || "$VENV/bin/pip" install --quiet \
    grpcio protobuf numpy opencv-python-headless pillow blake3 jsonschema pydantic onnxruntime
import grpc, google.protobuf, numpy, cv2, PIL, blake3, jsonschema, pydantic, onnxruntime
EOF
"$VENV/bin/python" - <<'EOF' || exit 2
import grpc, google.protobuf, numpy, cv2, PIL, blake3, jsonschema, pydantic, onnxruntime
EOF
echo "   ok"

step "ingest worker"
INGEST="$ROOT/workers/ingest/target/release/memory-engine-ingest"
if [ ! -x "$INGEST" ]; then
  command -v cargo >/dev/null || { echo "missing: cargo (install Rust to build the ingest worker)" >&2; exit 2; }
  cargo build --release --manifest-path "$ROOT/workers/ingest/Cargo.toml" || exit 2
fi
echo "   ok"

step "library database"
mkdir -p "$WORKDIR"
PYTHONPATH="$ROOT/packages/media-db" "$VENV/bin/python" - "$WORKDIR/library.db" <<'EOF' || exit 2
import sys
from memory_engine_media_db import Database
Database.open(sys.argv[1]).close()
EOF
echo "   $WORKDIR/library.db"

step "model host"
PORT=50251
HOST_LOG="$WORKDIR/ml-host.log"
MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS=1 \
PYTHONPATH="$ROOT:$ROOT/workers/ml-runtime" \
  "$VENV/bin/python" -m memory_engine_ml_runtime \
  --port "$PORT" --database "$WORKDIR/library.db" > "$HOST_LOG" 2>&1 &
HOST_PID=$!
trap 'kill "$HOST_PID" 2>/dev/null' EXIT
for _ in $(seq 1 60); do
  grep -q "serving" "$HOST_LOG" 2>/dev/null && break
  kill -0 "$HOST_PID" 2>/dev/null || { echo "model host died; see $HOST_LOG" >&2; exit 2; }
  sleep 1
done
grep -q "serving" "$HOST_LOG" || { echo "model host never came up; see $HOST_LOG" >&2; exit 2; }
echo "   serving on 127.0.0.1:$PORT"

step "pipeline"
MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS=1 \
PYTHONPATH="$ROOT/services/pipeline" \
  "$VENV/bin/python" -m memory_engine_pipeline "${SOURCES[@]}" \
  --workdir "$WORKDIR" --ml-runtime "127.0.0.1:$PORT" "${EXTRA_ARGS[@]}"
CODE=$?

step "outputs"
found=0
for f in "$WORKDIR"/outputs/pdf/*.pdf "$WORKDIR"/outputs/video/*.mp4; do
  [ -f "$f" ] && { echo "   $f"; found=1; }
done
[ $found -eq 0 ] && echo "   none were produced -- read the report above for why"
exit $CODE
