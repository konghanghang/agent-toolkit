from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "common" / "private_files.py"


def load_module():
    spec = importlib.util.spec_from_file_location("private_files_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


private_files = load_module()


def windows_dacl_sddl(path: Path) -> str:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.casefold() == "psmodulepath":
            environment.pop(key)
    environment[private_files.WINDOWS_PRIVATE_PATH_ENV] = str(path)
    script = r"""
$ErrorActionPreference = "Stop"
$path = [Environment]::GetEnvironmentVariable("AGENT_TOOLKIT_PRIVATE_PATH", "Process")
$sections = [System.Security.AccessControl.AccessControlSections]::Access
(Get-Acl -LiteralPath $path).GetSecurityDescriptorSddlForm($sections)
"""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        env=environment,
        check=True,
    )
    return result.stdout.strip()


class PrivateFilesTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not Windows ACLs")
    def test_existing_posix_file_and_directory_are_tightened(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "private"
            directory.mkdir(mode=0o755)
            path = directory / "credentials.json"
            path.write_text("{}\n", encoding="utf-8")
            directory.chmod(0o755)
            path.chmod(0o644)

            private_files.secure_private_directory(directory)
            private_files.secure_private_file(path)

            self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not Windows ACLs")
    def test_atomic_json_uses_private_posix_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "private"
            path = directory / "session.json"

            private_files.atomic_write_private_json(path, {"token": "secret"})

            self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(
                {"token": "secret"},
                json.loads(path.read_text(encoding="utf-8")),
            )

    def test_acl_failure_preserves_previous_file_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            path = directory / "session.json"
            path.write_text('{"token":"previous"}\n', encoding="utf-8")

            def fail_for_file(candidate: Path) -> None:
                if candidate != directory:
                    raise private_files.PrivatePathError("ACL denied")

            with patch.object(private_files, "is_windows", return_value=True), patch.object(
                private_files,
                "_apply_windows_acl",
                side_effect=fail_for_file,
            ):
                with self.assertRaisesRegex(
                    private_files.PrivatePathError, "ACL denied"
                ):
                    private_files.atomic_write_private_json(
                        path, {"token": "replacement"}
                    )

            self.assertEqual(
                {"token": "previous"},
                json.loads(path.read_text(encoding="utf-8")),
            )
            self.assertEqual([], list(directory.glob(".session.json.*.tmp")))

    def test_windows_acl_uses_powershell_with_path_outside_command_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "credentials.json"
            path.write_text("{}\n", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with patch.dict(
                private_files.os.environ,
                {"PSModulePath": "C:\\Program Files\\PowerShell\\7\\Modules"},
            ), patch.object(
                private_files, "is_windows", return_value=True
            ), patch.object(
                private_files.subprocess,
                "run",
                return_value=completed,
            ) as run:
                private_files.secure_private_file(path)

            command = run.call_args.args[0]
            environment = run.call_args.kwargs["env"]
            self.assertEqual("powershell.exe", command[0])
            self.assertIn("SetAccessRuleProtection($true, $false)", command[-1])
            self.assertIn("$_.AccessControlType -ne $allow", command[-1])
            self.assertIn("foreach ($trustedSid in $trustedSids)", command[-1])
            self.assertNotIn(str(path), command[-1])
            self.assertEqual(
                str(path),
                environment[private_files.WINDOWS_PRIVATE_PATH_ENV],
            )
            self.assertFalse(
                any(key.casefold() == "psmodulepath" for key in environment)
            )

    def test_windows_file_replace_uses_native_replacefile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            replaced = directory / "existing.toml"
            replacement = directory / "replacement.toml"
            replaced.write_text("existing\n", encoding="utf-8")
            replacement.write_text("replacement\n", encoding="utf-8")

            def replace_file(
                replaced_path: Path,
                replacement_path: Path,
                backup_path: Path,
            ) -> None:
                os.replace(replaced_path, backup_path)
                os.replace(replacement_path, replaced_path)

            with patch.object(
                private_files, "is_windows", return_value=True
            ), patch.object(
                private_files, "_replace_file_w", side_effect=replace_file
            ) as native_replace:
                result = private_files.replace_windows_file_preserving_acl(
                    replaced, replacement
                )

            self.assertEqual(replaced, result)
            native_replace.assert_called_once()
            native_replaced, native_replacement, native_backup = (
                native_replace.call_args.args
            )
            self.assertEqual(replaced, native_replaced)
            self.assertEqual(replacement, native_replacement)
            self.assertEqual(replaced.parent, native_backup.parent)
            self.assertTrue(native_backup.name.endswith(".bak"))
            self.assertEqual(
                "replacement\n", replaced.read_text(encoding="utf-8")
            )
            self.assertFalse(replacement.exists())
            self.assertFalse(native_backup.exists())

    def test_windows_file_replace_failure_keeps_both_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            replaced = directory / "existing.toml"
            replacement = directory / "replacement.toml"
            replaced.write_text("existing\n", encoding="utf-8")
            replacement.write_text("replacement\n", encoding="utf-8")

            def fail_after_backup(
                replaced_path: Path,
                replacement_path: Path,
                backup_path: Path,
            ) -> None:
                os.replace(replaced_path, backup_path)
                raise OSError("ACL denied")

            with patch.object(
                private_files, "is_windows", return_value=True
            ), patch.object(
                private_files, "_replace_file_w", side_effect=fail_after_backup
            ):
                with self.assertRaisesRegex(
                    private_files.PrivatePathError, "ACL denied"
                ):
                    private_files.replace_windows_file_preserving_acl(
                        replaced, replacement
                    )

            self.assertEqual("existing\n", replaced.read_text(encoding="utf-8"))
            self.assertEqual(
                "replacement\n", replacement.read_text(encoding="utf-8")
            )
            self.assertEqual([], list(directory.glob(".*.bak")))

    @unittest.skipUnless(os.name == "nt", "requires real Windows ACL support")
    def test_real_windows_file_replace_preserves_inherited_and_private_dacls(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            for private in (False, True):
                with self.subTest(private=private):
                    replaced = directory / f"existing-{private}.toml"
                    replacement = directory / f"replacement-{private}.toml"
                    replaced.write_text("existing\n", encoding="utf-8")
                    replacement.write_text("replacement\n", encoding="utf-8")
                    if private:
                        private_files.secure_private_file(replaced)
                    expected_dacl = windows_dacl_sddl(replaced)

                    private_files.replace_windows_file_preserving_acl(
                        replaced, replacement
                    )

                    self.assertEqual(
                        "replacement\n", replaced.read_text(encoding="utf-8")
                    )
                    self.assertFalse(replacement.exists())
                    self.assertEqual(expected_dacl, windows_dacl_sddl(replaced))

    @unittest.skipUnless(os.name == "nt", "requires real Windows ACL support")
    def test_real_windows_acl_is_applied_to_directory_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "private"
            private_files.secure_private_directory(directory)
            path = directory / "session.json"
            path.write_text("{}\n", encoding="utf-8")

            private_files.secure_private_file(path)

            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
