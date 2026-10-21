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

    def test_business_candidate_freezes_entry_and_local_dependencies(self) -> None:
        candidate = self.output / "candidate"
        for name in ("work-feature-delivery", "tapd-story", "git-commit", "grilling"):
            with self.subTest(skill=name):
                self.assertEqual(
                    {
                        path: digest for path, digest in workflow.hashes(ROOT / "skills" / name).items()
                        if "__pycache__" not in Path(path).parts and not path.endswith(".pyc")
                    },
                    workflow.hashes(candidate / "skills" / name),
                )
                self.assertFalse((candidate / "skills" / name).is_symlink())
        grading = json.loads((self.output / "grading.json").read_text(encoding="utf-8"))
        self.assertEqual(set(self.cases), {case["id"] for case in grading})
        self.assertTrue(workflow.BUSINESS_CASES.issubset(self.cases))

    def test_business_fixtures_start_with_only_the_intended_delivery_artifacts(self) -> None:
        expected = {
            "work-small-fix": ({"999001"}, set()),
            "work-resume-materials": ({"999001", "100021"}, {"20261029.md"}),
            "work-pending-batch": ({"999001", "100022"}, {"20261022.md"}),
            "work-readonly-analysis": ({"999001"}, set()),
            "work-missing-requirement-id": ({"999001"}, set()),
        }
        for case_id, (requirements, releases) in expected.items():
            with self.subTest(case=case_id):
                repo = Path(self.cases[case_id]["project"])
                knowledge = repo / "knowledge"
                self.assertEqual(requirements, {path.parent.name for path in (knowledge / "delivery/requirements").glob("*/index.md")})
                self.assertEqual(releases, {path.name for path in (knowledge / "delivery/releases").glob("*.md")})
                self.assertTrue((knowledge / "templates/requirement.md").is_file())
                self.assertTrue((knowledge / "templates/release.md").is_file())
                for path in repo.rglob("*"):
                    self.assertFalse(path.is_symlink(), str(path))
                self.assertEqual("", self.cases[case_id]["initial_status"])
        resume = Path(self.cases["work-resume-materials"]["project"])
        self.assertEqual(workflow.EXISTING_RELEASE, (resume / "knowledge/delivery/releases/20261029.md").read_text(encoding="utf-8"))
        self.assertEqual("archive.retention-days=14\n", (resume / "config/archive.properties").read_text(encoding="utf-8"))

    def test_business_records_have_the_dashboard_metadata_schema(self) -> None:
        for case_id in workflow.BUSINESS_CASES:
            repo = Path(self.cases[case_id]["project"])
            for record in (repo / "knowledge/delivery/requirements").glob("*/index.md"):
                with self.subTest(case=case_id, requirement=record.parent.name):
                    frontmatter, body = record.read_text(encoding="utf-8").removeprefix("---\n").split("\n---\n", 1)
                    metadata = {key: json.loads(value) for key, value in (line.split(": ", 1) for line in frontmatter.splitlines())}
                    self.assertEqual({"requirementId", "title", "phase", "owner", "updated", "blocker", "nextStep"}, set(metadata))
                    self.assertEqual(record.parent.name, metadata["requirementId"])
                    self.assertIn(metadata["phase"], {"待发布", "开发中", "待开发"})
                    self.assertEqual("2026-10-22" if case_id == "work-pending-batch" else "2026-10-23", metadata["updated"])
                    self.assertIn("<RequirementStatus />", body)

    def test_business_fixtures_have_confirmed_local_baselines_without_a_remote_transport(self) -> None:
        for case_id in workflow.BUSINESS_CASES:
            with self.subTest(case=case_id):
                repo = Path(self.cases[case_id]["project"])
                baseline = json.loads((repo / "BASELINE.json").read_text(encoding="utf-8"))
                self.assertEqual(baseline["sha"], workflow.git(repo, "rev-parse", baseline["ref"]))
                self.assertEqual(baseline["sha"], workflow.git(repo, "rev-parse", f"refs/remotes/origin/{baseline['ref']}"))
                self.assertEqual(baseline["sha"], workflow.git(repo, "merge-base", "HEAD", baseline["ref"]))
                self.assertEqual("", workflow.git(repo, "remote"))
                self.assertEqual("", workflow.git(repo, "for-each-ref", "--format=%(upstream)", "refs/heads"))
                baseline_cases = {
                    "work-readonly-analysis", "work-missing-requirement-id",
                    "work-topic-clarification", "work-discussion-confirmed", "work-create-only", "work-existing-review",
                }
                expected_branch = baseline["ref"] if case_id in baseline_cases else f"khh/{case_id}"
                self.assertEqual(expected_branch, workflow.git(repo, "branch", "--show-current"))

    def test_business_checks_reproduce_current_behavior_without_writing_artifacts(self) -> None:
        for case_id, expected_status in (
            ("work-small-fix", 1), ("work-pending-batch", 1),
            ("work-readonly-analysis", 1), ("work-resume-materials", 0),
            ("work-missing-requirement-id", 0),
        ):
            with self.subTest(case=case_id):
                repo = Path(self.cases[case_id]["project"])
                before = workflow.hashes(repo)
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
                    cwd=repo, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(expected_status, result.returncode, result.stdout + result.stderr)
                self.assertEqual(before, workflow.hashes(repo))
                self.assertEqual("", workflow.git(repo, "status", "--porcelain=v1"))
        repo = Path(self.cases["work-missing-requirement-id"]["project"])
        result = subprocess.run(
            [sys.executable, "-B", "-c", "from app import list_items; list_items([{'tenant_id': 'a'}], tenant_id='a')"],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("TypeError", result.stderr)
        self.assertEqual(self.cases["work-missing-requirement-id"]["initial_sha256"], workflow.hashes(repo))

    def run_tapd_stub(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", "tools/tapd_stub.py", *args],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )

    def write_tapd_spec(self, repo: Path, **story_fields) -> Path:
        spec = repo / "artifacts/story.json"
        workflow.write_files(repo, {"artifacts/story.json": json.dumps({
            "workspace_id": "100000",
            "story": {"name": "平台离线需求", "description_html": "<h3>需求说明</h3><p>保持已确认范围。</p>", "owner": "fixture-owner", **story_fields},
        }, ensure_ascii=False)})
        return spec

    def test_entry_fixtures_begin_without_created_stories_or_task_records(self) -> None:
        self.assertTrue(workflow.ENTRY_CASES.issubset(self.cases))
        for case_id in workflow.ENTRY_CASES:
            with self.subTest(case=case_id):
                repo = Path(self.cases[case_id]["project"])
                self.assertEqual({"next_id": "1110000000001000031", "stories": {}}, json.loads((repo / "tools/tapd-state.json").read_text()))
                self.assertEqual(b"", (repo / "tools/tapd-audit.jsonl").read_bytes())
                self.assertEqual({"999001"}, {path.parent.name for path in (repo / "knowledge/delivery/requirements").glob("*/index.md")})
                self.assertFalse((repo / "artifacts/story.json").exists())
                self.assertFalse((repo / "knowledge/delivery/releases").exists())
                if case_id == "work-existing-review":
                    self.assertEqual("app.py", workflow.git(repo, "diff", "--name-only"))
                else:
                    self.assertEqual("", self.cases[case_id]["initial_status"])

    def test_tapd_preview_create_and_id_recovery_change_only_local_state_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            workflow.write_files(repo, workflow.initial_files("work-create-only"))
            spec = self.write_tapd_spec(repo)
            before = workflow.hashes(repo)
            validate = self.run_tapd_stub(repo, "validate-spec", "--operation", "create", "--spec", str(spec))
            self.assertEqual(0, validate.returncode, validate.stderr)
            self.assertTrue(json.loads(validate.stdout)["simulation"])
            self.assertEqual(before, workflow.hashes(repo))
            preview = self.run_tapd_stub(repo, "create", "--spec", str(spec), "--dry-run")
            self.assertEqual(0, preview.returncode, preview.stderr)
            self.assertTrue(json.loads(preview.stdout)["simulation"])
            self.assertTrue(json.loads(preview.stdout)["dry_run"])
            self.assertEqual({}, json.loads((repo / "tools/tapd-state.json").read_text())["stories"])
            self.assertEqual({"tools/tapd-audit.jsonl"}, {path for path, digest in workflow.hashes(repo).items() if before[path] != digest})
            created = self.run_tapd_stub(repo, "create", "--spec", str(spec))
            self.assertEqual(0, created.returncode, created.stderr)
            response = json.loads(created.stdout)
            self.assertTrue(response["simulation"])
            self.assertFalse(response["dry_run"])
            self.assertEqual("1110000000001000031", response["id"])
            after = workflow.hashes(repo)
            self.assertEqual({"tools/tapd-state.json", "tools/tapd-audit.jsonl"}, {path for path, digest in after.items() if before[path] != digest})
            detail = self.run_tapd_stub(repo, "detail", "--id", response["id"])
            self.assertEqual(0, detail.returncode, detail.stderr)
            self.assertEqual(response["story"], json.loads(detail.stdout)["story"])
            self.assertTrue(json.loads(detail.stdout)["simulation"])
            self.assertEqual(after, workflow.hashes(repo))
            events = [json.loads(line) for line in (repo / "tools/tapd-audit.jsonl").read_text().splitlines()]
            self.assertEqual(["preview", "create"], [event["action"] for event in events])
            self.assertTrue(all(event["simulation"] for event in events))
            self.assertEqual(events[0]["spec_sha256"], events[1]["spec_sha256"])
            self.assertFalse((repo / f"knowledge/delivery/requirements/{response['id']}").exists())

    def test_tapd_simulations_are_separate_per_project(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder) / "first", Path(folder) / "second"
            for repo in (first, second):
                workflow.write_files(repo, workflow.initial_files("work-authorized-refactor"))
                self.write_tapd_spec(repo)
            second_before = workflow.hashes(second)
            created = self.run_tapd_stub(first, "create", "--spec", "artifacts/story.json")
            self.assertEqual(0, created.returncode, created.stderr)
            story_id = json.loads(created.stdout)["id"]
            self.assertEqual(second_before, workflow.hashes(second))
            missing = self.run_tapd_stub(second, "detail", "--id", story_id)
            self.assertNotEqual(0, missing.returncode)
            self.assertEqual(second_before, workflow.hashes(second))
            preview = self.run_tapd_stub(second, "preview", "--spec", "artifacts/story.json")
            self.assertEqual(0, preview.returncode, preview.stderr)
            self.assertTrue(json.loads(preview.stdout)["dry_run"])
            self.assertEqual({}, json.loads((second / "tools/tapd-state.json").read_text())["stories"])

    def test_tapd_rejects_outside_or_unconfirmed_specs_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder) / "project"
            workflow.write_files(repo, workflow.initial_files("work-create-only"))
            spec = self.write_tapd_spec(repo, owner="")
            outside = Path(folder) / "outside.json"
            outside.write_text(spec.read_text())
            before = workflow.hashes(repo)
            for path in (spec, outside):
                result = self.run_tapd_stub(repo, "create", "--spec", str(path))
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(before, workflow.hashes(repo))

    def test_entry_review_defect_and_refactor_baseline_are_observable_offline(self) -> None:
        review = Path(self.cases["work-existing-review"]["project"])
        before = workflow.hashes(review)
        result = subprocess.run(
            [sys.executable, "-B", "-c", "from app import total; print(total(100, 20, 150))"],
            cwd=review, capture_output=True, text=True, check=True, timeout=30,
        )
        self.assertEqual("0", result.stdout.strip())
        self.assertEqual(before, workflow.hashes(review))
        refactor = Path(self.cases["work-authorized-refactor"]["project"])
        before = workflow.hashes(refactor)
        result = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=refactor, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(before, workflow.hashes(refactor))

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
