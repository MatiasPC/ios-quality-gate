---
name: spec-tdd
description: Use when implementing any new feature, behaviour change or bugfix in a Swift / iOS / SwiftPM repository. Spec-first TDD under the qg quality gate — failing tests are written and locked before production code, and a Stop hook blocks finishing until build, tests and mutation testing on the changed lines pass.
---

# Spec-first TDD under the qg gate

The SessionStart context gives the absolute path of the `qg` CLI. Call it by that path (written `qg` below).

Why this shape (sources in the plugin README):
- Giving a model the tests up front measurably improves the code it writes (Mathews & Nagappan, ASE '24).
- What drives TDD's benefit is **small, uniform cycles**, more than test-first ordering (Fucci et al., IEEE TSE 2017).
- Coverage says little about test strength; **surviving mutants** point at real gaps (Petrović et al., ICSE '21).

## 1. RED — specify behaviour that does not exist yet

1. `qg start "<one-line feature title>"`
2. Turn the request into a list of concrete behaviours. Each one becomes one small test with a name
   that reads as a spec (`testClampsProgressToOne`, `@Test("rejects empty email")`).
   Cover edge cases and boundaries explicitly: the mutation step will find any you skip.
3. Only touch production code to add the **minimal API surface** the tests need to compile
   (signatures that `fatalError()` or return a placeholder). No logic yet.
4. `qg lock`. It refuses if the tests already pass (they would not specify anything new).
   If it reports a *compile* red, read the output and make sure the failure is the missing behaviour, not a typo.

## 2. GREEN — implement against frozen tests

- Test files are now read-only: the PreToolUse hook denies edits and the gate hashes them.
  Do not try to work around it. If a locked test is genuinely wrong (it contradicts the spec or
  a standard), run `qg escalate "<test name>: <what is wrong and why>"`, explain it to the human
  and stop. Never bend production code to satisfy a wrong test.
- Write the simplest code that makes the tests pass. Follow the repo's conventions (Velvet UI tokens, etc.).
- Run the tests yourself as you go, then `qg check` before you finish.

## 3. The gate

`qg check` (and the Stop hook) runs: test-file integrity → build → tests → mutation testing of the
lines you changed since `qg start`. Threshold defaults to 80% killed.

A **surviving mutant** means a line whose behaviour no locked test pins down. Fix it by:
- simplifying or deleting code the spec does not require (most common), or
- if the behaviour is truly needed, `qg escalate` asking the human to unlock so a test can be added.

Never weaken code in ways that only exist to dodge a mutant. When the gate passes the Stop hook lets you finish; report the mutation score in your summary.
