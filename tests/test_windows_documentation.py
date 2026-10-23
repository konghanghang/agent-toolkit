from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_GUIDE_PATH = ROOT / "docs" / "windows.md"
INSTALLATION_GUIDE_PATH = ROOT / "docs" / "installation.md"
WINDOWS_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "windows.yml"
LOCAL_BROWSER_SKILL_PATH = ROOT / "skills" / "local-browser-verify" / "SKILL.md"
CAPTURE_DRAFT_SKILL_PATH = ROOT / "skills" / "capture-iminling-draft" / "SKILL.md"


class WindowsDocumentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.windows_guide = WINDOWS_GUIDE_PATH.read_text(encoding="utf-8")
        cls.installation_guide = INSTALLATION_GUIDE_PATH.read_text(encoding="utf-8")
        cls.workflow = WINDOWS_WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.local_browser_skill = LOCAL_BROWSER_SKILL_PATH.read_text(encoding="utf-8")
        cls.capture_draft_skill = CAPTURE_DRAFT_SKILL_PATH.read_text(encoding="utf-8")
        cls.powershell_blocks = "\n".join(
            re.findall(r"```powershell\n(.*?)```", cls.windows_guide, flags=re.DOTALL)
        )
        optional_section = re.search(
            r"^## 按能力准备外部依赖\n(.*?)(?=^## |\Z)",
            cls.installation_guide,
            flags=re.DOTALL | re.MULTILINE,
        )
        assert optional_section is not None
        cls.optional_dependencies = optional_section.group(1)

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
                self.assertIn(expected, self.windows_guide)

    def test_runtime_and_optional_dependencies_are_distinguished(self) -> None:
        self.assertIn("`scripts/install.py` 只负责", self.installation_guide)
        self.assertIn("`common/bootstrap_work.py`", self.installation_guide)
        self.assertIn("`requirements.txt`", self.installation_guide)
        for dependency in (
            "Google Chrome",
            "ripgrep",
            "curl.exe",
            "Node.js",
            "npm",
            "npx",
            "PicGo Core",
            "web-uploader",
            "Hugo",
            "rsvg-convert",
        ):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, self.optional_dependencies)

    def test_remaining_skill_guidance_is_platform_neutral(self) -> None:
        self.assertIn("POSIX shell 的 `source`", self.local_browser_skill)
        self.assertIn("PowerShell 的点调用（dot-source）", self.local_browser_skill)
        self.assertNotIn("先 `source` 用户的 shell 配置", self.local_browser_skill)

        capture_powershell_blocks = "\n".join(
            re.findall(
                r"```powershell\n(.*?)```",
                self.capture_draft_skill,
                flags=re.DOTALL,
            )
        )
        self.assertIn(
            'hugo --source "$IminlingRepo" --minify --buildDrafts',
            capture_powershell_blocks,
        )
        self.assertIn("$LASTEXITCODE", capture_powershell_blocks)

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
        self.assertIn("安装器不会修改 `ExecutionPolicy`", self.windows_guide)
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
