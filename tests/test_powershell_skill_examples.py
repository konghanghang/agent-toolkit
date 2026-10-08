from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

WRAPPER_EXAMPLES = {
    "aliyun-sls-log-query": "sls.cmd",
    "archery-prod-query": "archery.cmd",
    "jumpserver-k8s-log-query": "jms.cmd",
    "verify-test-db": "testdb.cmd",
    "gitlab-run-pipeline": "gitlab.cmd",
}
DIRECT_SCRIPT_EXAMPLES = {
    "local-browser-verify": "browser_verify.py",
    "nacos-config-query": "nacos_query.py",
    "prometheus-query": "prometheus_query.py",
    "tapd-story": "tapd_story.py",
    "tapd-weekly-report": "tapd_weekly_report.py",
    "upload-to-easyimage": "upload_easyimage.py",
    "capture-iminling-draft": "resolve_iminling_repo.py",
}
EXTERNAL_SCRIPTED_SKILLS = {"ui-ux-pro-max"}


def powershell_blocks(skill_name: str) -> str:
    path = ROOT / "skills" / skill_name / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```powershell\n(.*?)```", content, flags=re.DOTALL)
    if not blocks:
        raise AssertionError(f"{path.relative_to(ROOT)} 缺少 PowerShell 示例")
    return "\n".join(blocks)


class PowerShellSkillExamplesTest(unittest.TestCase):
    def test_all_managed_scripted_skills_are_covered(self) -> None:
        discovered = {
            path.parent.parent.name
            for path in (ROOT / "skills").glob("*/scripts/*.py")
        }
        managed = discovered - EXTERNAL_SCRIPTED_SKILLS
        documented = set(WRAPPER_EXAMPLES) | set(DIRECT_SCRIPT_EXAMPLES)
        self.assertEqual(managed, documented)

    def test_public_wrappers_are_used_from_powershell(self) -> None:
        for skill_name, wrapper_name in WRAPPER_EXAMPLES.items():
            with self.subTest(skill=skill_name):
                blocks = powershell_blocks(skill_name)
                self.assertIn(
                    f'& "$AgentToolkitRoot\\bin\\{wrapper_name}"',
                    blocks,
                )

    def test_direct_scripts_use_quoted_absolute_paths(self) -> None:
        for skill_name, script_name in DIRECT_SCRIPT_EXAMPLES.items():
            with self.subTest(skill=skill_name):
                blocks = powershell_blocks(skill_name)
                self.assertIn('& "$PythonBin"', blocks)
                self.assertIn(
                    f'"$AgentToolkitRoot\\skills\\{skill_name}\\scripts\\{script_name}"',
                    blocks,
                )

    def test_powershell_examples_do_not_assume_bash_runtime_variables(self) -> None:
        for skill_name in (*WRAPPER_EXAMPLES, *DIRECT_SCRIPT_EXAMPLES):
            with self.subTest(skill=skill_name):
                blocks = powershell_blocks(skill_name)
                self.assertNotIn("$AGENT_TOOLKIT_ROOT", blocks)
                self.assertNotIn("$PYTHON_BIN", blocks)
                self.assertNotIn("python3 ", blocks)


if __name__ == "__main__":
    unittest.main()
