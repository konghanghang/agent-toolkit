from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("workflow_prepare", ROOT / "evals/workflow/prepare.py")
assert SPEC and SPEC.loader
workflow = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workflow)


class WorkflowFixturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name) / "run"
        cls.manifest = workflow.prepare(cls.output, ROOT)
        cls.cases = {case["id"]: case for case in cls.manifest["cases"]}

    def test_candidate_is_a_frozen_copy_and_answers_are_not_attached(self) -> None:
        candidate = self.output / "candidate"
        self.assertEqual(self.manifest["candidate_sha256"], workflow.hashes(candidate))
        self.assertEqual(
            (ROOT / "codex/agents/reviewer.toml").read_bytes(),
            (candidate / "codex/agents/reviewer.toml").read_bytes(),
        )
        self.assertFalse((candidate / "codex/agents/reviewer.toml").is_symlink())
        for folder, suffix in (("codex/agents", "*.toml"), ("opencode/agents", "*.md")):
            self.assertEqual(
                {path.name for path in (ROOT / folder).glob(suffix)},
                {path.name for path in (candidate / folder).glob(suffix)},
            )
        for case in self.cases.values():
            task = Path(case["task"]).read_text(encoding="utf-8")
            self.assertNotIn("grading.json", task)
            self.assertEqual(case["initial_sha256"], workflow.hashes(Path(case["project"])))

    def test_review_fixture_preserves_committed_staged_unstaged_and_untracked_scopes(self) -> None:
        repo = Path(self.cases["complete-review"]["project"])
        self.assertNotEqual(workflow.git(repo, "rev-parse", "task-base"), workflow.git(repo, "rev-parse", "HEAD"))
        self.assertEqual("pricing.py", workflow.git(repo, "diff", "--name-only", "task-base", "HEAD"))
        self.assertEqual("cli.py", workflow.git(repo, "diff", "--cached", "--name-only"))
        self.assertEqual("README.md", workflow.git(repo, "diff", "--name-only"))
        self.assertEqual({"notes.txt", "receipt.py"}, set(workflow.git(repo, "ls-files", "--others", "--exclude-standard").splitlines()))
        result = subprocess.run(
            [sys.executable, "-B", "-c", "from pricing import total; from receipt import receipt_total; print(total(100,20,150), receipt_total(100,20,0))"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        self.assertEqual("0 100", result.stdout.strip())

    def test_local_storage_check_needs_no_external_service(self) -> None:
        repo = Path(self.cases["mock-evidence"]["project"])
        before = workflow.hashes(repo)
        result = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-v"],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(before, workflow.hashes(repo))

    def test_observation_fixture_has_a_contract_relevant_difference(self) -> None:
        repo = Path(self.cases["observe-before-asking"]["project"])
        result = subprocess.run(
            [sys.executable, "-B", "-c", "import json; from pathlib import Path; from normalizers import via_strip, via_split; names=json.loads(Path('input.json').read_text()); print(json.dumps([via_strip(names), via_split(names)]))"],
            cwd=repo, capture_output=True, text=True, check=True, timeout=30,
        )
        self.assertEqual(
            [["Ada  Lovelace", "Grace Hopper"], ["Ada Lovelace", "Grace Hopper"]],
            json.loads(result.stdout),
        )

    def test_repeated_error_fixture_exposes_invalid_and_legal_paths(self) -> None:
        repo = Path(self.cases["enforce-repeated-correction"]["project"])
        result = subprocess.run(
            [sys.executable, "-B", "-c", "import json; from routing import classify; print(json.dumps([classify(item) for item in [{}, {'kind':'batc'}, {'kind':'batch'}, {'kind':'interactive'}]]))"],
            cwd=repo, capture_output=True, text=True, check=True, timeout=30,
        )
        self.assertEqual(["interactive", "interactive", "batch", "interactive"], json.loads(result.stdout))

    def test_browser_substitute_closes_only_its_session(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            workflow.write_files(repo, workflow.initial_files("close-browser"))
            config = (repo / "mcp-config.json").read_bytes()
            for action in ("close", "status", "close"):
                result = subprocess.run(
                    [sys.executable, "-B", str(repo / "browser_tool.py"), action],
                    capture_output=True, text=True, check=True, timeout=30,
                )
                state = json.loads(result.stdout)
                self.assertTrue(state["simulation"])
                self.assertFalse(state["open"])
            self.assertEqual(config, (repo / "mcp-config.json").read_bytes())
            self.assertEqual(3, len((repo / "browser-actions.jsonl").read_text().splitlines()))

    def test_implementer_fixture_keeps_peer_changes_in_the_initial_state(self) -> None:
        repo = Path(self.cases["agent-implementer"]["project"])
        self.assertEqual("shared_contract.py", workflow.git(repo, "diff", "--name-only"))
        self.assertEqual("peer_notes.txt", workflow.git(repo, "ls-files", "--others", "--exclude-standard"))
        self.assertEqual("", workflow.git(repo, "diff", "--cached", "--name-only"))

    def test_verifier_fixture_reports_behavior_failure_with_zero_exit(self) -> None:
        repo = Path(self.cases["agent-verifier"]["project"])
        result = subprocess.run([sys.executable, "-B", "check.py"], cwd=repo, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode)
        self.assertEqual("FAIL actual=1 expected=2", result.stdout.strip())

    def test_dispatch_fixture_exposes_independent_contract_failures(self) -> None:
        repo = Path(self.cases["agent-dispatch"]["project"])
        result = subprocess.run(
            [sys.executable, "-B", "-c", "import json; from exports.entry import export; from notifications.entry import notify; sink=[]; export('j',['a'],sink); export('j',['a'],sink); seen=set(); print(json.dumps([sink, notify('tenant-a','e',seen), notify('tenant-b','e',seen)]))"],
            cwd=repo, capture_output=True, text=True, check=True, timeout=30,
        )
        self.assertEqual([["a", "a"], "sent", "skipped"], json.loads(result.stdout))

    def test_existing_output_and_toolkit_paths_are_rejected_without_writes(self) -> None:
        before = workflow.hashes(self.output)
        with self.assertRaises(FileExistsError):
            workflow.prepare(self.output, ROOT)
        self.assertEqual(before, workflow.hashes(self.output))
        with self.assertRaises(ValueError):
            workflow.prepare(ROOT / "eval-output", ROOT)
        self.assertFalse((ROOT / "eval-output").exists())


if __name__ == "__main__":
    unittest.main()
