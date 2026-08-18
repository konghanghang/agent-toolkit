from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FILE = ROOT / ".github" / "workflows" / "windows.yml"


class WindowsWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = yaml.load(
            WORKFLOW_FILE.read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        cls.job = cls.document["jobs"]["windows"]
        cls.steps = {
            step["name"]: step
            for step in cls.job["steps"]
        }

    def test_workflow_runs_on_real_windows_runner(self) -> None:
        self.assertEqual("Windows", self.document["name"])
        self.assertEqual("windows-latest", self.job["runs-on"])
        self.assertEqual("15", self.job["timeout-minutes"])
        self.assertEqual("read", self.document["permissions"]["contents"])
        self.assertIn("pull_request", self.document["on"])
        self.assertIn("workflow_dispatch", self.document["on"])

    def test_workflow_runs_repository_and_all_skill_tests(self) -> None:
        repository_script = self.steps["Run repository tests"]["run"]
        self.assertIn("unittest discover -s tests", repository_script)

        skill_script = self.steps["Run all Skill tests"]["run"]
        self.assertIn("Get-ChildItem -Path skills -Directory", skill_script)
        self.assertIn("unittest discover -s $testDirectory", skill_script)
        self.assertIn("$LASTEXITCODE", skill_script)

    def test_workflow_verifies_real_installation_and_idempotency(self) -> None:
        script = self.steps[
            "Verify installer junctions, Rules, and idempotency"
        ]["run"]
        self.assertIn('LinkType -ne "Junction"', script)
        self.assertIn('".agents\\skills\\$skillName"', script)
        self.assertIn('".claude\\skills\\$skillName"', script)
        self.assertIn('Join-Path $codexHome "agents"', script)
        self.assertIn('Get-ChildItem -Path "codex\\rules"', script)
        self.assertEqual(3, script.count("python scripts/install.py --codex-home"))
        self.assertIn("--dry-run", script)

    def test_workflow_executes_every_windows_wrapper(self) -> None:
        script = self.steps["Verify Windows command wrappers"]["run"]
        self.assertIn("install_command_templates", script)
        self.assertIn("runtime.json", script)
        for command in ("sls", "archery", "jms", "testdb"):
            for suffix in ("cmd", "ps1"):
                with self.subTest(command=command, suffix=suffix):
                    self.assertIn(f'"bin\\{command}.{suffix}"', script)
        self.assertIn("& $Path --help", script)
        self.assertIn("$LASTEXITCODE", script)


if __name__ == "__main__":
    unittest.main()
