# Findings: Google Workspace CLI (`gws`) multi-account setup

Research log behind the `gws-multi-account` skill. Verified on macOS with `gws 0.22.5` (2026-08-30).

## Sources

- Jonathan Lewell — [Connect Multiple Google Accounts to Claude Code (Google Workspace CLI)](https://www.youtube.com/watch?v=hxggsSm3Fis&t=846s) (YouTube; the multi-account part starts around 14:06)
- [googleworkspace/cli README](https://github.com/googleworkspace/cli) and its issue tracker (linked inline below)
- Everything else in this log is field-verified on this setup

## The CLI

- Repo: <https://github.com/googleworkspace/cli> — Rust binary, "not an officially supported Google product".
- Install: `npm install -g @googleworkspace/cli` or `brew install googleworkspace-cli`. Binary: `gws`.
- Generic surface: `gws <service> <resource> <method> --params '<JSON>' --json '<JSON>'`, built dynamically
  from the Google Discovery Service (drive, gmail, docs, sheets, calendar, slides, tasks, people, chat, …).
- Helper surface: `+`-prefixed helpers (`gws gmail +send`, `gws calendar +agenda`, `gws workflow +standup-report`, …)
  that hide MIME/base64/RFC 5322 plumbing.
- The repo also ships 100+ agent skills (`skills/gws-*`), installable via `npx skills add https://github.com/googleworkspace/cli`.

## Multi-account: state of the art (as of v0.22.5)

- **One account per config directory.** There is no `--account` flag and no `gws auth list` in the
  released CLI, despite docs/issues suggesting otherwise (feature requests
  [#78](https://github.com/googleworkspace/cli/issues/78),
  [#293](https://github.com/googleworkspace/cli/issues/293); doc/behavior mismatch
  [#181](https://github.com/googleworkspace/cli/issues/181), closed by PR #223).
- **The supported workaround** is `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` — point it at a different directory
  per account. Each directory holds its own `client_secret.json`, `credentials.enc`, token cache.
- We wrapped this in `gwsa <profile> <gws args…>` (see `skills/gws-multi-account/scripts/gwsa`).
- Watch upstream releases: when native multi-account lands, the wrapper can be retired with no data
  migration (profiles are plain config dirs).

## Credential storage & portability

- Credentials are encrypted at rest (AES-256-GCM). The key lives in the **OS keyring by default** —
  which is NOT portable across machines.
- `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` stores the key at `<config_dir>/.encryption_key` instead.
  With that, **copying the whole config tree to another machine is sufficient** (tested layout:
  `~/.config/gws-accounts/`). Trade-off: key on disk ⇒ keep dirs `700`, files `600`.
- Alternative migration path (per README): `gws auth export --unmasked > credentials.json` on the
  authenticated machine, then `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE=/path/credentials.json` on the target
  (intended for headless/CI).
- `gws auth status` prints exactly which files it resolves (`client_config`, `encrypted_credentials`,
  `plain_credentials`, keyring backend) — the best debugging tool here.

## OAuth app + consent screen gotchas

- One OAuth **Desktop app** client (single GCP project) can serve many Google accounts, including
  consumer `@gmail.com` and multiple Workspace domains — each identity just completes its own
  `gws auth login` against the same `client_secret.json`.
- Consent screen must be **External**; every account that will log in must be added under
  **Audience → Test users** while the app is in Testing.
- **Testing-mode scope cap:** unverified apps are limited to ~25 scopes; `gws auth login` default
  presets exceed it. Fix: `gws auth login -s gmail,drive,docs,sheets,calendar` (limit services).
- **Testing-mode refresh tokens expire after 7 days** (Google OAuth policy for External apps in
  Testing with sensitive scopes) ⇒ weekly re-login. Optional escape hatch: publish the app
  ("In production") without verification — login then shows an "unverified app" warning you can
  bypass for your own accounts; tokens stop expiring weekly. Note Gmail scopes are *restricted*
  scopes, so behavior of the unverified-production path can change on Google's side.
- Workspace domains can block third-party apps via Admin console **App access control** — if login
  fails for a Workspace account with an admin-policy error, allowlist the OAuth client ID there.
- `gws auth setup` can automate project + client creation but requires `gcloud` installed and
  authenticated; the manual Console path needs no extra tooling.

## Field-verified login/runtime gotchas (2026-08-30)

- **Truncated auth URL**: `gws auth login` prints a long authorization URL; if the terminal wraps it,
  clicking opens only the first fragment and Google answers
  `Error 400: invalid_request — Required parameter is missing: response_type`. Copy the complete URL
  manually. Likely the same phenomenon behind upstream issues
  [#150](https://github.com/googleworkspace/cli/issues/150) and
  [#695](https://github.com/googleworkspace/cli/issues/695).
- **Per-account quota-project IAM**: gws attaches the OAuth client's `project_id` as the quota
  project on every API call. Result: each authenticated account — test user or not — must hold
  `roles/serviceusage.serviceUsageConsumer` on that project, or every call fails with
  `403 … Caller does not have required permission to use project …`. Grant the role in
  Console → IAM to every email that will log in. Setting `GOOGLE_WORKSPACE_PROJECT_ID=""` does NOT
  disable the header (empty is treated as unset).
- **Domain Restricted Sharing vs multi-domain setups**: if the OAuth client's project belongs to a
  Workspace organization enforcing `constraints/iam.allowedPolicyMemberDomains`, IAM grants to
  principals outside that domain (consumer gmail.com included) are rejected outright. Consequence
  for the quota-project requirement above: **host the OAuth client in a no-org project created by a
  consumer @gmail.com account** when the accounts span several domains — no org, no policy, and
  cross-domain Service Usage Consumer grants just work. Alternative (org admins only): override the
  policy to "Allow all" at project level. Switching projects means a new client ⇒ every account must
  re-run `gws auth login`.

## Gmail sending & send-as aliases

- `gws gmail +send` supports `--from <alias>` for **send-as aliases** (plus `--cc/--bcc`, `--html`,
  `-a/--attach` up to 25MB total, `--draft`, `--dry-run`).
- The alias must already exist in Gmail (Settings → Accounts → "Send mail as"); verify with:
  `gws gmail users settings sendAs list --params '{"userId":"me"}'`.

## Login scopes: what `-s` really does (verified in source + live)

- `gws auth login` has three scope modes ([`auth_commands.rs`](https://github.com/googleworkspace/cli/blob/main/crates/google-workspace-cli/src/auth_commands.rs)):
  a MINIMAL/default preset (`drive`, `spreadsheets`, `gmail.modify`, `calendar`, `documents`,
  `presentations`, `tasks`), `--readonly`, `--full` (adds `pubsub` + `cloud-platform`), and
  `--scopes` (custom, comma-separated full URLs).
- **`-s/--services` only FILTERS the preset** — it never adds scopes. Notably the preset carries
  `gmail.modify` but NOT `gmail.settings.basic`, so Gmail settings writes (filters/rules, vacation)
  fail with `403 Request had insufficient authentication scopes` even though sending works.
  (Curiously, `settings filters list` succeeded on a `gmail.modify`-only token; `filters create`
  did not.)
- **`--scopes` REPLACES the whole set** — list every scope you need, e.g. the skill's recommended
  set: `gmail.modify`, `gmail.settings.basic`, `drive`, `documents`, `spreadsheets`, `calendar`.

## Machine-local layout (generic)

Real profile → email mappings are machine-local and deliberately NOT in this repo: they live in a
gitignored `profiles.local.csv` next to `SKILL.md` (columns `profile,email,send_as`), written by
`scripts/bootstrap.sh` — interactively, or from `name=email[:send_as]` arguments.

| Profile | Account | Notes |
|---|---|---|
| `personal` | you@gmail.com | consumer account |
| `work` | you@corp.com | sends as alias **alias@corp.com** |

- Root: `~/.config/gws-accounts/` (chmod 700), one subdir per profile.
- Shared OAuth client: `~/.config/gws-accounts/client_secret.json`, relative-symlinked into each
  profile so one downloaded JSON serves all accounts and the tree survives rsync/tar.
- Wrapper: `~/.local/bin/gwsa` (also canonical in this repo) — sets `GOOGLE_WORKSPACE_CLI_CONFIG_DIR`
  and defaults `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file`.
