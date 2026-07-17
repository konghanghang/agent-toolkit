from __future__ import annotations

import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_toolkit_install",
    ROOT / "scripts" / "install.py",
)
assert SPEC is not None and SPEC.loader is not None
install = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = install
SPEC.loader.exec_module(install)


class InstallTest(unittest.TestCase):
    def test_default_codex_home_ignores_environment_override(self) -> None:
        args = install.parse_args([])

        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/another-codex-home"}):
            codex_home, home_kind = install.resolve_codex_home(args)

        self.assertEqual(Path.home() / ".codex", codex_home)
        self.assertEqual("default", home_kind)

    def test_custom_codex_home_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"
            args = install.parse_args(["--rules-only", "--codex-home", str(missing)])

            with self.assertRaisesRegex(SystemExit, "Custom Codex home does not exist"):
                install.resolve_codex_home(args)

    def test_custom_codex_home_must_be_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir) / "codex-home"
            custom_home.write_text("not a directory", encoding="utf-8")
            args = install.parse_args(["--rules-only", "--codex-home", str(custom_home)])

            with self.assertRaisesRegex(SystemExit, "Custom Codex home is not a directory"):
                install.resolve_codex_home(args)

    def test_rules_only_plans_only_rules_for_custom_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(["--rules-only", "--codex-home", str(custom_home)])
            codex_home, home_kind = install.resolve_codex_home(args)

            specs = install.plan_links(args, codex_home)

        self.assertEqual("custom", home_kind)
        self.assertTrue(specs)
        self.assertTrue(all(spec.label == "Codex rule" for spec in specs))
        self.assertTrue(all(spec.target.parent == codex_home / "rules" for spec in specs))

    def test_rules_install_preserves_local_default_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            rules_dir = custom_home / "rules"
            rules_dir.mkdir()
            default_rules = rules_dir / "default.rules"
            default_rules.write_text("local rule\n", encoding="utf-8")
            args = install.parse_args(["--rules-only", "--codex-home", str(custom_home)])
            specs = install.plan_links(args, custom_home)

            results = [install.install_link(spec, args) for spec in specs]

            self.assertTrue(all(success for _, success in results))
            self.assertEqual("local rule\n", default_rules.read_text(encoding="utf-8"))
            self.assertFalse(default_rules.is_symlink())
            self.assertTrue(all(spec.target.is_symlink() for spec in specs))

    def test_codex_home_is_rejected_for_claude_only(self) -> None:
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                install.parse_args(["--claude-only", "--codex-home", "/tmp/codex-home"])

    def test_skill_is_rejected_for_rules_only(self) -> None:
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                install.parse_args(["--rules-only", "--skill", "git-commit"])


if __name__ == "__main__":
    unittest.main()
