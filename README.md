# skills

Agent skills by [@hervenivon](https://github.com/hervenivon) — reusable, tested know-how for
AI coding agents (Claude Code and any harness that reads `SKILL.md` files).

## Skills

| Skill | Use when |
|---|---|
| [`gws-multi-account`](skills/gws-multi-account/SKILL.md) | Operating several Google accounts (Gmail, Drive, Docs, Sheets, Calendar) from the [Google Workspace CLI](https://github.com/googleworkspace/cli), and keeping that setup portable across machines |

## Install

With the [skills CLI](https://github.com/vercel-labs/skills):

```bash
npx skills add https://github.com/hervenivon/skills
```

Or manually — for `gws-multi-account`, the bootstrap script does everything (installs the CLI and
wrapper, asks for your profile names + emails, writes a gitignored `profiles.local.csv`, and links
the skill into `~/.claude/skills` and `~/.agents/skills`):

```bash
git clone https://github.com/hervenivon/skills
skills/skills/gws-multi-account/scripts/bootstrap.sh
```

## Research notes

Longer-form findings that back the skills live in [`docs/`](docs/):

- [`gws-cli-findings.md`](docs/gws-cli-findings.md) — Google Workspace CLI multi-account research log
