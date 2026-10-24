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
    def setUp(self):
        # Existing behavior tests exercise a fully provisioned work machine.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = Path(temporary.name) / "profile.json"
        state.write_text(json.dumps({"schema_version": 1, "profile": "work"}))
        patcher = mock.patch.multiple(
            install,
            PROFILE_STATE=state,
            LEGACY_PROFILE_STATE=Path(temporary.name) / "legacy-profile.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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
            if os.name != "nt":
                self.assertEqual(0o600, spec.target.stat().st_mode & 0o777)

    def test_atomic_write_secures_new_windows_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "config.toml"

            with mock.patch.object(
                install, "is_windows", return_value=True
            ), mock.patch.object(
                install, "secure_private_file"
            ) as secure_private_file, mock.patch.object(
                install, "copy_windows_acl"
            ) as copy_windows_acl:
                install.atomic_write_text(target, "model = \"gpt-5.5\"\n")

            secure_private_file.assert_called_once()
            secured_path = secure_private_file.call_args.args[0]
            self.assertEqual(target.parent, secured_path.parent)
            self.assertTrue(secured_path.name.startswith(f".{target.name}."))
            copy_windows_acl.assert_not_called()
            self.assertEqual('model = "gpt-5.5"\n', target.read_text(encoding="utf-8"))

    def test_atomic_write_preserves_existing_windows_acl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "config.toml"
            target.write_text('model = "gpt-5.2"\n', encoding="utf-8")

            with mock.patch.object(
                install, "is_windows", return_value=True
            ), mock.patch.object(
                install, "secure_private_file"
            ) as secure_private_file, mock.patch.object(
                install, "copy_windows_acl"
            ) as copy_windows_acl:
                install.atomic_write_text(target, 'model = "gpt-5.5"\n')

            copy_windows_acl.assert_called_once()
            source, replacement = copy_windows_acl.call_args.args
            self.assertEqual(target, source)
            self.assertEqual(target.parent, replacement.parent)
            self.assertTrue(replacement.name.startswith(f".{target.name}."))
            secure_private_file.assert_not_called()
            self.assertEqual('model = "gpt-5.5"\n', target.read_text(encoding="utf-8"))

    def test_atomic_write_acl_failure_preserves_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "config.toml"
            original = 'model = "gpt-5.2"\n'
            target.write_text(original, encoding="utf-8")

            with mock.patch.object(
                install, "is_windows", return_value=True
            ), mock.patch.object(
                install, "copy_windows_acl", side_effect=OSError("ACL denied")
            ):
                with self.assertRaisesRegex(OSError, "ACL denied"):
                    install.atomic_write_text(target, 'model = "gpt-5.5"\n')

            self.assertEqual(original, target.read_text(encoding="utf-8"))
            self.assertEqual([], list(target.parent.glob(f".{target.name}.*")))

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

            self.assertTrue(first_success, first_message)
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

    def test_skill_is_rejected_for_opencode_only(self) -> None:
        with self.assertRaises(SystemExit):
            install.parse_args(["--opencode-only", "--skill", "git-commit"])

    def test_codex_home_is_rejected_for_opencode_only(self) -> None:
        with self.assertRaises(SystemExit):
            install.parse_args(["--opencode-only", "--codex-home", "/tmp/codex"])

    def test_opencode_only_plans_config_file_and_agents_directory(self) -> None:
        specs = install.plan_installs(install.parse_args(["--opencode-only"]))

        self.assertEqual(["OpenCode agents", "OpenCode config"], [spec.label for spec in specs])
        self.assertEqual(install.DEFAULT_OPENCODE_HOME / "agents", specs[0].target)
        self.assertEqual(install.DIRECTORY_LINK_STRATEGY, specs[0].strategy)
        self.assertEqual(install.DEFAULT_OPENCODE_HOME / "opencode.json", specs[1].target)
        self.assertEqual(install.FILE_LINK_STRATEGY, specs[1].strategy)

    def test_general_install_includes_opencode(self) -> None:
        specs = install.plan_installs(install.parse_args(["--profile", "general"]))
        labels = {spec.label for spec in specs}

        self.assertIn("OpenCode agents", labels)
        self.assertIn("OpenCode config", labels)

    def test_codex_only_excludes_opencode(self) -> None:
        specs = install.plan_installs(install.parse_args(["--profile", "general", "--codex-only"]))

        self.assertFalse(any(spec.label.startswith("OpenCode") for spec in specs))

    def test_resolve_link_target_normalizes_windows_namespace_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "opencode.json"
            source.write_bytes(b"{}\n")
            target = Path(temp_dir) / "home" / "link.json"
            target.parent.mkdir()
            for raw in (str(source), "\\\\?\\" + str(source), os.path.relpath(source, target.parent)):
                with self.subTest(raw=raw), mock.patch.object(
                    install, "is_windows", return_value=True
                ), mock.patch.object(install.os, "readlink", return_value=raw):
                    self.assertEqual(source.resolve(), install.resolve_link_target(target))

            # Inspect UNC conversion without contacting a network share.
            with mock.patch.object(install, "is_windows", return_value=True), mock.patch.object(
                install.os, "readlink", return_value="\\\\?\\UNC\\server\\share\\opencode.json"
            ), mock.patch.object(install, "Path", return_value=source) as path:
                self.assertEqual(source.resolve(), install.resolve_link_target(target))
                path.assert_called_once_with("\\\\server\\share\\opencode.json")

    def test_opencode_config_adopts_identical_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"AGENT_TOOLKIT_BACKUP_DIR": str(Path(temp_dir) / "backups")},
        ):
            root = Path(temp_dir)
            source = root / "opencode.json"
            source.write_text('{"model":"example"}\n', encoding="utf-8")
            target = root / "home" / "opencode.json"
            target.parent.mkdir()
            target.write_text('{"model":"example"}\n', encoding="utf-8")
            spec = install.InstallSpec(source, target, "OpenCode config", install.FILE_LINK_STRATEGY)
            args = install.parse_args(["--opencode-only"])

            message, success = install.install_spec(spec, args)

            self.assertTrue(success, message)
            self.assertIn("ADOPT", message)
            if os.name == "nt":
                self.assertFalse(target.is_symlink())
                self.assertEqual(source.read_bytes(), target.read_bytes())
            else:
                self.assertTrue(target.is_symlink())
                self.assertEqual(source.resolve(), install.resolve_link_target(target))
            self.assertIn("preserved", message)

            message, success = install.install_spec(spec, args)

            self.assertTrue(success, message)
            self.assertTrue(message.startswith("OK"), message)

    def test_opencode_config_rejects_different_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.json"
            source.write_text('{"model":"managed"}\n', encoding="utf-8")
            target = root / "target.json"
            target.write_text('{"model":"local"}\n', encoding="utf-8")
            spec = install.InstallSpec(source, target, "OpenCode config", install.FILE_LINK_STRATEGY)
            args = install.parse_args(["--opencode-only"])

            message, success = install.install_spec(spec, args)

            self.assertFalse(success)
            self.assertIn("differs", message)
            self.assertEqual('{"model":"local"}\n', target.read_text(encoding="utf-8"))
            self.assertFalse(target.is_symlink())

    def test_opencode_agents_adopt_identical_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {"AGENT_TOOLKIT_BACKUP_DIR": str(Path(temp_dir) / "backups")},
        ):
            root = Path(temp_dir)
            source = root / "agents"
            source.mkdir()
            (source / "reviewer.md").write_text("review\n", encoding="utf-8")
            (source / ".metadata").write_bytes(b"metadata\n")
            target = root / "home" / "agents"
            target.mkdir(parents=True)
            (target / "reviewer.md").write_text("review\n", encoding="utf-8")
            (target / ".metadata").write_bytes(b"metadata\n")
            spec = install.InstallSpec(source, target, "OpenCode agents", install.DIRECTORY_LINK_STRATEGY)
            args = install.parse_args(["--opencode-only"])

            message, success = install.install_spec(spec, args)

            self.assertTrue(success, message)
            self.assertIn("ADOPT", message)
            expected_kind = "junction" if os.name == "nt" else "symlink"
            self.assertEqual(expected_kind, install.directory_link_kind(target))
            self.assertEqual(source.resolve(), install.resolve_directory_link(target))
            self.assertEqual(b"metadata\n", (target / ".metadata").read_bytes())

    def test_opencode_agents_reject_directory_with_extra_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source-agents"
            source.mkdir()
            (source / "reviewer.md").write_text("review\n", encoding="utf-8")
            target = root / "home-agents"
            target.mkdir()
            (target / "reviewer.md").write_text("review\n", encoding="utf-8")
            (target / "local.md").write_text("local\n", encoding="utf-8")
            spec = install.InstallSpec(source, target, "OpenCode agents", install.DIRECTORY_LINK_STRATEGY)
            args = install.parse_args(["--opencode-only"])

            message, success = install.install_spec(spec, args)

            self.assertFalse(success)
            self.assertIn("not a directory link", message)
            self.assertTrue((target / "local.md").is_file())

    def test_opencode_agents_reject_extra_or_different_hidden_file(self) -> None:
        for source_content in (None, b"source\n"):
            with self.subTest(source_content=source_content), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                source = root / "source-agents"
                target = root / "home-agents"
                source.mkdir()
                target.mkdir()
                for directory in (source, target):
                    (directory / "reviewer.md").write_bytes(b"review\n")
                hidden = target / ".local-agent.md"
                hidden.write_bytes(b"local\n")
                if source_content is not None:
                    (source / hidden.name).write_bytes(source_content)
                spec = install.InstallSpec(source, target, "OpenCode agents", install.DIRECTORY_LINK_STRATEGY)

                message, success = install.install_spec(spec, install.parse_args(["--opencode-only"]))

                self.assertFalse(success, message)
                self.assertTrue(message.startswith("CONFLICT"), message)
                self.assertIsNone(install.directory_link_kind(target))
                self.assertEqual(b"local\n", hidden.read_bytes())

    def test_opencode_agent_validation_rejects_missing_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            agents = root / "agents"
            agents.mkdir()
            (agents / "empty.md").write_text(
                "---\ndescription: empty\nmode: subagent\n---\n",
                encoding="utf-8",
            )
            config = root / "opencode.json"
            config.write_text("{}\n", encoding="utf-8")

            with mock.patch.object(install, "OPENCODE_AGENTS_SOURCE", agents), mock.patch.object(
                install, "OPENCODE_CONFIG_SOURCE", config
            ):
                with self.assertRaisesRegex(SystemExit, "missing instructions"):
                    install.plan_installs(install.parse_args(["--opencode-only"]))

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

    def test_gitlab_install_selects_private_credentials_initializer(self) -> None:
        args = install.parse_args(["--skill", "gitlab-run-pipeline", "--dry-run"])

        initializers = install.selected_skill_initializers(args)

        self.assertEqual(
            [install.SKILLS_DIR / "gitlab-run-pipeline" / "scripts" / "init_config.py"],
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


class WindowsFileCopyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.json"
        self.source.write_bytes(b'{"model":"managed"}\r\n')
        self.target = self.root / "home" / "opencode.json"
        self.receipt = self.target.with_name(".opencode.json.agent-toolkit.json")
        self.spec = install.InstallSpec(
            self.source, self.target, "OpenCode config", install.FILE_LINK_STRATEGY
        )
        self.args = install.parse_args(["--opencode-only"])
        patches = [
            mock.patch.object(install, "is_windows", return_value=True),
            mock.patch.dict(os.environ, {"AGENT_TOOLKIT_BACKUP_DIR": str(self.root / "backups")}),
        ]
        if os.name != "nt":
            patches.extend([
                mock.patch.object(install, "secure_private_file"),
                mock.patch.object(install, "copy_windows_acl"),
            ])
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_copy_lifecycle_protects_local_edits_without_symlink_privileges(self):
        with mock.patch.object(
            Path, "symlink_to", side_effect=OSError(1314, "symlink privilege unavailable")
        ) as symlink:
            message, success = install.install_spec(self.spec, self.args)
            self.assertTrue(success, message)
            self.assertFalse(self.target.is_symlink())
            self.assertEqual(self.source.read_bytes(), self.target.read_bytes())

            message, success = install.install_spec(self.spec, self.args)
            self.assertTrue(success, message)
            self.assertTrue(message.startswith("OK"), message)

            previous = self.target.read_bytes()
            self.source.write_bytes(b'{"model":"updated"}\n')
            message, success = install.install_spec(self.spec, self.args)
            self.assertTrue(success, message)
            self.assertTrue(message.startswith("UPDATE"), message)
            self.assertEqual(self.source.read_bytes(), self.target.read_bytes())
            backups = list((self.root / "backups" / "opencode").iterdir())
            self.assertEqual([previous], [path.read_bytes() for path in backups])

            self.target.write_bytes(b'{"model":"local"}\n')
            receipt_before = self.receipt.read_bytes()
            args = install.parse_args(["--opencode-only", "--replace-managed"])
            message, success = install.install_spec(self.spec, args)
            self.assertFalse(success, message)
            self.assertIn("modified locally", message)
            self.assertEqual(b'{"model":"local"}\n', self.target.read_bytes())
            self.assertEqual(receipt_before, self.receipt.read_bytes())
            symlink.assert_not_called()

    def test_copy_rejects_unmanaged_files_and_invalid_receipts(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b'{"model":"local"}\n')
        for receipt_content in (None, "invalid json", '{"managed_by":"someone-else"}'):
            with self.subTest(receipt=receipt_content):
                if receipt_content is not None:
                    self.receipt.write_text(receipt_content, encoding="utf-8")
                message, success = install.install_spec(self.spec, self.args)
                self.assertFalse(success, message)
                self.assertTrue(message.startswith("CONFLICT"), message)
                self.assertEqual(b'{"model":"local"}\n', self.target.read_bytes())
                if receipt_content is None:
                    self.assertFalse(self.receipt.exists())
                else:
                    self.assertEqual(receipt_content, self.receipt.read_text(encoding="utf-8"))
        self.assertFalse((self.root / "backups").exists())

    def test_copy_dry_run_does_not_create_or_update_files(self):
        args = install.parse_args(["--opencode-only", "--dry-run"])
        message, success = install.install_spec(self.spec, args)
        self.assertTrue(success, message)
        self.assertFalse(self.target.parent.exists())

        self.target.parent.mkdir()
        self.target.write_bytes(self.source.read_bytes())
        message, success = install.install_spec(self.spec, args)
        self.assertTrue(success, message)
        self.assertIn("ADOPT", message)
        self.assertFalse(self.receipt.exists())
        self.assertFalse((self.root / "backups").exists())

        message, success = install.install_spec(self.spec, self.args)
        self.assertTrue(success, message)
        self.source.write_bytes(b'{"model":"updated"}\n')
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        message, success = install.install_spec(self.spec, args)
        self.assertTrue(success, message)
        self.assertIn("UPDATE", message)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_receipt_write_failure_restores_original_config(self):
        for action in ("create", "adopt", "update"):
            with self.subTest(action=action):
                target = self.root / action / "opencode.json"
                receipt = target.with_name(".opencode.json.agent-toolkit.json")
                spec = install.InstallSpec(self.source, target, "OpenCode config", install.FILE_LINK_STRATEGY)
                if action != "create":
                    target.parent.mkdir()
                    target.write_bytes(self.source.read_bytes())
                if action == "update":
                    message, success = install.install_spec(spec, self.args)
                    self.assertTrue(success, message)
                    self.source.write_bytes(b'{"model":"updated"}\n')
                original = target.read_bytes() if target.exists() else None
                original_receipt = receipt.read_bytes() if receipt.exists() else None
                with mock.patch.object(install, "atomic_write_text", side_effect=OSError("receipt write failed")):
                    message, success = install.install_spec(spec, self.args)
                self.assertFalse(success, message)
                self.assertIn("receipt write failed", message)
                self.assertEqual(original, target.read_bytes() if target.exists() else None)
                self.assertEqual(original_receipt, receipt.read_bytes() if receipt.exists() else None)


class ProfileTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state = self.directory / "machine" / "install-profile.json"
        self.legacy = self.directory / "checkout" / ".local" / "install-profile.json"
        patcher = mock.patch.multiple(
            install, PROFILE_STATE=self.state, LEGACY_PROFILE_STATE=self.legacy
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_first_install_requires_choice_before_writes(self):
        with mock.patch.object(sys, "argv", ["install.py"]), mock.patch.object(install, "install_spec") as write:
            with self.assertRaisesRegex(SystemExit, "Choose an installation profile"):
                install.main()
            write.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_general_excludes_work_skills_rules_and_initializers(self):
        args = install.parse_args(["--profile", "general"])
        specs = install.plan_installs(args)
        rules = {s.source.name for s in specs if s.label == "Codex rule"}
        self.assertEqual({"git-commit.rules", "github-readonly.rules"}, rules)
        skills = {s.source.name for s in specs if s.label == "Codex skill"}
        self.assertIn("git-commit", skills)
        self.assertIn("feature-delivery", skills)
        self.assertNotIn("work-feature-delivery", skills)
        self.assertNotIn("verify-test-db", skills)
        self.assertNotIn("tapd-story", skills)
        self.assertNotIn("nacos-config-query", skills)
        self.assertEqual([], install.selected_skill_initializers(args))

    def test_rules_only_obeys_general_profile(self):
        specs = install.plan_installs(install.parse_args(["--profile", "general", "--rules-only"]))
        self.assertEqual({"git-commit.rules", "github-readonly.rules"}, {s.source.name for s in specs})

    def test_claude_only_general_has_no_codex_or_work_components(self):
        args = install.parse_args(["--profile", "general", "--claude-only"])
        specs = install.plan_installs(args)
        self.assertTrue(all(s.label == "Claude Code skill" for s in specs))
        skills = {spec.source.name for spec in specs}
        self.assertIn("feature-delivery", skills)
        self.assertNotIn("work-feature-delivery", skills)
        self.assertNotIn("nacos-config-query", skills)
        self.assertEqual([], install.selected_skill_initializers(args))

    def test_unclassified_rule_and_invalid_category_rejected(self):
        rules = self.directory / "rules"
        rules.mkdir()
        (rules / "new.rules").write_text("# new")
        with mock.patch.object(install, "CODEX_RULES_SOURCE", rules):
            with self.assertRaisesRegex(SystemExit, "Unclassified rules: new.rules"):
                install.read_profile_manifest()
        manifest = self.directory / "manifest.json"
        data = json.loads(install.PROFILE_MANIFEST.read_text())
        data["skills"]["git-commit"] = "unknown"
        manifest.write_text(json.dumps(data))
        with mock.patch.object(install, "PROFILE_MANIFEST", manifest):
            with self.assertRaisesRegex(SystemExit, "must map names to general or work"):
                install.read_profile_manifest()

    def test_work_includes_general_and_work(self):
        general = install.plan_installs(install.parse_args(["--profile", "general"]))
        args = install.parse_args(["--profile", "work"])
        work = install.plan_installs(args)
        self.assertTrue(set(general) < set(work))
        for label in ("Codex skill", "Claude Code skill"):
            skills = {spec.source.name for spec in work if spec.label == label}
            self.assertTrue({"feature-delivery", "work-feature-delivery", "nacos-config-query"} <= skills)
        self.assertEqual(2, len(install.selected_skill_initializers(args)))

    def test_explicit_skill_cannot_bypass_profile(self):
        for skill in ("verify-test-db", "nacos-config-query"):
            with self.subTest(skill=skill):
                args = install.parse_args(["--profile", "general", "--skill", skill])
                with self.assertRaisesRegex(SystemExit, "outside general profile"):
                    install.plan_installs(args)

    def test_saved_profile_reused_and_explicit_profile_wins(self):
        install.save_profile("general")
        self.assertEqual("general", install.resolve_profile(install.parse_args([])))
        self.assertEqual("work", install.resolve_profile(install.parse_args(["--profile", "work"])))

    def test_legacy_profile_is_read_without_writing_machine_record(self):
        self.legacy.parent.mkdir(parents=True)
        original = '{"schema_version":1,"profile":"general"}\n'
        self.legacy.write_text(original)

        self.assertEqual("general", install.resolve_profile(install.parse_args([])))
        self.assertFalse(self.state.parent.exists())
        self.assertEqual(original, self.legacy.read_text())

    def test_machine_profile_wins_over_different_checkout_records(self):
        install.save_profile("general")
        for checkout in ("checkout-a", "checkout-b"):
            with self.subTest(checkout=checkout):
                legacy = self.directory / checkout / ".local" / "install-profile.json"
                legacy.parent.mkdir(parents=True)
                legacy.write_text('{"schema_version":1,"profile":"work"}')
                with mock.patch.object(install, "LEGACY_PROFILE_STATE", legacy):
                    self.assertEqual("general", install.resolve_profile(install.parse_args([])))
                    self.assertEqual("work", install.resolve_profile(install.parse_args(["--profile", "work"])))

    def test_corrupt_state_fails_closed(self):
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text('{"schema_version":1,"profile":"work"}')
        self.state.parent.mkdir()
        for content in (
            "not-json", "[]", '{"schema_version":true,"profile":"general"}',
            '{"schema_version":2,"profile":"general"}',
            '{"schema_version":1,"profile":"typo"}',
        ):
            with self.subTest(content=content):
                self.state.write_text(content)
                with self.assertRaises(SystemExit) as raised:
                    install.resolve_profile(install.parse_args([]))
                self.assertIn(str(self.state), str(raised.exception))
                self.assertEqual("general", install.resolve_profile(install.parse_args(["--profile", "general"])))

    def test_corrupt_legacy_record_is_reported_when_machine_record_missing(self):
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text('{"schema_version":1,"profile":"typo"}')
        with self.assertRaisesRegex(SystemExit, "invalid saved profile") as raised:
            install.resolve_profile(install.parse_args([]))
        self.assertIn(str(self.legacy), str(raised.exception))
        self.assertFalse(self.state.exists())

    def test_machine_record_access_errors_do_not_fall_back_to_legacy(self):
        self.legacy.parent.mkdir(parents=True)
        self.legacy.write_text('{"schema_version":1,"profile":"work"}')
        original_lstat = Path.lstat
        for error in (PermissionError("permission denied"), OSError("I/O failure")):
            def lstat(path, *args, **kwargs):
                if path == self.state:
                    raise error
                return original_lstat(path, *args, **kwargs)

            with self.subTest(error=type(error).__name__), mock.patch.object(Path, "lstat", lstat):
                with self.assertRaises(SystemExit) as raised:
                    install.resolve_profile(install.parse_args([]))
                self.assertIn(str(self.state), str(raised.exception))
                self.assertIn(str(error), str(raised.exception))

    def test_unclassified_skill_rejected_even_for_rules_only(self):
        skills = self.directory / "skills"
        new = skills / "new-skill"
        new.mkdir(parents=True)
        (new / "SKILL.md").write_text("new skill")
        with mock.patch.object(install, "SKILLS_DIR", skills):
            with self.assertRaisesRegex(SystemExit, "Unclassified skills: new-skill"):
                install.plan_installs(install.parse_args(["--profile", "general", "--rules-only"]))

    def test_dry_run_does_not_save_profile(self):
        with mock.patch.object(sys, "argv", ["install.py", "--profile", "general", "--dry-run"]), mock.patch.object(install, "install_spec", return_value=("OK", True)), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(0, install.main())
        self.assertFalse(self.state.exists())

    def test_legacy_dry_run_does_not_migrate_or_modify_records(self):
        self.legacy.parent.mkdir(parents=True)
        original = '{"schema_version":1,"profile":"general"}\n'
        self.legacy.write_text(original)
        with mock.patch.object(sys, "argv", ["install.py", "--dry-run"]), mock.patch.object(install, "install_spec", return_value=("OK", True)), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(0, install.main())
        self.assertFalse(self.state.parent.exists())
        self.assertEqual(original, self.legacy.read_text())

    def test_legacy_record_migrates_only_after_success(self):
        self.legacy.parent.mkdir(parents=True)
        original = '{"schema_version":1,"profile":"general"}\n'
        self.legacy.write_text(original)
        for success in (False, True):
            with self.subTest(success=success), mock.patch.object(sys, "argv", ["install.py"]), mock.patch.object(install, "install_spec", return_value=("OK" if success else "CONFLICT", success)), mock.patch("sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(0 if success else 1, install.main())
                self.assertEqual(success, self.state.exists())
                self.assertEqual(original, self.legacy.read_text())
        self.assertEqual({"schema_version": 1, "profile": "general"}, json.loads(self.state.read_text()))
        with mock.patch.object(install, "LEGACY_PROFILE_STATE", self.directory / "another-checkout" / "profile.json"):
            self.assertEqual("general", install.resolve_profile(install.parse_args([])))

    def test_existing_machine_record_is_unchanged_by_dry_run(self):
        install.save_profile("general")
        original = self.state.read_bytes()
        with mock.patch.object(sys, "argv", ["install.py", "--profile", "work", "--dry-run"]), mock.patch.object(install, "install_spec", return_value=("OK", True)), mock.patch.object(install, "run_skill_initializer", return_value=True), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(0, install.main())
        self.assertEqual(original, self.state.read_bytes())

    def test_failure_does_not_replace_saved_profile(self):
        install.save_profile("general")
        with mock.patch.object(sys, "argv", ["install.py", "--profile", "work"]), mock.patch.object(install, "install_spec", return_value=("CONFLICT", False)), mock.patch("sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(1, install.main())
        self.assertEqual("general", install.resolve_profile(install.parse_args([])))

    def test_success_remembers_profile(self):
        with mock.patch.object(sys, "argv", ["install.py", "--profile", "general"]), mock.patch.object(install, "install_spec", return_value=("OK", True)), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(0, install.main())
        self.assertEqual("general", install.resolve_profile(install.parse_args([])))


if __name__ == "__main__":
    unittest.main()
