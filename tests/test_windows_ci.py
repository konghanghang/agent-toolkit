from __future__ import annotations

import re
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
        cls.step_names = [step["name"] for step in cls.job["steps"]]
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

    def test_workflow_forces_python_utf8(self) -> None:
        self.assertEqual("1", self.job["env"]["PYTHONUTF8"])

    def test_workflow_runs_repository_and_all_skill_tests(self) -> None:
        repository_script = self.steps["Run repository tests"]["run"]
        self.assertIn("unittest discover -s tests", repository_script)

        skill_script = self.steps["Run all Skill tests"]["run"]
        self.assertIn("Get-ChildItem -Path skills -Directory", skill_script)
        self.assertIn("unittest discover -s $testDirectory", skill_script)
        self.assertIn("$LASTEXITCODE", skill_script)

    def test_workflow_validates_every_skill(self) -> None:
        script = self.steps["Validate all Skills"]["run"]
        self.assertIn("Get-ChildItem -Path skills -Directory", script)
        self.assertIn("python scripts/quick_validate.py $skillDirectory.FullName", script)
        self.assertIn("$LASTEXITCODE", script)

    def test_workflow_materializes_locked_external_skill_before_consumers(self) -> None:
        script = self.steps["Materialize locked external Skills"]["run"]
        self.assertIn(
            "python scripts/update_ui_ux_pro_max.py --from-lock",
            script,
        )
        self.assertNotIn("--keep-backups", script)
        self.assertNotRegex(script, r"--version\s+\d")

        setup_node = self.steps["Set up Node.js"]
        self.assertEqual("actions/setup-node@v4", setup_node["uses"])
        self.assertEqual("22", setup_node["with"]["node-version"])
        materialize_index = self.step_names.index("Materialize locked external Skills")
        self.assertLess(
            self.step_names.index("Set up Node.js"),
            materialize_index,
        )
        for consumer in (
            "Validate all Skills",
            "Run repository tests",
            "Run all Skill tests",
            "Run direct Skill CLI smoke tests",
        ):
            with self.subTest(consumer=consumer):
                self.assertLess(materialize_index, self.step_names.index(consumer))

    def test_workflow_smoke_tests_every_direct_skill_cli(self) -> None:
        script = self.steps["Run direct Skill CLI smoke tests"]["run"]
        direct_clis = {
            path.relative_to(ROOT).as_posix().replace("/", "\\")
            for path in (ROOT / "skills").glob("*/scripts/*.py")
            if re.search(
                r"if\s+__name__\s*==\s*['\"]__main__['\"]",
                path.read_text(encoding="utf-8"),
            )
        }
        self.assertTrue(direct_clis)
        for cli_path in direct_clis:
            with self.subTest(cli_path=cli_path):
                self.assertIn(f'"{cli_path}"', script)
        self.assertIn("Test-Path -LiteralPath $scriptPath -PathType Leaf", script)
        self.assertIn("python $scriptPath --help", script)
        self.assertIn("$LASTEXITCODE", script)

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
