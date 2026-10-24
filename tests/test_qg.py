"""Tests for bin/qg. Run: python3 -m unittest discover -s tests -v

They use fake build/test commands so they run in seconds without a Swift toolchain;
the real end-to-end run on a SwiftPM package is in README ("Dogfood").
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

QG_PATH = Path(__file__).resolve().parent.parent / "bin" / "qg"
loader = importlib.machinery.SourceFileLoader("qg", str(QG_PATH))
spec = importlib.util.spec_from_loader("qg", loader)
qg = importlib.util.module_from_spec(spec)
loader.exec_module(qg)

CALC = """import Foundation

struct Calc {
    func add(_ a: Int, _ b: Int) -> Int { a + b }
}
"""

CALC_NEW = """import Foundation

struct Calc {
    func add(_ a: Int, _ b: Int) -> Int { a + b }
    func isAdult(_ age: Int) -> Bool { age >= 18 && age != 99 }
}
"""


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class Repo:
    """A throwaway git repo with one source and one test file committed."""

    def __init__(self, config=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "Sources").mkdir()
        (self.root / "Tests").mkdir()
        self.write("Sources/Calc.swift", CALC)
        self.write("Tests/CalcTests.swift", "// add tests\n")
        if config:
            self.write(".quality-gate.json", json.dumps(config))
        sh(self.root, "git", "init", "-q")
        sh(self.root, "git", "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
        sh(self.root, "git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    def write(self, rel, text):
        (self.root / rel).write_text(text)

    def read(self, rel):
        return (self.root / rel).read_text()

    def cfg(self):
        return qg.load_config(self.root)

    def state(self):
        return qg.load_state(self.root)

    def cleanup(self):
        self.tmp.cleanup()


@contextlib.contextmanager
def chdir(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def call(fn, *args):
    """Run a qg command/hook, capturing stdout and the SystemExit message/code."""
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out):
        try:
            fn(*args)
        except SystemExit as e:
            code = e.code
    return out.getvalue(), code


def hook_json(text):
    return json.loads(text) if text.strip() else None


class MutantGeneration(unittest.TestCase):
    def ops(self, line):
        return [name for name, _ in qg.line_mutants(line)]

    def test_mutates_comparisons_and_logic(self):
        ops = self.ops("    if a == b && c < d { return true }")
        self.assertEqual(sorted(ops), sorted(["== → !=", "&& → ||", "< → <=", "true → false"]))

    def test_applies_one_mutation_per_mutant(self):
        mutated = dict(qg.line_mutants("x = a + b"))
        self.assertEqual(mutated["+ → -"], "x = a - b")

    def test_ignores_strings_and_comments(self):
        self.assertEqual(self.ops('let s = "a == b && true" // x < y'), [])

    def test_ignores_generics_force_unwrap_and_identity(self):
        self.assertEqual(self.ops("let xs: Array<Int> = y!"), [])
        self.assertEqual(self.ops("if a === b {}"), [])

    def test_not_equal_is_not_also_a_negation(self):
        self.assertEqual(self.ops("if a != b {}"), ["!= → =="])

    def test_negation_is_dropped(self):
        self.assertEqual(dict(qg.line_mutants("if !isEmpty {}"))["drop !"], "if isEmpty {}")

    def test_skips_imports_attributes_and_comments(self):
        for line in ("import Foundation", "@MainActor", "// a == b", "#if DEBUG"):
            self.assertEqual(self.ops(line), [], line)


class ChangedLines(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_modified_file_reports_only_new_lines(self):
        base = qg.git(self.repo.root, "rev-parse", "HEAD").strip()
        self.repo.write("Sources/Calc.swift", CALC_NEW)
        self.assertEqual(qg.changed_lines(self.repo.root, base, "Sources/Calc.swift"), {5})

    def test_new_file_reports_every_line(self):
        base = qg.git(self.repo.root, "rev-parse", "HEAD").strip()
        self.repo.write("Sources/New.swift", "a\nb\nc\n")
        self.assertEqual(qg.changed_lines(self.repo.root, base, "Sources/New.swift"), {1, 2, 3})

    def test_classifies_tests_and_sources(self):
        cfg = self.repo.cfg()
        self.assertTrue(qg.is_test("Tests/DesignSystemTests/FooTests.swift", cfg))
        self.assertTrue(qg.is_test("App/Feature/LoginTests.swift", cfg))
        self.assertFalse(qg.is_source("Tests/DesignSystemTests/FooTests.swift", cfg))
        self.assertTrue(qg.is_source("Sources/DesignSystem/Components/DSButton.swift", cfg))
        self.assertFalse(qg.is_source(".build/checkouts/x.swift", cfg))
        self.assertFalse(qg.is_source("Package.swift", cfg))


class Lock(unittest.TestCase):
    def tearDown(self):
        self.repo.cleanup()

    def start(self, test_cmd):
        self.repo = Repo({"build_cmd": "true", "test_cmd": test_cmd})
        with chdir(self.repo.root):
            call(qg.cmd_start, ["feature"])

    def test_refuses_without_new_tests(self):
        self.start("false")
        with chdir(self.repo.root):
            out, code = call(qg.cmd_lock, [])
        self.assertIn("no test files changed", str(code))
        self.assertEqual(self.repo.state()["phase"], "red")

    def test_refuses_when_tests_already_pass(self):
        self.start("true")
        self.repo.write("Tests/CalcTests.swift", "// isAdult tests\n")
        with chdir(self.repo.root):
            out, code = call(qg.cmd_lock, [])
        self.assertIn("already PASS", str(code))
        self.assertEqual(self.repo.state()["phase"], "red")

    def test_locks_failing_tests(self):
        self.start("false")
        self.repo.write("Tests/CalcTests.swift", "// isAdult tests\n")
        with chdir(self.repo.root):
            out, code = call(qg.cmd_lock, [])
        state = self.repo.state()
        self.assertEqual(code, 0)
        self.assertEqual(state["phase"], "green")
        self.assertEqual(state["red_kind"], "assertion")
        self.assertIn("Tests/CalcTests.swift", state["locked_tests"])

    def test_compile_failure_counts_as_red(self):
        self.repo = Repo({"build_cmd": "false", "test_cmd": "true"})
        with chdir(self.repo.root):
            call(qg.cmd_start, ["feature"])
            self.repo.write("Tests/CalcTests.swift", "// isAdult tests\n")
            call(qg.cmd_lock, [])
        self.assertEqual(self.repo.state()["red_kind"], "compile")

    def test_cannot_start_twice(self):
        self.start("false")
        with chdir(self.repo.root):
            out, code = call(qg.cmd_start, ["other"])
        self.assertIn("already active", str(code))


class PreToolUseHook(unittest.TestCase):
    def setUp(self):
        self.repo = Repo({"build_cmd": "true", "test_cmd": "false"})
        with chdir(self.repo.root):
            call(qg.cmd_start, ["feature"])

    def tearDown(self):
        self.repo.cleanup()

    def lock(self):
        self.repo.write("Tests/CalcTests.swift", "// isAdult tests\n")
        with chdir(self.repo.root):
            call(qg.cmd_lock, [])

    def decide(self, tool, **tool_input):
        out, _ = call(qg.pre_tool_use, {"cwd": str(self.repo.root), "tool_name": tool, "tool_input": tool_input})
        data = hook_json(out)
        return data["hookSpecificOutput"]["permissionDecision"] if data else "allow"

    def test_tests_are_editable_while_red(self):
        self.assertEqual(self.decide("Edit", file_path=str(self.repo.root / "Tests/CalcTests.swift")), "allow")

    def test_tests_are_locked_while_green(self):
        self.lock()
        self.assertEqual(self.decide("Edit", file_path=str(self.repo.root / "Tests/CalcTests.swift")), "deny")
        self.assertEqual(self.decide("Write", file_path="Tests/NewTests.swift"), "deny")
        self.assertEqual(self.decide("Edit", file_path=str(self.repo.root / "Sources/Calc.swift")), "allow")

    def test_files_outside_the_repo_are_ignored(self):
        self.lock()
        self.assertEqual(self.decide("Write", file_path="/tmp/elsewhere/Tests/x.swift"), "allow")

    def test_state_and_config_are_protected(self):
        self.assertEqual(self.decide("Write", file_path=str(self.repo.root / ".quality-gate.json")), "deny")
        self.assertEqual(self.decide("Edit", file_path=str(self.repo.root / ".quality-gate/state.json")), "deny")
        self.assertEqual(self.decide("Bash", command="echo {} > .quality-gate/state.json"), "deny")

    def test_human_only_commands(self):
        self.assertEqual(self.decide("Bash", command="/x/bin/qg unlock"), "deny")
        self.assertEqual(self.decide("Bash", command="python3 qg reset"), "deny")
        self.assertEqual(self.decide("Bash", command="/x/bin/qg check"), "allow")

    def test_shell_writes_to_tests_are_blocked_while_green(self):
        self.lock()
        self.assertEqual(self.decide("Bash", command="sed -i '' 's/a/b/' Tests/CalcTests.swift"), "deny")
        self.assertEqual(self.decide("Bash", command="echo x >> Tests/CalcTests.swift"), "deny")
        self.assertEqual(self.decide("Bash", command="git checkout HEAD -- Tests/CalcTests.swift"), "deny")
        self.assertEqual(self.decide("Bash", command="git stash"), "deny")
        self.assertEqual(self.decide("Bash", command="cat Tests/CalcTests.swift"), "allow")
        self.assertEqual(self.decide("Bash", command="swift test --filter CalcTests"), "allow")


class Gate(unittest.TestCase):
    """test_cmd passes only on the exact implemented source, so every mutant is killed."""

    def make(self, test_cmd, threshold=0.8):
        self.repo = Repo({"build_cmd": "true", "test_cmd": test_cmd, "mutation_threshold": threshold})
        with chdir(self.repo.root):
            call(qg.cmd_start, ["isAdult"])
        self.repo.write("Tests/CalcTests.swift", "// isAdult tests\n")
        self.repo.write("expected.txt", CALC_NEW)
        with chdir(self.repo.root):
            call(qg.cmd_lock, [])
        self.repo.write("Sources/Calc.swift", CALC_NEW)

    def tearDown(self):
        self.repo.cleanup()

    def run_gate(self):
        return qg.gate(self.repo.root, self.repo.cfg(), self.repo.state())

    def test_strong_tests_kill_all_mutants_and_pass(self):
        self.make("cmp -s Sources/Calc.swift expected.txt")
        ok, report, details = self.run_gate()
        self.assertTrue(ok, report)
        # line 5 has `>=`, `&&`, `!=` → 3 mutants; line 4 is unchanged and must not be mutated
        self.assertEqual(details["mutation"]["killed"], 3)
        self.assertEqual(self.repo.read("Sources/Calc.swift"), CALC_NEW)

    def test_weak_tests_let_mutants_survive_and_fail(self):
        # Passes on the real code and on every mutant: a test that pins nothing down.
        self.make("grep -q isAdult Sources/Calc.swift")
        ok, report, details = self.run_gate()
        self.assertFalse(ok)
        self.assertEqual(len(details["mutation"]["survived"]), 3)
        self.assertIn("Sources/Calc.swift:5", report)
        self.assertEqual(self.repo.read("Sources/Calc.swift"), CALC_NEW)

    def test_tampering_with_locked_tests_fails_before_running_anything(self):
        self.make("true")
        self.repo.write("Tests/CalcTests.swift", "// weakened\n")
        ok, report, _ = self.run_gate()
        self.assertFalse(ok)
        self.assertIn("Tests/CalcTests.swift", report)

    def test_adding_a_test_file_while_green_counts_as_tampering(self):
        self.make("true")
        self.repo.write("Tests/ExtraTests.swift", "// sneaky\n")
        ok, report, _ = self.run_gate()
        self.assertFalse(ok)
        self.assertIn("Tests/ExtraTests.swift", report)

    def test_failing_tests_fail_the_gate(self):
        self.make("false")
        ok, report, _ = self.run_gate()
        self.assertFalse(ok)
        self.assertIn("Tests fail", report)

    def test_interrupted_mutation_is_restored(self):
        self.make("true")
        backup = self.repo.root / ".quality-gate" / "mutation-backup.json"
        backup.write_text(json.dumps({"Sources/Calc.swift": CALC_NEW}))
        self.repo.write("Sources/Calc.swift", CALC_NEW.replace(">=", "<"))
        with chdir(self.repo.root):
            call(qg.cmd_status, [])
        self.assertEqual(self.repo.read("Sources/Calc.swift"), CALC_NEW)
        self.assertFalse(backup.exists())


class StopHook(unittest.TestCase):
    def tearDown(self):
        self.repo.cleanup()

    def stop(self, active=False):
        out, _ = call(qg.stop, {"cwd": str(self.repo.root), "stop_hook_active": active})
        return hook_json(out)

    def green(self, test_cmd):
        self.repo = Repo({"build_cmd": "true", "test_cmd": test_cmd, "max_stop_blocks": 2})
        with chdir(self.repo.root):
            call(qg.cmd_start, ["isAdult"])
        self.repo.write("Tests/CalcTests.swift", "// isAdult tests\n")
        self.repo.write("expected.txt", CALC_NEW)
        with chdir(self.repo.root):
            call(qg.cmd_lock, [])

    def test_no_gate_means_no_opinion(self):
        self.repo = Repo()
        self.assertIsNone(self.stop())

    def test_blocks_until_implemented_then_passes(self):
        self.green("cmp -s Sources/Calc.swift expected.txt")
        self.assertEqual(self.stop()["decision"], "block")
        self.repo.write("Sources/Calc.swift", CALC_NEW)
        result = self.stop(active=True)
        self.assertNotIn("decision", result)
        self.assertIn("passed", result["systemMessage"])
        self.assertEqual(self.repo.state()["phase"], "passed")
        self.assertIsNone(self.stop())

    def test_gives_up_after_max_blocks_and_tells_the_human(self):
        self.green("false")
        self.assertEqual(self.stop()["decision"], "block")
        self.assertEqual(self.stop(active=True)["decision"], "block")
        result = self.stop(active=True)
        self.assertNotIn("decision", result)
        self.assertIn("Needs a human", result["systemMessage"])

    def test_escalation_lets_the_agent_stop_but_keeps_tests_locked(self):
        self.green("false")
        with chdir(self.repo.root):
            out, code = call(qg.cmd_escalate, ["testX", "expects", "20"])
        self.assertEqual(code, 0)
        result = self.stop()
        self.assertNotIn("decision", result)
        self.assertIn("testX expects 20", result["systemMessage"])
        out, _ = call(qg.pre_tool_use, {"cwd": str(self.repo.root), "tool_name": "Edit",
                                        "tool_input": {"file_path": str(self.repo.root / "Tests/CalcTests.swift")}})
        self.assertEqual(hook_json(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        out, _ = call(qg.pre_tool_use, {"cwd": str(self.repo.root), "tool_name": "Bash",
                                        "tool_input": {"command": "qg resume"}})
        self.assertEqual(hook_json(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_escalate_requires_a_reason(self):
        self.green("false")
        with chdir(self.repo.root):
            out, code = call(qg.cmd_escalate, [])
        self.assertIn("say why", str(code))
        self.assertEqual(self.repo.state()["phase"], "green")

    def test_human_can_resume_or_unlock_after_escalation(self):
        self.green("false")
        with chdir(self.repo.root):
            call(qg.cmd_escalate, ["why"])
            call(qg.cmd_resume, [])
            self.assertEqual(self.repo.state()["phase"], "green")
            call(qg.cmd_escalate, ["why"])
            call(qg.cmd_unlock, [])
        self.assertEqual(self.repo.state()["phase"], "red")

    def test_red_phase_nudges_once(self):
        self.repo = Repo({"build_cmd": "true", "test_cmd": "false"})
        with chdir(self.repo.root):
            call(qg.cmd_start, ["isAdult"])
        self.assertEqual(self.stop()["decision"], "block")
        self.assertIsNone(self.stop(active=True))


if __name__ == "__main__":
    unittest.main()
