#!/bin/sh
# The base commit's engine, config, lists and baseline judge the change, so a
# change cannot exempt itself from the scrub.
# Usage: tools/ci/scrub-gate.sh <base-ref>
set -eu
base="${1:-}"
engine_path=tools/scrub-lint
zero=0000000000000000000000000000000000000000
gate_paths=".scrub-lint.conf .scrub-lint $engine_path tools/ci tools/check .github/workflows"

warn_if_gate_changed() {
  touched="$(git diff --name-only "$base" -- $gate_paths | tr '\n' ' ')"
  if [ -n "$touched" ]; then
    echo "::warning title=Scrub gate changed::this change edits the scrub gate ($touched). The base gate judges it; review the gate change by hand."
  fi
}

run_base_pinned_gate() {
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  git archive "$base" "$engine_path" | tar -x -C "$tmp"
  engine="$tmp/$engine_path/scripts/scrub_lint.py"
  echo "scrub-gate: engine, config, lists and baseline from $base"
  python3 "$engine" baseline shrink-check --lists public --config-rev "$base" --base-ref "$base"
  python3 "$engine" check --lists public --config-rev "$base"
}

run_default_gate() {
  echo "scrub-gate: no base commit; working-tree engine with built-in defaults"
  python3 "$engine_path/scripts/scrub_lint.py" check --lists public --config-rev none
}

if [ -z "$base" ] || [ "$base" = "$zero" ]; then
  run_default_gate
elif ! git rev-parse --verify --quiet "$base^{commit}" >/dev/null; then
  echo "scrub-gate: base $base is not in this clone; refusing to trust the change's own gate" >&2
  exit 1
else
  warn_if_gate_changed
  if git cat-file -e "$base:$engine_path/scripts/scrub_lint.py" 2>/dev/null; then
    run_base_pinned_gate
  else
    run_default_gate
  fi
fi
