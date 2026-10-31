#!/usr/bin/env bash
# mutator-in-place.sh — a mutator --test-command that runs each mutant in the real checkout.
#
# mutator puts the mutated file in a worker overlay whose other files are symlinks into the
# project. A path alias resolved from the project root (`@/…` in a Vite/Vitest config, which
# resolves symlinks to their real path) loads the ORIGINAL file, so every mutant "survives".
# This wrapper copies the overlay files that differ from the checkout over it, runs the test
# command from the project root, and restores them. Use it with --max-workers 1 only: mutants
# share the checkout, so parallel workers would race.
#
# Usage, from the project root:
#   mutator --max-workers 1 --test-command \
#     "$HOME/.claude/skills/ub-tools/scripts/mutator-in-place.sh $PWD ./node_modules/.bin/vitest run <test>" <file>
set -euo pipefail

ROOT="$(cd "${1:?usage: mutator-in-place.sh <project-root> <test command...>}" && pwd -P)"
shift
[[ $# -gt 0 ]] || { echo "ERROR: no test command" >&2; exit 64; }

# The baseline runs in the checkout itself.
if [[ "$(pwd -P)" == "$ROOT" ]]; then exec "$@"; fi

OVERLAY="$(pwd -P)"
BACKUP="$(mktemp -d)"
CHANGED=()
restore() {
  local rel
  for rel in "${CHANGED[@]}"; do cp "$BACKUP/$rel" "$ROOT/$rel"; done
  rm -rf "$BACKUP"
}
trap restore EXIT

while IFS= read -r -d '' file; do
  rel="${file#./}"
  [[ -f "$ROOT/$rel" ]] || continue
  if ! cmp -s "$rel" "$ROOT/$rel"; then
    mkdir -p "$BACKUP/$(dirname "$rel")"
    cp "$ROOT/$rel" "$BACKUP/$rel"
    CHANGED+=("$rel")
    cp "$rel" "$ROOT/$rel"
  fi
done < <(find . \( -path ./node_modules -o -path ./.git -o -path ./target -o -path ./.metrics \) -prune -o -type f -print0)

if [[ ${#CHANGED[@]} -eq 0 ]]; then
  echo "ERROR: overlay $OVERLAY holds no file that differs from $ROOT" >&2
  exit 65
fi

cd "$ROOT"
set +e
"$@"
status=$?
set -e
exit "$status"
