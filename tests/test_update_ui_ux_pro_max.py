from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_ui_ux_pro_max.py"


def load_module():
    spec = importlib.util.spec_from_file_location("update_ui_ux_pro_max_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


updater = load_module()


class UpdateUiUxProMaxTest(unittest.TestCase):
    def test_read_locked_release_validates_reproducibility_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_file = Path(temp_dir) / "external-skills.lock.json"
            lock_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "skills": {
                            updater.SKILL_NAME: {
                                "source": updater.UPSTREAM_URL,
                                "package": updater.PACKAGE_NAME,
                                "version": "2.15.0",
                                "install_scope": "main-skill-only",
                                "local_transform": updater.LOCAL_TRANSFORM,
                                "content_sha256": "a" * 64,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(updater, "LOCK_FILE", lock_file):
                release = updater.read_locked_release()

            self.assertEqual(("2.15.0", "a" * 64), release)

    def test_read_locked_release_rejects_transform_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_file = Path(temp_dir) / "external-skills.lock.json"
            lock_file.write_text(
                json.dumps(
                    {
                        "skills": {
                            updater.SKILL_NAME: {
                                "source": updater.UPSTREAM_URL,
                                "package": updater.PACKAGE_NAME,
                                "version": "2.15.0",
                                "install_scope": "main-skill-only",
                                "local_transform": "unknown-transform",
                                "content_sha256": "a" * 64,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(updater, "LOCK_FILE", lock_file):
                with self.assertRaisesRegex(SystemExit, "local_transform"):
                    updater.read_locked_release()

    def test_generate_skill_prefers_current_agents_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            current = work_dir / ".agents" / "skills" / updater.SKILL_NAME
            legacy = work_dir / ".codex" / "skills" / updater.SKILL_NAME
            current.mkdir(parents=True)
            legacy.mkdir(parents=True)

            completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            with patch.object(updater, "run", return_value=completed):
                generated = updater.generate_skill("npx", "2.15.0", work_dir)

            self.assertEqual(current, generated)

    def test_generate_skill_keeps_legacy_codex_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            legacy = work_dir / ".codex" / "skills" / updater.SKILL_NAME
            legacy.mkdir(parents=True)

            completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            with patch.object(updater, "run", return_value=completed):
                generated = updater.generate_skill("npx", "2.10.2", work_dir)

            self.assertEqual(legacy, generated)

    def test_generate_skill_reports_all_supported_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            completed = subprocess.CompletedProcess([], 0, stdout="generated elsewhere", stderr="")

            with patch.object(updater, "run", return_value=completed):
                with self.assertRaisesRegex(SystemExit, "Expected one of") as raised:
                    updater.generate_skill("npx", "2.15.0", work_dir)

            message = str(raised.exception)
            self.assertIn(str(work_dir / ".agents" / "skills" / updater.SKILL_NAME), message)
            self.assertIn(str(work_dir / ".codex" / "skills" / updater.SKILL_NAME), message)

    def test_bundled_tests_and_data_validator_run_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir)
            tests_dir = skill_dir / "scripts" / "tests"
            tests_dir.mkdir(parents=True)
            (tests_dir / "test_core.py").write_text("", encoding="utf-8")
            (tests_dir / "test_catalog_refresh.py").write_text("", encoding="utf-8")
            validator = skill_dir / "scripts" / "validate_data.py"
            validator.write_text("", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

            with patch.object(updater, "run", return_value=completed) as run:
                checks = updater.run_bundled_checks(skill_dir)

            self.assertEqual(
                [
                    "bundled unit tests (1 files)",
                    "source-only tests skipped (test_catalog_refresh.py)",
                    "data validation",
                ],
                checks,
            )
            self.assertEqual(2, run.call_count)
            test_command = run.call_args_list[0].args[0]
            validator_command = run.call_args_list[1].args[0]
            self.assertEqual(updater.sys.executable, test_command[0])
            self.assertEqual("unittest", test_command[2])
            self.assertEqual(str(tests_dir), test_command[5])
            self.assertEqual([updater.sys.executable, str(validator)], validator_command)

    def test_local_transform_moves_quick_reference_with_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir)
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(
                "---\nname: ui-ux-pro-max\ndescription: test\n---\n"
                "# Skill\n\n"
                "## Quick Reference\n\n"
                "### 1. Accessibility (CRITICAL)\n\n- Keep focus visible\n\n"
                "### 2. Interaction & Motion\n\n- Respect reduced motion\n\n"
                "## How to Use\n\n"
                "Run the search tool or rely on the Quick Reference sections above.\n"
                "Review from the Quick Reference checklist above.\n",
                encoding="utf-8",
            )

            transforms = updater.apply_local_transforms(skill_dir)

            transformed = skill_file.read_text(encoding="utf-8")
            reference = (skill_dir / "references" / "quick-reference.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual([updater.LOCAL_TRANSFORM], transforms)
            self.assertIn("references/quick-reference.md", transformed)
            self.assertNotIn("Keep focus visible", transformed)
            self.assertIn("## Contents", reference)
            self.assertIn(
                "[1. Accessibility (CRITICAL)](#1-accessibility-critical)",
                reference,
            )
            self.assertIn("Keep focus visible", reference)
            self.assertIn("Respect reduced motion", reference)
            self.assertIn("read the linked Quick Reference", transformed)
            self.assertIn("linked Quick Reference checklist", transformed)
            self.assertNotIn("Quick Reference sections above", transformed)

    def test_local_transform_fails_closed_when_upstream_structure_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: ui-ux-pro-max\ndescription: test\n---\n# Changed upstream\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "Quick Reference boundary"):
                updater.apply_local_transforms(skill_dir)

    def test_content_digest_ignores_python_runtime_caches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir)
            source = skill_dir / "scripts" / "search.py"
            source.parent.mkdir()
            source.write_text("print('search')\n", encoding="utf-8")
            expected = updater.content_digest(skill_dir)

            cache = source.parent / "__pycache__" / "search.cpython-311.pyc"
            cache.parent.mkdir()
            cache.write_bytes(b"runtime cache")
            (source.parent / "orphan.pyc").write_bytes(b"runtime cache")

            self.assertEqual(expected, updater.content_digest(skill_dir))
            source.write_text("print('changed')\n", encoding="utf-8")
            self.assertNotEqual(expected, updater.content_digest(skill_dir))

    def test_write_lock_records_local_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_file = Path(temp_dir) / "external-skills.lock.json"
            with patch.object(updater, "LOCK_FILE", lock_file):
                updater.write_lock("2.15.0", "abc123")

            entry = json.loads(lock_file.read_text(encoding="utf-8"))["skills"][
                updater.SKILL_NAME
            ]
            self.assertEqual("2.15.0", entry["version"])
            self.assertEqual("main-skill-only", entry["install_scope"])
            self.assertEqual(updater.LOCAL_TRANSFORM, entry["local_transform"])
            self.assertEqual("abc123", entry["content_sha256"])

    def test_empty_bundled_test_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir)
            (skill_dir / "scripts" / "tests").mkdir(parents=True)

            with self.assertRaisesRegex(SystemExit, "test directory is empty"):
                updater.run_bundled_checks(skill_dir)

    def test_install_rolls_back_when_lock_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skills_dir = root / "skills"
            target = skills_dir / updater.SKILL_NAME
            generated = root / "generated"
            backup_dir = root / "backups"
            target.mkdir(parents=True)
            generated.mkdir()
            (target / "version.txt").write_text("old", encoding="utf-8")
            (generated / "version.txt").write_text("new", encoding="utf-8")

            with patch.object(updater, "SKILLS_DIR", skills_dir), patch.object(
                updater, "TARGET_DIR", target
            ), patch.object(updater, "BACKUP_DIR", backup_dir), patch.object(
                updater, "write_lock", side_effect=OSError("lock denied")
            ):
                with self.assertRaisesRegex(OSError, "lock denied"):
                    updater.install_skill(generated, "2.15.0", "new-digest", 3)

            self.assertEqual(
                "old",
                (target / "version.txt").read_text(encoding="utf-8"),
            )
            self.assertFalse(any(skills_dir.glob(f".{updater.SKILL_NAME}.candidate-*")))

    def test_locked_install_does_not_rewrite_lock_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skills_dir = root / "skills"
            target = skills_dir / updater.SKILL_NAME
            generated = root / "generated"
            generated.mkdir()
            (generated / "version.txt").write_text("locked", encoding="utf-8")
            digest = updater.content_digest(generated)

            with patch.object(updater, "SKILLS_DIR", skills_dir), patch.object(
                updater, "TARGET_DIR", target
            ), patch.object(updater, "BACKUP_DIR", root / "backups"), patch.object(
                updater, "write_lock"
            ) as write_lock:
                updater.install_skill(
                    generated,
                    "2.15.0",
                    digest,
                    0,
                    update_lock=False,
                )

            write_lock.assert_not_called()
            self.assertEqual(
                "locked",
                (target / "version.txt").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
