#!/usr/bin/env bash
# pnpm-coverage.sh — write coverage/lcov.info for crapper and mutator in a pnpm + Vitest project.
#
# crapper installs @vitest/coverage-v8 with npm, which fails inside a pnpm tree; it then warns
# and scores every function at 0% coverage. This script adds the provider with pnpm at the
# project's own Vitest version, runs Vitest's binary directly (a `pnpm` script would re-verify
# dependencies and prune the provider first), and restores the manifests whatever happens.
#
# Usage, from the project root (arguments go to `vitest run`):
#   pnpm-coverage.sh --project unit --coverage.include='lib/apps/**'
#   crapper --use-existing-coverage lib/apps
set -euo pipefail

[[ -f pnpm-lock.yaml ]] || { echo "ERROR: no pnpm-lock.yaml here; run from a pnpm project root" >&2; exit 1; }
VITEST_VERSION="$(node -p "require('vitest/package.json').version" 2>/dev/null)" \
  || { echo "ERROR: vitest is not installed in this project (run pnpm install)" >&2; exit 1; }

BACKUP="$(mktemp -d)"
MANIFESTS=(package.json pnpm-lock.yaml pnpm-workspace.yaml)
for f in "${MANIFESTS[@]}"; do [[ -f "$f" ]] && cp "$f" "$BACKUP/$f"; done
restore() {
  for f in "${MANIFESTS[@]}"; do [[ -f "$BACKUP/$f" ]] && cp "$BACKUP/$f" "$f"; done
  pnpm install --frozen-lockfile --prefer-offline >/dev/null 2>&1 \
    || echo "WARNING: manifests restored, but pnpm install failed; run it by hand" >&2
  rm -rf "$BACKUP"
}
trap restore EXIT

pnpm add -D "@vitest/coverage-v8@$VITEST_VERSION" >/dev/null
./node_modules/.bin/vitest run --coverage.enabled --coverage.provider=v8 \
  --coverage.reporter=lcov --coverage.reportsDirectory=coverage "$@"
echo "==> wrote coverage/lcov.info; next: crapper --use-existing-coverage <path>"
