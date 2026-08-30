---
name: gws-multi-account
description: Use when a Google Workspace CLI (gws) command must target a specific Google account — sending Gmail (including from a send-as alias), managing Gmail settings/filters/rules, reading/writing Drive files, Docs, Sheets, or Calendar across multiple accounts — or when setting up, moving, or fixing auth for gws on a machine.
---

# gws multi-account

## Overview

`gws` (github.com/googleworkspace/cli) handles **one account per config directory** — there is no `--account` flag. Account selection = pointing `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` at the right profile. The `gwsa` wrapper does this: `gwsa <profile> <any gws args…>`.

**Never run bare `gws` or invent config paths for account-specific work. Run `gwsa list` first to see the real profiles and their auth state.**

## Profiles on this machine

**Read `profiles.local.csv` next to this SKILL.md** — it maps profile → account for THIS machine (gitignored; columns `profile,email,send_as`; an empty `send_as` means mail goes out as the primary address, otherwise pass it via `+send --from <send_as>`). If the file is missing, run `scripts/bootstrap.sh` (interactive, or `./bootstrap.sh work=me@corp.com:alias@corp.com personal=me@gmail.com`).

Profile config dirs live under `~/.config/gws-accounts/<profile>/`, with a shared OAuth `client_secret.json` symlinked into each.

## Quick reference

```bash
gwsa list                                                  # profiles + auth state (start here)
gwsa work gmail +send --to a@b.com --from alias@corp.com \
     --subject 'Hi' --body 'Hello' [-a file.pdf] [--html] [--draft] [--dry-run]
gwsa personal gmail +triage                                # unread summary; also +read, +reply, +forward
gwsa work drive files list --params '{"pageSize": 10}'
gwsa work drive +upload ./report.pdf                       # upload (positional; --name, --parent)
gwsa work docs documents create --json '{"title": "My doc"}'       # new Doc (returns documentId)
gwsa work docs +write --document ID --text 'Appended text'         # append to a Doc
gwsa work drive files export --params '{"fileId": "ID", "mimeType": "text/plain"}' \
     --output out.txt   # read a Doc as text — --output MUST be inside the current directory
gwsa work gmail users settings filters create --params '{"userId":"me"}' \
     --json '{"criteria":{"from":"noisy@example.com"},"action":{"removeLabelIds":["INBOX"]}}'  # Gmail rule
gwsa <profile> schema gmail.users.settings.filters.create  # discover any API params
```

Use `+helper` commands (they handle MIME, base64, threading); drop to raw `service resource method --params/--json` only when no helper fits. Every helper takes `--help` and `--dry-run`.

## Auth & new accounts

```bash
gwsa <profile> auth status   # exact files it resolves — best debugging tool
gwsa <profile> auth login --scopes "https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.settings.basic,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/calendar"
```

Use the explicit `--scopes` list above (add scopes to taste): the default preset (what `-s gmail,…` filters) requests `gmail.modify` but NOT `gmail.settings.basic`, so Gmail settings writes — filters/rules, vacation responder — fail with `insufficient authentication scopes`. `--scopes` REPLACES the whole set, so list everything you need. In the browser, pick the Google account **matching the profile**. While the OAuth app is in Testing mode, refresh tokens expire every ~7 days — expect periodic re-logins (or publish the app to production, unverified, to stop that).

New account = add a line to `profiles.local.csv` and re-run `scripts/bootstrap.sh` (it's idempotent), add the email as a test user on the OAuth consent screen, then login.

## Moving to another machine

Profiles use `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` (set by `gwsa`), so credentials + `.encryption_key` live inside each profile dir:

1. Clone this repo, run `scripts/bootstrap.sh` (recreates profiles from your answers or an existing CSV, installs gws + gwsa, links the skill into `~/.claude/skills` and `~/.agents/skills`), then
2. Either copy the whole `~/.config/gws-accounts/` tree AND `profiles.local.csv` (done — no re-auth), or drop in `client_secret.json` and run the logins. Keep dirs `700`; never commit the tree or the CSV to git.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `access_denied` at login | Add that email under OAuth consent screen → Test users |
| `Error 400: invalid_request` / `Required parameter is missing: response_type` at login | The auth URL was line-wrapped by the terminal and the click lost its query params — copy the FULL printed URL into the browser instead |
| `403 Request had insufficient authentication scopes` | The stored token misses a scope (e.g. `gmail.settings.basic` for filters/rules) — re-run `auth login` with the full `--scopes` list above |
| Scope/consent error at login | Trim the `--scopes` list (unverified apps are capped at ~25 scopes) |
| `403 … serviceusage.serviceUsageConsumer … to use project <id>` on any API call | gws bills quota to the OAuth client's project, so EVERY logged-in account needs the **Service Usage Consumer** IAM role on that project (Console → IAM → Grant access; allow a few minutes to propagate) |
| IAM grant rejected: `'Domain Restricted Sharing' organization policy` | The OAuth client's project lives in a Workspace org that blocks external principals. Host the client in a **no-org project** (created by a consumer @gmail.com account) instead — cross-domain grants are unrestricted there. All accounts must then re-login (tokens are tied to the client) |
| Auth works, then dies ~7 days later | Testing-mode refresh tokens expire weekly — re-login, or publish the OAuth app to production (unverified) |
| Workspace account blocked by admin policy | Allowlist the OAuth client ID in Admin console → App access control |
| `Invalid from address` on `+send --from` | Alias missing in Gmail → verify: `gwsa <p> gmail users settings sendAs list --params '{"userId":"me"}'` |
