#!/usr/bin/env bash
# bootstrap.sh — set up (or restore) multi-account Google Workspace CLI on a machine.
#
# Profiles are defined in profiles.local.csv next to SKILL.md (gitignored,
# machine-local). Columns: profile,email,send_as (send_as optional).
#
# Usage:
#   ./bootstrap.sh                                   # interactive, or reuse existing CSV
#   ./bootstrap.sh personal=me@gmail.com work=me@corp.com:alias@corp.com
#
# Idempotent: re-running with an existing profiles.local.csv keeps it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="${GWSA_ROOT:-$HOME/.config/gws-accounts}"
BIN_DIR="${GWSA_BIN_DIR:-$HOME/.local/bin}"
CSV="${GWSA_PROFILES_CSV:-$SKILL_DIR/profiles.local.csv}"

# Scopes requested at login. gws's default preset lacks gmail.settings.basic,
# which is required to manage Gmail filters/rules — hence an explicit list.
LOGIN_SCOPES="https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.settings.basic,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/calendar"

# 1. Establish the profile list (args > existing CSV > interactive prompts)
if [[ $# -gt 0 ]]; then
  {
    echo "profile,email,send_as"
    for spec in "$@"; do
      profile="${spec%%=*}"
      rest="${spec#*=}"
      email="${rest%%:*}"
      send_as=""
      [[ "$rest" == *:* ]] && send_as="${rest#*:}"
      if [[ -z "$profile" || -z "$email" || "$profile" == "$spec" ]]; then
        echo "ERROR: bad profile spec '$spec' (expected name=email[:send_as])" >&2
        exit 3
      fi
      echo "$profile,$email,$send_as"
    done
  } > "$CSV"
  echo "==> wrote $CSV from arguments"
elif [[ -f "$CSV" ]]; then
  echo "==> using existing $CSV"
elif [[ -t 0 ]]; then
  echo "Define your Google account profiles (empty profile name to finish)."
  {
    echo "profile,email,send_as"
    while true; do
      read -r -p "Profile name (e.g. personal, work; empty to finish): " profile
      [[ -z "$profile" ]] && break
      read -r -p "  Google account email for '$profile': " email
      [[ -z "$email" ]] && { echo "  skipped (no email)"; continue; }
      read -r -p "  Send-as alias for '$profile' (optional, Enter to skip): " send_as
      echo "$profile,$email,$send_as"
    done
  } > "$CSV"
  echo "==> wrote $CSV"
else
  echo "ERROR: no profiles defined. Pass name=email[:send_as] arguments," >&2
  echo "       run interactively, or create $CSV first." >&2
  exit 3
fi
chmod 600 "$CSV"

# 2. Ensure the gws CLI is installed
if ! command -v gws >/dev/null 2>&1; then
  if command -v npm >/dev/null 2>&1; then
    echo "==> Installing @googleworkspace/cli via npm..."
    npm install -g @googleworkspace/cli
  elif command -v brew >/dev/null 2>&1; then
    echo "==> Installing googleworkspace-cli via Homebrew..."
    brew install googleworkspace-cli
  else
    echo "ERROR: gws not found and neither npm nor brew is available." >&2
    exit 1
  fi
fi
echo "==> gws: $(gws --version 2>&1 | head -1)"

# 3. Create per-account profile directories from the CSV
mkdir -p "$ROOT"
chmod 700 "$ROOT"
tail -n +2 "$CSV" | while IFS=, read -r profile email send_as; do
  [[ -z "$profile" ]] && continue
  mkdir -p "$ROOT/$profile"
  chmod 700 "$ROOT/$profile"
  # Relative symlink so the whole tree survives copy/rsync to another machine
  ln -sf ../client_secret.json "$ROOT/$profile/client_secret.json"
  echo "==> profile ready: $ROOT/$profile ($email${send_as:+, sends as $send_as})"
done

# 4. Install the gwsa wrapper
mkdir -p "$BIN_DIR"
install -m 0755 "$SCRIPT_DIR/gwsa" "$BIN_DIR/gwsa"
echo "==> installed: $BIN_DIR/gwsa"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "WARNING: $BIN_DIR is not on your PATH — add it in your shell profile." ;;
esac

# 5. Make the skill discoverable by agents (Claude Code + cross-runtime dir)
for agents_dir in "$HOME/.claude/skills" "$HOME/.agents/skills"; do
  mkdir -p "$agents_dir"
  ln -sfn "$SKILL_DIR" "$agents_dir/gws-multi-account"
  echo "==> skill linked: $agents_dir/gws-multi-account"
done

# 6. Status + next steps
echo
if [[ -f "$ROOT/client_secret.json" ]]; then
  chmod 600 "$ROOT/client_secret.json"
  echo "==> OAuth client found: $ROOT/client_secret.json"
else
  cat <<EOF
NEXT STEP — OAuth client secret is missing:
  Download the OAuth 'Desktop app' client JSON from your Google Cloud project
  (APIs & Services -> Credentials; consent screen External + your accounts as
  test users; host the project OUTSIDE any org that enforces Domain Restricted
  Sharing) and save it as:
    $ROOT/client_secret.json
EOF
fi
echo
echo "Then authenticate each profile (opens a browser; pick the MATCHING Google account;"
echo "if the page says 'response_type missing', copy the FULL printed URL instead of clicking):"
tail -n +2 "$CSV" | while IFS=, read -r profile email send_as; do
  [[ -z "$profile" ]] && continue
  echo "  gwsa $profile auth login --scopes \"$LOGIN_SCOPES\"   # $email"
done
echo
echo "Restoring from another machine instead? Copy the whole '$ROOT' directory"
echo "(it contains encrypted credentials + .encryption_key per profile) and you are done."
echo
echo "Check state anytime with: gwsa list"
