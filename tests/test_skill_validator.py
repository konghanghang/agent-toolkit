from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "quick_validate.py"
SPEC = importlib.util.spec_from_file_location("quick_validate", VALIDATOR_PATH)
assert SPEC and SPEC.loader
quick_validate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quick_validate)


class SkillValidatorTest(unittest.TestCase):
    def test_all_repository_skills_are_valid(self) -> None:
        skill_directories = sorted(
            path.parent for path in (ROOT / "skills").glob("*/SKILL.md")
        )
        self.assertTrue(skill_directories)
        for skill_directory in skill_directories:
            with self.subTest(skill=skill_directory.name):
                self.assertEqual(
                    (True, "Skill is valid!"),
                    quick_validate.validate_skill(skill_directory),
                )

    def test_every_scripted_skill_includes_tests(self) -> None:
        skill_directories = sorted(
            {
                path.parent.parent
                for path in (ROOT / "skills").glob("*/scripts/*.py")
            }
        )
        self.assertTrue(skill_directories)
        for skill_directory in skill_directories:
            tests = [
                *skill_directory.glob("tests/test*.py"),
                *skill_directory.glob("scripts/tests/test*.py"),
            ]
            with self.subTest(skill=skill_directory.name):
                self.assertTrue(
                    tests,
                    f"{skill_directory.relative_to(ROOT)} has scripts but no tests",
                )

    def test_utf8_crlf_frontmatter_is_valid_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_directory = Path(tmp_dir) / "windows-demo"
            skill_directory.mkdir()
            content = (
                "---\r\n"
                "name: windows-demo\r\n"
                "description: 验证 Windows 下的 UTF-8 和 CRLF frontmatter\r\n"
                "---\r\n"
                "\r\n"
                "# Windows demo\r\n"
            )
            (skill_directory / "SKILL.md").write_bytes(content.encode("utf-8"))

            self.assertEqual(
                (True, "Skill is valid!"),
                quick_validate.validate_skill(skill_directory),
            )


if __name__ == "__main__":
    unittest.main()
