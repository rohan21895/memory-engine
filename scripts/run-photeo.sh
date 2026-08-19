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
if [ ! -x "$INGEST" ] || [ -n "$(find "$ROOT/workers/ingest/src" -newer "$INGEST" -name '*.rs' 2>/dev/null | head -1)" ]; then
  command -v cargo >/dev/null || { echo "missing: cargo (install Rust to build the ingest worker)" >&2; exit 2; }
  cargo build --release --manifest-path "$ROOT/workers/ingest/Cargo.toml" || exit 2
fi
echo "   ok"

# A dist older than its src silently runs last week's renderer -- this exact
# mistake has produced corrupt output more than once, so it is checked every run.
step "render workers"
for worker in render-print render-video; do
  DIST="$ROOT/workers/$worker/dist"
  if [ ! -d "$DIST" ] || [ -n "$(find "$ROOT/workers/$worker/src" -newer "$DIST" -name '*.ts' 2>/dev/null | head -1)" ]; then
    echo "   rebuilding $worker (dist missing or older than src)"
    (cd "$ROOT/workers/$worker" && npm install --silent && npm run build --silent && touch dist) || exit 2
  fi
done
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

# The print renderer refuses a book without a safety clearance, and the
# clearance is computed over the exact album that will be printed. So: plan
# everything except the print first, classify that album, then render it.
SAFETY_HEAD="$ROOT/models/weights/safety-head/nsfw-siglip-head.v1.json"

step "pipeline (plan + videos)"
MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS=1 \
PYTHONPATH="$ROOT/services/pipeline" \
  "$VENV/bin/python" -m memory_engine_pipeline "${SOURCES[@]}" \
  --workdir "$WORKDIR" --ml-runtime "127.0.0.1:$PORT" \
  --no-render-print "${EXTRA_ARGS[@]}"
CODE=$?

if [ -f "$SAFETY_HEAD" ]; then
  step "print safety clearance"
  "$VENV/bin/python" "$ROOT/scripts/classify_album_clearance.py" --workdir "$WORKDIR"
  CLEARANCE=$?
  if [ $CLEARANCE -ne 0 ]; then
    echo "   one or more photos were blocked; the album will not print until they"
    echo "   are excluded (MediaRecord.exclusion) or a human override is recorded."
  fi
  step "pipeline (print render)"
  MEMORY_ENGINE_ALLOW_UNVERIFIED_MODELS=1 \
  PYTHONPATH="$ROOT/services/pipeline" \
    "$VENV/bin/python" -m memory_engine_pipeline "${SOURCES[@]}" \
    --workdir "$WORKDIR" --ml-runtime "127.0.0.1:$PORT" \
    --no-render-video --allow-development-clearance "${EXTRA_ARGS[@]}"
  PRINT_CODE=$?
  [ $PRINT_CODE -ne 0 ] && CODE=$PRINT_CODE
else
  step "print safety clearance"
  echo "   SKIPPING the album PDF: no safety classifier head at"
  echo "   $SAFETY_HEAD"
  echo "   Build it once with: python scripts/models/build_safety_head.py"
  echo "   (downloads the SigLIP 2 text tower; needs torch + transformers)"
fi

step "outputs"
found=0
for f in "$WORKDIR"/outputs/pdf/*.pdf "$WORKDIR"/outputs/video/*.mp4; do
  [ -f "$f" ] && { echo "   $f"; found=1; }
done
[ $found -eq 0 ] && echo "   none were produced -- read the report above for why"
exit $CODE
