from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = TOOLKIT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import install  # noqa: E402
import install_agy_hud  # noqa: E402


class InstallAgyHudTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp = Path(self.temp_dir.name)
        self.config_target = self.temp / "antigravity-cli" / "agy-hud.config.json"
        patcher = mock.patch.object(install, "AGY_HUD_CONFIG_TARGET", self.config_target)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_parse_semver_handles_prefix_and_invalid(self) -> None:
        self.assertEqual((20, 19, 4), install_agy_hud.parse_semver("v20.19.4\n"))
        self.assertEqual((1, 5, 16), install_agy_hud.parse_semver("1.5.16"))
        self.assertIsNone(install_agy_hud.parse_semver("unknown"))

    def test_check_prerequisites_validates_node_and_agy_versions(self) -> None:
        with mock.patch.object(
            install_agy_hud, "probe_tool_version", side_effect=[(20, 19, 4), (1, 5, 16)]
        ):
            errors, _ = install_agy_hud.check_prerequisites()
            self.assertEqual([], errors)

        with mock.patch.object(
            install_agy_hud, "probe_tool_version", side_effect=[(16, 20, 0), (1, 0, 9)]
        ):
            errors, _ = install_agy_hud.check_prerequisites()
            self.assertEqual(2, len(errors))
            self.assertIn("node 16.20.0", errors[0])
            self.assertIn("agy 1.0.9", errors[1])

        with mock.patch.object(
            install_agy_hud, "probe_tool_version", side_effect=[None, None]
        ):
            errors, _ = install_agy_hud.check_prerequisites()
            self.assertEqual(2, len(errors))
            self.assertIn("node not found", errors[0])
            self.assertIn("agy not found", errors[1])

    def test_resolve_node_binary_falls_back_to_nvm_and_injects_path(self) -> None:
        fake_home = self.temp / "home"
        v18 = fake_home / ".nvm" / "versions" / "node" / "v18.20.0" / "bin" / "node"
        v20 = fake_home / ".nvm" / "versions" / "node" / "v20.19.4" / "bin" / "node"
        v18.parent.mkdir(parents=True)
        v20.parent.mkdir(parents=True)
        v18.write_text("", encoding="utf-8")
        v20.write_text("", encoding="utf-8")

        with (
            mock.patch.object(install, "is_windows", return_value=False),
            mock.patch.object(install_agy_hud.shutil, "which", return_value=None),
        ):
            found = install_agy_hud.resolve_node_binary(home=fake_home)
        self.assertEqual(v20, found)

        env = install_agy_hud.build_runtime_env(found)
        self.assertEqual(str(v20.parent), env["PATH"].split(os.pathsep)[0])

    def test_blocks_inside_active_agy_session_unless_overridden(self) -> None:
        clean_env = {k: v for k, v in os.environ.items() if k not in install_agy_hud.AGY_SESSION_ENV_VARS}
        clean_env["AGY_SESSION_ID"] = "sess-1"
        with mock.patch.dict(os.environ, clean_env, clear=True):
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                rc = install_agy_hud.main(["--dry-run"])
            self.assertEqual(1, rc)
            self.assertIn("AGY_SESSION_ID", stderr.getvalue())

            stdout = io.StringIO()
            with (
                mock.patch.object(install_agy_hud, "check_prerequisites", return_value=([], {})),
                mock.patch("sys.stdout", stdout),
            ):
                rc = install_agy_hud.main(["--dry-run", "--allow-inside-agy"])
            self.assertEqual(0, rc)
            self.assertIn("dry-run", stdout.getvalue())

    def test_dry_run_does_not_download_or_write_files(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch.object(install_agy_hud, "running_inside_agy_session", return_value=None),
            mock.patch.object(install_agy_hud, "check_prerequisites", return_value=([], {})),
            mock.patch.object(install_agy_hud, "download_script") as dl_mock,
            mock.patch("sys.stdout", stdout),
        ):
            rc = install_agy_hud.main(["--dry-run"])
        self.assertEqual(0, rc)
        dl_mock.assert_not_called()
        self.assertFalse(self.config_target.exists())
        self.assertIn("agy-hud upstream installer", stdout.getvalue())
        self.assertIn("AGY HUD config", stdout.getvalue())

    def test_skip_upstream_syncs_config_and_protects_conflict(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch.object(install_agy_hud, "download_script") as dl_mock,
            mock.patch("sys.stdout", stdout),
        ):
            rc = install_agy_hud.main(["--skip-upstream"])
        self.assertEqual(0, rc)
        dl_mock.assert_not_called()
        self.assertTrue(self.config_target.exists())
        loaded = json.loads(self.config_target.read_text(encoding="utf-8"))
        self.assertTrue(loaded["display"]["showSpeed"])

        # Replacing target with custom content causes CONFLICT and preserves file.
        self.config_target.unlink()
        self.config_target.write_text('{"display":{"showSpeed":false}}\n', encoding="utf-8")
        stdout2 = io.StringIO()
        with mock.patch("sys.stdout", stdout2):
            rc2 = install_agy_hud.main(["--skip-upstream"])
        self.assertEqual(1, rc2)
        self.assertIn("CONFLICT", stdout2.getvalue())
        self.assertIn('"showSpeed":false', self.config_target.read_text(encoding="utf-8"))

    def test_upstream_installer_runs_platform_script(self) -> None:
        fake_proc = mock.Mock(returncode=0)
        with (
            mock.patch.object(install_agy_hud, "running_inside_agy_session", return_value=None),
            mock.patch.object(install_agy_hud, "check_prerequisites", return_value=([], {"PATH": "/mock/bin"})),
            mock.patch.object(install, "is_windows", return_value=False),
            mock.patch.object(install_agy_hud, "download_script", return_value=b"#!/usr/bin/env bash\necho ok\n") as dl_mock,
            mock.patch.object(install_agy_hud.subprocess, "run", return_value=fake_proc) as run_mock,
            mock.patch("sys.stdout", io.StringIO()),
        ):
            rc = install_agy_hud.main([])
        self.assertEqual(0, rc)
        dl_mock.assert_called_once_with(install_agy_hud.AGY_HUD_POSIX_INSTALL_URL)
        called_cmd = run_mock.call_args[0][0]
        self.assertEqual("bash", called_cmd[0])
        self.assertTrue(called_cmd[1].endswith("install.sh"))
        self.assertEqual({"PATH": "/mock/bin"}, run_mock.call_args[1]["env"])
        self.assertTrue(self.config_target.exists())


if __name__ == "__main__":
    unittest.main()
