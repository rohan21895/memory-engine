#!/bin/bash
# Local stand-in for .github/workflows/ci.yml.
#
# Runs the same commands CI runs, with the same interpreter CI uses (system
# python3, NOT a venv), so a pass here means what a pass there means. Written
# when GitHub Actions was blocked on billing and merges would otherwise have
# been blind; kept because a local pre-push check is worth having anyway.
#
# It needs the same dependencies the CI workflow installs:
#   python3 -m pip install "pydantic>=2.7" "jsonschema>=4.20" "blake3>=0.4" \
#                          "pillow>=10.0" "numpy>=1.26" opencv-python-headless \
#                          grpcio onnxruntime -e ./workers/ml-runtime
set -o pipefail
# Resolve the repository root from this script's own location rather than
# hardcoding it. The original held one developer's absolute path, which both
# leaked a home directory into a public repo and made the script useless to
# anyone else who cloned it.
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 1
find . -name __pycache__ -type d -not -path './.git/*' -exec rm -rf {} + 2>/dev/null
fail=0
run() { printf "%-24s " "$1"; shift; if out=$("$@" 2>&1); then echo "pass"; else echo "FAIL"; echo "$out" | tail -25; fail=1; fi; }

# Freshness first: on CI this is a separate job with its own checkout, so it
# never sees the Cargo.lock that running cargo in the same tree produces.
run "codegen freshness"   node scripts/ci/check-codegen-freshness.mjs
run "lint (workspace)"    node scripts/ci/run-workspace-check.mjs lint
run "test (workspace)"    node scripts/ci/run-workspace-check.mjs test
[ -f workers/ml-runtime/tests/run_required_suite.py ] && run "ml-runtime required" python3 workers/ml-runtime/tests/run_required_suite.py
echo "----"
[ $fail -eq 0 ] && echo "LOCAL CI GREEN" || echo "LOCAL CI RED"
exit $fail
