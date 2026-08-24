from __future__ import annotations

import importlib.util
import io
import json
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

            specs = install.plan_installs(args, codex_home)

        self.assertEqual("custom", home_kind)
        self.assertTrue(specs)
        self.assertTrue(all(spec.label == "Codex rule" for spec in specs))
        self.assertTrue(all(spec.target.parent == codex_home / "rules" for spec in specs))
        self.assertTrue(all(spec.strategy == install.COPY_STRATEGY for spec in specs))

    def test_agents_only_plans_agents_directory_link_for_custom_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(["--agents-only", "--codex-home", str(custom_home)])
            codex_home, home_kind = install.resolve_codex_home(args)

            specs = install.plan_installs(args, codex_home)

        self.assertEqual("custom", home_kind)
        self.assertEqual(1, len(specs))
        self.assertEqual("Codex agents", specs[0].label)
        self.assertEqual(install.CODEX_AGENTS_SOURCE, specs[0].source)
        self.assertEqual(codex_home / "agents", specs[0].target)
        self.assertEqual(install.DIRECTORY_LINK_STRATEGY, specs[0].strategy)

    def test_full_install_includes_agents_for_custom_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(["--codex-home", str(custom_home)])

            specs = install.plan_installs(args, custom_home)

        agent_specs = [spec for spec in specs if spec.label == "Codex agents"]
        self.assertEqual(1, len(agent_specs))
        self.assertEqual(custom_home / "agents", agent_specs[0].target)

    def test_full_install_includes_playwright_config_for_custom_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(["--codex-home", str(custom_home)])

            specs = install.plan_installs(args, custom_home)

        config_specs = [
            spec for spec in specs if spec.label == "Codex Playwright MCP config"
        ]
        self.assertEqual(1, len(config_specs))
        self.assertEqual(custom_home / "config.toml", config_specs[0].target)
        self.assertEqual(install.TOML_MERGE_STRATEGY, config_specs[0].strategy)

    def test_config_only_plans_only_playwright_config_for_custom_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(
                ["--config-only", "--codex-home", str(custom_home)]
            )

            specs = install.plan_installs(args, custom_home)

        self.assertEqual(1, len(specs))
        self.assertEqual("Codex Playwright MCP config", specs[0].label)
        self.assertEqual(custom_home / "config.toml", specs[0].target)

    def test_playwright_config_install_creates_expanded_private_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(
                ["--config-only", "--codex-home", str(custom_home)]
            )
            spec = install.plan_installs(args, custom_home)[0]

            message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("CREATE", message)
            content = spec.target.read_text(encoding="utf-8")
            profile_path = Path.home() / ".local" / "share" / "playwright-profile"
            npm_cache_path = (
                Path.home() / ".local" / "share" / "playwright-mcp" / "npm-cache"
            )
            self.assertIn(
                f'"--user-data-dir", {json.dumps(str(profile_path))}',
                content,
            )
            self.assertIn('default_tools_approval_mode = "approve"', content)
            self.assertIn(
                'disabled_tools = ["browser_run_code_unsafe"]',
                content,
            )
            self.assertIn(
                f"npm_config_cache = {json.dumps(str(npm_cache_path))}",
                content,
            )
            self.assertEqual(0o600, spec.target.stat().st_mode & 0o777)

    def test_playwright_config_install_preserves_existing_config_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            config_path = custom_home / "config.toml"
            npm_cache_path = (
                Path.home() / ".local" / "share" / "playwright-mcp" / "npm-cache"
            )
            config_path.write_text(
                "\n".join(
                    [
                        'model = "gpt-5.5"',
                        "",
                        "[mcp_servers.other]",
                        'url = "https://example.invalid/mcp"',
                        "",
                        "[mcp_servers.playwright]",
                        'command = "npx"',
                        'args = ["-y", "@playwright/mcp@latest"]',
                        "",
                        "[mcp_servers.playwright.env]",
                        f"npm_config_cache = {json.dumps(str(npm_cache_path))}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            args = install.parse_args(
                ["--config-only", "--codex-home", str(custom_home)]
            )
            spec = install.plan_installs(args, custom_home)[0]

            first_message, first_success = install.install_spec(spec, args)
            first_content = config_path.read_text(encoding="utf-8")
            second_message, second_success = install.install_spec(spec, args)

            self.assertTrue(first_success)
            self.assertIn("UPDATE", first_message)
            self.assertIn('model = "gpt-5.5"', first_content)
            self.assertIn("[mcp_servers.other]", first_content)
            self.assertIn('args = ["-y", "@playwright/mcp@latest"]', first_content)
            self.assertIn('default_tools_approval_mode = "approve"', first_content)
            self.assertIn(
                'disabled_tools = ["browser_run_code_unsafe"]', first_content
            )
            self.assertTrue(second_success)
            self.assertIn("already contains managed settings", second_message)
            self.assertEqual(first_content, config_path.read_text(encoding="utf-8"))

    def test_playwright_config_install_rejects_unmanaged_approval_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            config_path = custom_home / "config.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[mcp_servers.playwright]",
                        'command = "npx"',
                        'default_tools_approval_mode = "prompt"',
                        "",
                        "[mcp_servers.playwright.env]",
                        'npm_config_cache = "/tmp/npm-cache"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            original = config_path.read_text(encoding="utf-8")
            args = install.parse_args(
                ["--config-only", "--codex-home", str(custom_home)]
            )
            spec = install.plan_installs(args, custom_home)[0]

            message, success = install.install_spec(spec, args)

            self.assertFalse(success)
            self.assertIn("unmanaged value for default_tools_approval_mode", message)
            self.assertEqual(original, config_path.read_text(encoding="utf-8"))

    def test_playwright_config_dry_run_does_not_create_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(
                [
                    "--config-only",
                    "--codex-home",
                    str(custom_home),
                    "--dry-run",
                ]
            )
            spec = install.plan_installs(args, custom_home)[0]

            message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("CREATE", message)
            self.assertFalse(spec.target.exists())

    def test_rules_install_preserves_local_default_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            rules_dir = custom_home / "rules"
            rules_dir.mkdir()
            default_rules = rules_dir / "default.rules"
            default_rules.write_text("local rule\n", encoding="utf-8")
            args = install.parse_args(["--rules-only", "--codex-home", str(custom_home)])
            specs = install.plan_installs(args, custom_home)

            results = [install.install_spec(spec, args) for spec in specs]

            self.assertTrue(all(success for _, success in results))
            self.assertEqual("local rule\n", default_rules.read_text(encoding="utf-8"))
            self.assertFalse(default_rules.is_symlink())
            self.assertTrue(all(spec.target.is_file() for spec in specs))
            self.assertTrue(all(not spec.target.is_symlink() for spec in specs))
            self.assertTrue(
                all(spec.target.read_bytes() == spec.source.read_bytes() for spec in specs)
            )

    @unittest.skipIf(os.name == "nt", "requires file symlink creation")
    def test_rules_install_migrates_current_managed_symlink_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(["--rules-only", "--codex-home", str(custom_home)])
            spec = install.plan_installs(args, custom_home)[0]
            spec.target.parent.mkdir()
            spec.target.symlink_to(spec.source)

            message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("MIGRATE", message)
            self.assertTrue(spec.target.is_file())
            self.assertFalse(spec.target.is_symlink())
            self.assertEqual(spec.source.read_bytes(), spec.target.read_bytes())

    def test_rules_install_updates_managed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(["--rules-only", "--codex-home", str(custom_home)])
            spec = install.plan_installs(args, custom_home)[0]
            spec.target.parent.mkdir()
            spec.target.write_text(
                f"{install.RULE_MANAGED_MARKER}\nold rule\n",
                encoding="utf-8",
            )

            message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("UPDATE", message)
            self.assertEqual(spec.source.read_bytes(), spec.target.read_bytes())

    def test_rules_install_rejects_unmanaged_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(["--rules-only", "--codex-home", str(custom_home)])
            spec = install.plan_installs(args, custom_home)[0]
            spec.target.parent.mkdir()
            spec.target.write_text("local rule\n", encoding="utf-8")

            message, success = install.install_spec(spec, args)

            self.assertFalse(success)
            self.assertIn("is not managed", message)
            self.assertEqual("local rule\n", spec.target.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "requires file symlink creation")
    def test_rules_dry_run_does_not_migrate_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(
                ["--rules-only", "--codex-home", str(custom_home), "--dry-run"]
            )
            spec = install.plan_installs(args, custom_home)[0]
            spec.target.parent.mkdir()
            spec.target.symlink_to(spec.source)

            message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("MIGRATE", message)
            self.assertTrue(spec.target.is_symlink())

    def test_agents_install_creates_directory_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(["--agents-only", "--codex-home", str(custom_home)])
            spec = install.plan_installs(args, custom_home)[0]

            message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("CREATE", message)
            expected_kind = "junction" if os.name == "nt" else "symlink"
            self.assertEqual(expected_kind, install.directory_link_kind(spec.target))
            self.assertEqual(
                install.CODEX_AGENTS_SOURCE.resolve(),
                install.resolve_directory_link(spec.target),
            )

    def test_agents_install_rejects_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            agents_target = custom_home / "agents"
            agents_target.mkdir()
            args = install.parse_args(["--agents-only", "--codex-home", str(custom_home)])
            spec = install.plan_installs(args, custom_home)[0]

            message, success = install.install_spec(spec, args)

            self.assertFalse(success)
            self.assertIn("exists and is not a directory link", message)
            self.assertTrue(agents_target.is_dir())
            self.assertFalse(agents_target.is_symlink())

    @unittest.skipIf(os.name == "nt", "requires directory symlink creation")
    def test_agents_install_rejects_unknown_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            custom_home = root / "codex-home"
            custom_home.mkdir()
            unknown_source = root / "external-agents"
            unknown_source.mkdir()
            agents_target = custom_home / "agents"
            agents_target.symlink_to(unknown_source, target_is_directory=True)
            args = install.parse_args(
                [
                    "--agents-only",
                    "--codex-home",
                    str(custom_home),
                    "--replace-managed",
                ]
            )
            spec = install.plan_installs(args, custom_home)[0]

            message, success = install.install_spec(spec, args)

            self.assertFalse(success)
            self.assertIn(str(unknown_source), message)
            self.assertEqual(unknown_source.resolve(), install.resolve_link_target(agents_target))

    @unittest.skipIf(os.name == "nt", "requires directory symlink creation")
    def test_agents_install_replaces_managed_symlink_with_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            agents_target = custom_home / "agents"
            agents_target.symlink_to(install.SKILLS_DIR, target_is_directory=True)
            args = install.parse_args(
                [
                    "--agents-only",
                    "--codex-home",
                    str(custom_home),
                    "--replace-managed",
                ]
            )
            spec = install.plan_installs(args, custom_home)[0]

            message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("REPLACE", message)
            self.assertEqual(
                install.CODEX_AGENTS_SOURCE.resolve(),
                install.resolve_link_target(agents_target),
            )

    def test_agents_dry_run_does_not_create_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_home = Path(temp_dir)
            args = install.parse_args(
                ["--agents-only", "--codex-home", str(custom_home), "--dry-run"]
            )
            spec = install.plan_installs(args, custom_home)[0]

            message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("CREATE", message)
            self.assertFalse(os.path.lexists(spec.target))

    def test_agent_validation_rejects_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            source.mkdir()
            (source / "invalid.toml").write_text(
                'name = "invalid"\n',
                encoding="utf-8",
            )
            custom_home = root / "codex-home"
            custom_home.mkdir()
            args = install.parse_args(
                ["--agents-only", "--codex-home", str(custom_home)]
            )

            with mock.patch.object(install, "CODEX_AGENTS_SOURCE", source):
                with self.assertRaisesRegex(
                    SystemExit,
                    "missing required fields: description, developer_instructions",
                ):
                    install.plan_installs(args, custom_home)

    def test_skills_remain_directory_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skill"
            source = install.SKILLS_DIR / "git-commit"
            spec = install.InstallSpec(
                source,
                target,
                "Codex skill",
                install.DIRECTORY_LINK_STRATEGY,
            )
            args = install.parse_args([])

            _, success = install.install_spec(spec, args)

            self.assertTrue(success)
            expected_kind = "junction" if os.name == "nt" else "symlink"
            self.assertEqual(expected_kind, install.directory_link_kind(target))
            self.assertEqual(source.resolve(), install.resolve_directory_link(target))

    def test_windows_directory_link_uses_junction(self) -> None:
        source = install.SKILLS_DIR / "git-commit"
        target = Path("C:/Users/test/.agents/skills/git-commit")

        with mock.patch.object(install.os, "name", "nt"), mock.patch.object(
            install, "create_windows_junction"
        ) as create_junction:
            install.create_directory_link(source, target)

        create_junction.assert_called_once_with(source, target)

    def test_windows_junction_command_uses_environment_paths(self) -> None:
        source = Path("C:/toolkit path/skills/git-commit")
        target = Path("C:/Users/test/.agents/skills/git-commit")
        completed = mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(install.subprocess, "run", return_value=completed) as run:
            install.create_windows_junction(source, target)

        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual("powershell.exe", command[0])
        self.assertIn("New-Item -ItemType Junction", command[-1])
        self.assertNotIn(str(source), " ".join(command))
        self.assertNotIn(str(target), " ".join(command))
        self.assertEqual(str(source), environment[install.WINDOWS_JUNCTION_SOURCE_ENV])
        self.assertEqual(str(target), environment[install.WINDOWS_JUNCTION_TARGET_ENV])

    def test_windows_junction_failure_is_actionable_and_does_not_copy(self) -> None:
        completed = mock.Mock(returncode=1, stdout="", stderr="Access denied")

        with mock.patch.object(install.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(
                install.DirectoryLinkError,
                "local NTFS volumes",
            ):
                install.create_windows_junction(Path("C:/source"), Path("C:/target"))

    def test_install_reports_directory_link_creation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skill"
            spec = install.InstallSpec(
                install.SKILLS_DIR / "git-commit",
                target,
                "Codex skill",
                install.DIRECTORY_LINK_STRATEGY,
            )
            args = install.parse_args([])

            with mock.patch.object(
                install,
                "create_directory_link",
                side_effect=install.DirectoryLinkError("junction failed"),
            ):
                message, success = install.install_spec(spec, args)

            self.assertFalse(success)
            self.assertIn("junction failed", message)
            self.assertFalse(target.exists())

    def test_existing_windows_junction_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "skill"
            target.mkdir()
            source = install.SKILLS_DIR / "git-commit"
            spec = install.InstallSpec(
                source,
                target,
                "Codex skill",
                install.DIRECTORY_LINK_STRATEGY,
            )
            args = install.parse_args([])

            with mock.patch.object(
                install,
                "directory_link_kind",
                return_value="junction",
            ), mock.patch.object(
                install,
                "resolve_directory_link",
                return_value=source.resolve(),
            ):
                message, success = install.install_spec(spec, args)

            self.assertTrue(success)
            self.assertIn("OK", message)

    def test_codex_home_is_rejected_for_claude_only(self) -> None:
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                install.parse_args(["--claude-only", "--codex-home", "/tmp/codex-home"])

    def test_skill_is_rejected_for_rules_only(self) -> None:
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                install.parse_args(["--rules-only", "--skill", "git-commit"])

    def test_skill_is_rejected_for_agents_only(self) -> None:
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                install.parse_args(["--agents-only", "--skill", "git-commit"])

    def test_skill_is_rejected_for_config_only(self) -> None:
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                install.parse_args(["--config-only", "--skill", "git-commit"])

    def test_verify_test_db_install_selects_private_config_initializer(self) -> None:
        args = install.parse_args(["--skill", "verify-test-db", "--dry-run"])

        initializers = install.selected_skill_initializers(args)

        self.assertEqual(
            [install.SKILLS_DIR / "verify-test-db" / "scripts" / "init_config.py"],
            initializers,
        )

    def test_rules_and_agents_only_skip_skill_initializers(self) -> None:
        for argv in (["--rules-only"], ["--agents-only"]):
            with self.subTest(argv=argv):
                args = install.parse_args(argv)
                self.assertEqual([], install.selected_skill_initializers(args))

    def test_dry_run_is_forwarded_to_skill_initializer(self) -> None:
        initializer = install.SKILLS_DIR / "verify-test-db" / "scripts" / "init_config.py"
        args = install.parse_args(["--skill", "verify-test-db", "--dry-run"])
        completed = mock.Mock(returncode=0)

        with mock.patch.object(install.subprocess, "run", return_value=completed) as run:
            success = install.run_skill_initializer(initializer, args)

        self.assertTrue(success)
        run.assert_called_once_with([sys.executable, str(initializer), "--dry-run"])


if __name__ == "__main__":
    unittest.main()
