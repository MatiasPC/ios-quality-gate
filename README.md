# ios-quality-gate

A Claude Code plugin that keeps coding agents honest on Swift / iOS projects.
The agent writes **failing tests first**, the tests get **locked**, it implements, and a
**Stop hook** won't let it finish until build, tests and **mutation testing on the lines it
changed** all pass.

```
qg start "feature"   RED    write tests for behaviour that does not exist yet
qg lock              ──►    proves they fail, hashes and freezes every test file
(implement)          GREEN  PreToolUse hook denies edits to tests; Stop hook runs the gate
qg check             gate   integrity → build → tests → mutation score ≥ threshold
```

## Why each piece exists

| Piece | Evidence |
|---|---|
| Tests before code, given to the model | Mathews & Nagappan, *Test-Driven Development and LLM-based Code Generation*, ASE 2024 — adding tests to the prompt consistently raised solve rates. [arXiv 2402.13521](https://arxiv.org/abs/2402.13521) |
| Small, uniform cycles (skill guidance) | Fucci et al., *A Dissection of the TDD Process*, IEEE TSE 2017 — quality/productivity gains tracked **granularity and uniformity**, not test-first order. [arXiv 1611.05994](https://arxiv.org/abs/1611.05994) |
| Mutation testing instead of coverage | Petrović, Ivanković, Fraser, Just, *Does mutation testing improve testing practices?*, ICSE 2021 — ~15M mutants at Google; mutants are coupled with real faults. [arXiv 2103.07189](https://arxiv.org/abs/2103.07189). Inozemtseva & Holmes, ICSE 2014 — coverage is not strongly correlated with suite effectiveness. |
| Locking tests | Agents under pressure to "make tests pass" can edit the tests instead. Anthropic's Claude Code best practices tell the agent not to modify tests during TDD; this plugin enforces it with a hook plus a hash check (the hash catches anything the hook's shell heuristics miss). |
| A gate at all | DORA 2024: every +25% AI adoption was associated with an estimated −7.2% delivery stability. [dora.dev](https://dora.dev/research/2024/dora-report/) |

## Install

```bash
claude --plugin-dir ~/Documents/ios-quality-gate      # one session
```

Or add it to a local marketplace to install permanently. Requires `python3` and `git`.

## Configure (optional) — `.quality-gate.json` at the repo root

Defaults target SwiftPM (`swift build --build-tests` / `swift test --skip-build`). For an Xcode app:

```json
{
  "build_cmd": "xcodebuild build-for-testing -scheme MyApp -destination 'platform=iOS Simulator,name=iPhone 17' -quiet",
  "test_cmd": "xcodebuild test-without-building -scheme MyApp -destination 'platform=iOS Simulator,name=iPhone 17' -quiet",
  "mutation_threshold": 0.8,
  "max_mutants": 40
}
```

Other keys: `test_globs`, `source_globs`, `ignore_globs` (fnmatch; `*` crosses `/`), `command_timeout`, `max_stop_blocks`.

## How the mutation step works

Only lines added/modified since `qg start` are mutated (`git diff -U0` + new files), one operator at a
time: `==/!=`, `< <= > >=`, `&&/||`, `true/false`, `+/-`, `+=/-=`, `min/max`, dropping `!`. String
literals and comments are masked; generics (`Array<Int>`) and force-unwraps are left alone. Each
mutant is built and tested; a build failure is "unviable" and excluded. The original file is
restored after every mutant, and a crash-safe backup is restored on the next `qg` invocation.

A **surviving mutant** is a line whose behaviour no locked test pins down: either over-built code
(delete/simplify it) or a missing spec (human runs `qg unlock`, agent adds a test).

## When a locked test is wrong

The agent runs `qg escalate "why"`: the Stop hook lets it finish and shows you the reason, while the
tests stay locked. You then run `qg unlock` (fix the tests, back to RED) or `qg resume` (continue).

## Human-only commands

`qg unlock` (back to RED), `qg resume` and `qg reset` — the hook refuses to let the agent run them, and the
gate's state/config files are write-protected while a gate is active.

## Limits (v0.1)

- Shell-write detection for tests is heuristic; the hash check at the gate is the real guarantee.
- Line-based mutator, not AST-based. Good enough for logic; it won't mutate SwiftUI layout.
- Mutation cost ≈ one incremental build + test run per mutant (capped by `max_mutants`).

## Develop

```bash
python3 -m unittest discover -s tests -v
```
