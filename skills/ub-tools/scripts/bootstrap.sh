#!/usr/bin/env bash
# bootstrap.sh — install unclebob's crapper, dryer and mutator as uv tools, pinned by tools.lock.
#
# Usage:
#   ./bootstrap.sh            # install the pinned commits (idempotent: a re-run is a no-op)
#   ./bootstrap.sh --update   # move every pin to its repo's latest default-branch commit, then install
#
# Nothing is cloned: uv builds each tool from its git commit into an isolated environment
# and puts the command on PATH. mutator gets crapper inside its own environment, which
# replaces the "clone crapper next to mutator" step of its README.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCK="${UB_TOOLS_LOCK:-$SKILL_DIR/tools.lock}"
REPO_BASE="https://github.com/unclebob"
TOOLS=(crapper dryer mutator)

for cmd in uv git; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd is required." >&2
    [[ "$cmd" == uv ]] && echo "       brew install uv, or https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
  fi
done

pin_of() {
  local sha
  sha="$(awk -v n="$1" '$1 == n { print $2 }' "$LOCK")"
  if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "ERROR: no 40-hex pin for $1 in $LOCK" >&2
    exit 3
  fi
  printf '%s' "$sha"
}

spec_of() { printf '%s @ git+%s/%s@%s' "$1" "$REPO_BASE" "$1" "$(pin_of "$1")"; }

# 1. Optionally move the pins
if [[ "${1:-}" == "--update" ]]; then
  for tool in "${TOOLS[@]}"; do
    sha="$(git ls-remote "$REPO_BASE/$tool" HEAD | cut -f1)"
    old="$(pin_of "$tool")"
    if [[ "$sha" != "$old" ]]; then
      sed -i.bak "s/^$tool $old\$/$tool $sha/" "$LOCK" && rm -f "$LOCK.bak"
      echo "==> $tool: $old -> $sha"
    fi
  done
elif [[ $# -gt 0 ]]; then
  echo "ERROR: unknown argument '$1' (expected --update or nothing)" >&2
  exit 3
fi

# 2. Install. Trusting the system certificate store lets a TLS-intercepting proxy that works
#    for git and curl also work for uv. uv renamed --native-tls to --system-certs; an older
#    uv rejects the new name, so pick whichever this uv accepts.
if uv tool install --help | grep -q -- '--system-certs'; then
  CERTS_FLAG=--system-certs
else
  CERTS_FLAG=--native-tls
fi
uv tool install "$CERTS_FLAG" "$(spec_of crapper)"
uv tool install "$CERTS_FLAG" "$(spec_of dryer)"
uv tool install "$CERTS_FLAG" --with "$(spec_of crapper)" "$(spec_of mutator)"

# 3. Verify each command runs
BIN_DIR="$(uv tool dir --bin)"
for tool in "${TOOLS[@]}"; do
  "$BIN_DIR/$tool" --help >/dev/null
  echo "==> $tool ready at $(pin_of "$tool")"
done
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "WARNING: $BIN_DIR is not on your PATH — run: uv tool update-shell" ;;
esac

# 4. Keep the tools' reports out of every repository: they write .metrics/ in the project root.
EXCLUDES="${UB_TOOLS_GIT_EXCLUDES:-$(git config --global --path core.excludesFile || true)}"
EXCLUDES="${EXCLUDES:-${XDG_CONFIG_HOME:-$HOME/.config}/git/ignore}"
mkdir -p "$(dirname "$EXCLUDES")"
touch "$EXCLUDES"
if ! grep -qxF '.metrics/' "$EXCLUDES"; then
  echo '.metrics/' >> "$EXCLUDES"
  echo "==> added .metrics/ to $EXCLUDES"
fi

# 5. Expose this skill to agents
if [[ -z "${UB_TOOLS_SKIP_LINK:-}" ]]; then
  for agents_dir in "$HOME/.claude/skills" "$HOME/.agents/skills"; do
    mkdir -p "$agents_dir"
    ln -sfn "$SKILL_DIR" "$agents_dir/ub-tools"
    echo "==> skill linked: $agents_dir/ub-tools"
  done
fi
