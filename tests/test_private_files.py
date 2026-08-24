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

    def test_copy_windows_acl_uses_environment_paths_and_verifies_dacl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            source = directory / "existing.toml"
            target = directory / "replacement.toml"
            source.write_text("existing\n", encoding="utf-8")
            target.write_text("replacement\n", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with patch.dict(
                private_files.os.environ,
                {"PSModulePath": "C:\\Program Files\\PowerShell\\7\\Modules"},
            ), patch.object(
                private_files.subprocess,
                "run",
                return_value=completed,
            ) as run:
                private_files.copy_windows_acl(source, target)

            command = run.call_args.args[0]
            environment = run.call_args.kwargs["env"]
            self.assertEqual("powershell.exe", command[0])
            self.assertIn("Set-Acl -LiteralPath $target", command[-1])
            self.assertIn("GetSecurityDescriptorSddlForm", command[-1])
            self.assertNotIn(str(source), command[-1])
            self.assertNotIn(str(target), command[-1])
            self.assertEqual(
                str(source),
                environment[private_files.WINDOWS_ACL_SOURCE_ENV],
            )
            self.assertEqual(
                str(target),
                environment[private_files.WINDOWS_ACL_TARGET_ENV],
            )
            self.assertFalse(
                any(key.casefold() == "psmodulepath" for key in environment)
            )

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
