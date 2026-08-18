#!/bin/bash
# A LOCAL SUBSET of .github/workflows/ci.yml. Not a substitute for it.
#
# It once said it "runs the same commands CI runs". It did not: it omitted the
# shadow-file guard, the egress suite, the demo-script tests and the eval gate.
# A tree carrying an iCloud shadow copy could therefore print GREEN -- and a
# shadow copy is what broke main on 18 Aug, because the only guard that detects
# one was not in this list. The claim was repeated in agent briefs and in merge
# decisions for three days. (#97, found by the shipping agent.)
#
# So: this script now runs every platform-independent gate the workflow runs,
# and NAMES the ones it cannot. Green here means "the Linux-independent gates
# pass on this machine" and nothing more.
#
# The manifest below is checked against ci.yml by
# scripts/ci/check-local-ci-parity.mjs, which fails if the two drift.
set -o pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 1
find . -name __pycache__ -type d -not -path './.git/*' -exec rm -rf {} + 2>/dev/null

fail=0
run() {
  printf "%-26s " "$1"; shift
  if out=$("$@" 2>&1); then echo "pass"; else echo "FAIL"; echo "$out" | tail -25; fail=1; fi
}

# Freshness first: on CI this is a separate job with its own checkout, so it
# never sees the Cargo.lock that running cargo in the same tree produces.
run "codegen freshness"      node scripts/ci/check-codegen-freshness.mjs
run "shadow files"           node scripts/ci/check-no-shadow-files.mjs
run "lint (workspace)"       node scripts/ci/run-workspace-check.mjs lint
run "test (workspace)"       node scripts/ci/run-workspace-check.mjs test
run "contracts"              python3 -m unittest discover -s contracts/tests
run "egress"                 npm run test:egress --silent
run "demo scripts"           python3 -m unittest discover -s scripts/demo/tests
[ -f workers/ml-runtime/tests/run_required_suite.py ] && \
  run "ml-runtime required"  python3 workers/ml-runtime/tests/run_required_suite.py
if [ -d packages/eval-harness/gates ]; then
  ( cd packages/eval-harness && run "eval gate" python3 -m memory_engine_eval.harness gates/*.gate.json ) || fail=1
fi

echo "----"
echo "NOT RUN HERE (GitHub-only): Windows Ingest, Windows Desktop."
echo "A green below does not cover them. Push to see those."
[ $fail -eq 0 ] && echo "LOCAL SUBSET GREEN" || echo "LOCAL SUBSET RED"
exit $fail
