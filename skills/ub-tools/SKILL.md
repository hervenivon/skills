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

**Make the repository measurable once, instead of wrapping every run.** Check these two before trusting a score:

1. **Coverage in a pnpm (or yarn) project.** When `@vitest/coverage-v8` is missing, crapper installs it with npm. That fails in a pnpm tree, and crapper then only warns and scores every function at 0%. Fix: add `@vitest/coverage-v8` as a devDependency at Vitest's exact version, plus a `coverage` script that writes LCOV (`vitest run --coverage.enabled --coverage.provider=v8 --coverage.reporter=lcov --coverage.reportsDirectory=coverage`, scoped to the unit tests). crapper runs a `coverage` script as-is and skips its install when the package is present. Reference: scenario-labs/scenario-mcp#622. Upstream: unclebob/crapper#25.
2. **mutator and a path alias.** mutator tests each mutant from an overlay of symlinks. An alias resolved from the config file's real path (`'@': import.meta.dirname` in Vitest) loads the original file, so every mutant survives. **0 killed means this, not weak tests.** Fix: resolve the alias from the working directory (`'@': process.cwd()`), which is Vitest's default root anyway. Where the config cannot change, `scripts/mutator-in-place.sh` is the fallback (see below). Upstream: unclebob/mutator#19.

```bash
crapper --changed                 # CRAP per function in git additions and edits (runs coverage)
crapper --no-coverage <path>      # complexity only, no test run
dryer <path>                      # duplicate candidates, similarity >= 0.8 to review
mutator --scan <file>             # list mutation sites without running tests
mutator <file>                    # run the mutants for one file; start small
```

- CRAP is never below cyclomatic complexity, so a function at CC 10 fails a "CRAP < 10" rule even at full coverage: split it.
- Fallback for an alias you cannot change: run each mutant in the checkout, one at a time. `scripts/mutator-in-place.sh` copies the mutant over the checkout, runs the command from the project root, and restores the file:

  ```bash
  mutator --use-existing-coverage --max-workers 1 \
    --test-command "$HOME/.claude/skills/ub-tools/scripts/mutator-in-place.sh $PWD ./node_modules/.bin/vitest run <test file>" <file>
  ```
- mutator rewrites files while it runs: use it in a worktree another agent does not share.
- In an npm project, the first coverage run installs `@vitest/coverage-v8` into `node_modules` without editing `package.json`.
