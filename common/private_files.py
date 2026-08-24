from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable


WINDOWS_PRIVATE_PATH_ENV = "AGENT_TOOLKIT_PRIVATE_PATH"
WINDOWS_ACL_SCRIPT = r"""
$ErrorActionPreference = "Stop"
$path = [Environment]::GetEnvironmentVariable("AGENT_TOOLKIT_PRIVATE_PATH", "Process")
if ([string]::IsNullOrWhiteSpace($path)) {
    throw "AGENT_TOOLKIT_PRIVATE_PATH is empty"
}

$item = Get-Item -LiteralPath $path -Force
$acl = Get-Acl -LiteralPath $path
$acl.SetAccessRuleProtection($true, $false)

$existingRules = @(
    $acl.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    )
)
foreach ($rule in $existingRules) {
    $acl.PurgeAccessRules($rule.IdentityReference)
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$system = [System.Security.Principal.SecurityIdentifier]::new("S-1-5-18")
$administrators = [System.Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
$trustedSids = @($currentUser, $system, $administrators)
$rights = [System.Security.AccessControl.FileSystemRights]::FullControl
$allow = [System.Security.AccessControl.AccessControlType]::Allow

foreach ($sid in $trustedSids) {
    if ($item.PSIsContainer) {
        $inheritance = [System.Security.AccessControl.InheritanceFlags](
            [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
        )
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            $rights,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            $allow
        )
    } else {
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            $rights,
            $allow
        )
    }
    [void]$acl.AddAccessRule($rule)
}

Set-Acl -LiteralPath $path -AclObject $acl

$actual = Get-Acl -LiteralPath $path
if (-not $actual.AreAccessRulesProtected) {
    throw "ACL inheritance is still enabled"
}
$trustedValues = @($trustedSids | ForEach-Object { $_.Value })
$unexpected = @(
    $actual.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    ) | Where-Object {
        $_.AccessControlType -ne $allow -or
        $trustedValues -notcontains $_.IdentityReference.Value
    }
)
if ($unexpected.Count -gt 0) {
    throw "ACL still contains an untrusted or deny rule"
}
foreach ($trustedSid in $trustedSids) {
    $fullControlRules = @(
        $actual.GetAccessRules(
            $true,
            $true,
            [System.Security.Principal.SecurityIdentifier]
        ) | Where-Object {
            $_.AccessControlType -eq $allow -and
            $_.IdentityReference.Value -eq $trustedSid.Value -and
            ($_.FileSystemRights -band $rights) -eq $rights
        }
    )
    if ($fullControlRules.Count -eq 0) {
        throw "ACL does not grant full control to a trusted principal"
    }
}
"""


class PrivatePathError(OSError):
    """A local private path could not be created or protected."""


def is_windows() -> bool:
    return os.name == "nt"


def _apply_windows_acl(path: Path) -> None:
    environment = os.environ.copy()
    # A pwsh parent can export PowerShell 7 module paths that Windows PowerShell
    # cannot load. Let powershell.exe rebuild its own default PSModulePath.
    for key in tuple(environment):
        if key.casefold() == "psmodulepath":
            environment.pop(key)
    environment[WINDOWS_PRIVATE_PATH_ENV] = str(path)
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                WINDOWS_ACL_SCRIPT,
            ],
            capture_output=True,
            text=True,
            errors="replace",
            env=environment,
            check=False,
        )
    except OSError as exc:
        raise PrivatePathError(f"无法调用 Windows ACL 工具保护 {path}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise PrivatePathError(f"无法收紧 Windows ACL: {path} ({detail})")


def secure_private_path(path: str | Path, *, directory: bool) -> Path:
    protected = Path(path)
    if protected.is_symlink():
        raise PrivatePathError(f"私有路径不得是符号链接: {protected}")
    if directory:
        if not protected.is_dir():
            raise PrivatePathError(f"私有目录不存在或不是目录: {protected}")
    elif not protected.is_file():
        raise PrivatePathError(f"私有文件不存在或不是普通文件: {protected}")

    if is_windows():
        _apply_windows_acl(protected)
    else:
        protected.chmod(0o700 if directory else 0o600)
    return protected


def secure_private_directory(path: str | Path) -> Path:
    directory = Path(path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return secure_private_path(directory, directory=True)
    except PrivatePathError:
        raise
    except OSError as exc:
        raise PrivatePathError(f"无法创建或保护私有目录: {directory} ({exc})") from exc


def secure_private_file(path: str | Path) -> Path:
    protected = Path(path)
    try:
        return secure_private_path(protected, directory=False)
    except PrivatePathError:
        raise
    except OSError as exc:
        raise PrivatePathError(f"无法保护私有文件: {protected} ({exc})") from exc


def _replace_file_w(replaced: Path, replacement: Path, backup: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    replace_file.restype = wintypes.BOOL
    if replace_file(str(replaced), str(replacement), str(backup), 0, None, None):
        return
    raise ctypes.WinError(ctypes.get_last_error())


def replace_windows_file_preserving_acl(
    replaced: str | Path,
    replacement: str | Path,
) -> Path:
    replaced_path = Path(replaced)
    replacement_path = Path(replacement)
    if not is_windows():
        raise PrivatePathError("保留 Windows ACL 的文件替换只能在 Windows 上运行")
    if replaced_path.is_symlink() or not replaced_path.is_file():
        raise PrivatePathError(f"被替换路径不存在或不是普通文件: {replaced_path}")
    if replacement_path.is_symlink() or not replacement_path.is_file():
        raise PrivatePathError(f"替换路径不存在或不是普通文件: {replacement_path}")

    with tempfile.NamedTemporaryFile(
        dir=replaced_path.parent,
        prefix=f".{replaced_path.name}.",
        suffix=".bak",
        delete=False,
    ) as backup_file:
        backup_path = Path(backup_file.name)
    backup_path.unlink()

    try:
        _replace_file_w(replaced_path, replacement_path, backup_path)
    except OSError as exc:
        recovery_error = None
        if backup_path.exists():
            try:
                os.replace(backup_path, replaced_path)
            except OSError as restore_exc:
                recovery_error = restore_exc
        recovery_detail = (
            f"; 旧文件恢复失败: {recovery_error}" if recovery_error else ""
        )
        raise PrivatePathError(
            f"无法在保留 Windows ACL 的情况下替换文件: "
            f"{replaced_path} <- {replacement_path} ({exc}{recovery_detail})"
        ) from exc

    try:
        backup_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        recovery_error = None
        try:
            os.replace(backup_path, replaced_path)
        except OSError as restore_exc:
            recovery_error = restore_exc
        recovery_detail = (
            f"; 旧文件恢复失败: {recovery_error}" if recovery_error else ""
        )
        raise PrivatePathError(
            f"Windows 文件替换后无法删除临时备份: {backup_path} "
            f"({exc}{recovery_detail})"
        ) from exc
    return replaced_path


def atomic_write_private_bytes(path: str | Path, content: bytes) -> Path:
    target = Path(path)
    secure_private_directory(target.parent)
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary_path = Path(raw_path)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        secure_private_file(temporary_path)
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
    return target


def atomic_write_private_text(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    return atomic_write_private_bytes(path, content.encode(encoding))


def atomic_write_private_json(
    path: str | Path,
    payload: Any,
    *,
    default: Callable[[Any], Any] | None = None,
) -> Path:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        default=default,
    ) + "\n"
    return atomic_write_private_text(path, content)
