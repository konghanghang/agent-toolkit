from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
WINDOWS_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "windows.yml"


class WindowsDocumentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readme = README_PATH.read_text(encoding="utf-8")
        cls.workflow = WINDOWS_WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.powershell_blocks = "\n".join(
            re.findall(r"```powershell\n(.*?)```", cls.readme, flags=re.DOTALL)
        )

    def test_prerequisites_match_real_windows_ci_and_installer(self) -> None:
        self.assertIn('python-version: "3.11"', self.workflow)
        for expected in (
            "Git for Windows",
            "CPython 3.11",
            "py -3.11 --version",
            "powershell.exe",
            "Windows PowerShell 5.1",
            "NTFS",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.readme)

    def test_runtime_and_optional_dependencies_are_distinguished(self) -> None:
        self.assertIn("`scripts/install.py` 只负责", self.readme)
        self.assertIn("`common/bootstrap_work.py`", self.readme)
        self.assertIn("`requirements.txt`", self.readme)
        for dependency in ("Google Chrome", "Hugo", "rsvg-convert"):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, self.readme)

    def test_windows_runtime_uses_python_311_and_project_venv(self) -> None:
        self.assertIn(
            'py -3.11 "$HOME\\.agent-toolkit\\common\\bootstrap_work.py"',
            self.powershell_blocks,
        )
        self.assertIn(
            '"$HOME\\.agent-toolkit\\.venv\\Scripts\\python.exe"',
            self.powershell_blocks,
        )

    def test_execution_policy_guidance_prefers_cmd_and_current_user(self) -> None:
        self.assertIn('"$HOME\\.agent-toolkit\\bin\\sls.cmd"', self.powershell_blocks)
        self.assertIn("Get-ExecutionPolicy -List", self.powershell_blocks)
        self.assertIn(
            "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned",
            self.powershell_blocks,
        )
        self.assertIn("安装器不会修改 `ExecutionPolicy`", self.readme)
        self.assertNotRegex(
            self.powershell_blocks,
            r"Set-ExecutionPolicy[^\n]*-Scope\s+LocalMachine",
        )
        self.assertNotRegex(
            self.powershell_blocks,
            r"Set-ExecutionPolicy[^\n]*-ExecutionPolicy\s+(?:Bypass|Unrestricted)",
        )


if __name__ == "__main__":
    unittest.main()
