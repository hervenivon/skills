---
name: ub-tools
description: Use when a crapper, dryer or mutator command is needed (the ub-mode quality gates for CRAP score, duplication and mutation testing), when one of them is not found on PATH, or when installing or updating them on a machine.
---

# ub-tools

[crapper](https://github.com/unclebob/crapper), [dryer](https://github.com/unclebob/dryer) and [mutator](https://github.com/unclebob/mutator) are the quality-gate tools [ub-mode](https://github.com/unclebob/ub-mode) asks for. They support TypeScript, Java, Go, Rust, Python and Clojure, and run from a project root.

**If `crapper`, `dryer` or `mutator` is not found, run `scripts/bootstrap.sh` next to this file. Never report them missing, and never clone them into a project.**

## Install

`scripts/bootstrap.sh` needs `uv` and `git`. It installs each tool with `uv tool install` from the commit pinned in `tools.lock`: nothing is cloned, each tool gets its own environment, and mutator gets crapper inside its environment. A re-run with unchanged pins does nothing. It also adds `.metrics/` to the global git excludes, because every tool writes its report there.

`scripts/bootstrap.sh --update` moves the pins to each repo's latest commit, then installs. Commit `tools.lock` afterwards so other machines get the same versions.

## Use

```bash
crapper --changed                 # CRAP per function in git additions and edits (runs coverage)
crapper --no-coverage <path>      # complexity only, no test run
dryer <path>                      # duplicate candidates, similarity >= 0.8 to review
mutator --scan <file>             # list mutation sites without running tests
mutator <file>                    # run the mutants for one file; start small
```

- CRAP is never below cyclomatic complexity, so a function at CC 10 fails a "CRAP < 10" rule even at full coverage: split it.
- mutator rewrites source files while it runs: use it in a worktree another agent does not share.
- The first coverage run of a Vitest project installs `@vitest/coverage-v8` into `node_modules` without editing `package.json`.
