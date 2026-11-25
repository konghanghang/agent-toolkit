#!/usr/bin/env python3
"""Optional installer for icebear0828/agy-hud statusline and its managed config."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = TOOLKIT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import install  # noqa: E402

AGY_HUD_POSIX_INSTALL_URL = (
    "https://raw.githubusercontent.com/icebear0828/agy-hud/main/scripts/install.sh"
)
AGY_HUD_WINDOWS_INSTALL_URL = (
    "https://raw.githubusercontent.com/icebear0828/agy-hud/main/scripts/install.ps1"
)
MIN_NODE_VERSION = (18, 0, 0)
MIN_AGY_VERSION = (1, 1, 0)
VERSION_PATTERN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
# 识别是否正运行在活跃的 AGY 会话内，避免在会话中直接改写 settings.json。
AGY_SESSION_ENV_VARS = (
    "ANTIGRAVITY_CLI_SESSION",
    "ANTIGRAVITY_CONVERSATION_ID",
    "AGY_SESSION_ID",
)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install optional icebear0828/agy-hud statusline for Antigravity CLI (agy) "
            "and link its managed user config."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prerequisite checks and planned actions without downloading or writing",
    )
    parser.add_argument(
        "--skip-upstream",
        action="store_true",
        help="Skip running the upstream agy-hud installer and only sync managed config",
    )
    parser.add_argument(
        "--replace-managed",
        action="store_true",
        help="Replace an existing managed config link pointing to a legacy toolkit root",
    )
    parser.add_argument(
        "--allow-inside-agy",
        action="store_true",
        help="Allow running even when an active AGY session environment variable is detected",
    )
    return parser.parse_args(argv)


def parse_semver(text: str) -> Optional[tuple[int, int, int]]:
    match = VERSION_PATTERN.search(text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def format_semver(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def probe_tool_version(command: list[str], env: Optional[dict[str, str]] = None) -> Optional[tuple[int, int, int]]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    combined = f"{result.stdout}\n{result.stderr}"
    return parse_semver(combined)


def _nvm_node_candidates(home: Path) -> list[Path]:
    versions_dir = home / ".nvm" / "versions" / "node"
    if not versions_dir.is_dir():
        return []
    candidates: list[tuple[tuple[int, int, int], Path]] = []
    for entry in versions_dir.iterdir():
        node_bin = entry / "bin" / "node"
        ver = parse_semver(entry.name)
        if ver is not None and node_bin.is_file():
            candidates.append((ver, node_bin))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates]


def resolve_node_binary(home: Optional[Path] = None) -> Optional[Path]:
    """Locate node from PATH first, then common version managers (e.g. lazy-loaded nvm)."""
    found = shutil.which("node")
    if found:
        return Path(found)

    user_home = home or Path.home()
    if install.is_windows():
        candidates = [
            user_home / ".volta" / "bin" / "node.exe",
            Path(os.environ.get("NVM_SYMLINK", "")) / "node.exe",
            Path("C:/Program Files/nodejs/node.exe"),
        ]
    else:
        candidates = [
            *_nvm_node_candidates(user_home),
            user_home / ".volta" / "bin" / "node",
            user_home / ".fnm" / "aliases" / "default" / "bin" / "node",
            user_home / ".asdf" / "shims" / "node",
            user_home / ".local" / "share" / "mise" / "shims" / "node",
            Path("/opt/homebrew/bin/node"),
            Path("/usr/local/bin/node"),
        ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def build_runtime_env(node_binary: Optional[Path]) -> dict[str, str]:
    env = os.environ.copy()
    if node_binary is None:
        return env
    node_dir = str(node_binary.parent)
    current_path = env.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []
    if node_dir not in path_parts:
        env["PATH"] = f"{node_dir}{os.pathsep}{current_path}" if current_path else node_dir
    return env


def running_inside_agy_session() -> Optional[str]:
    for key in AGY_SESSION_ENV_VARS:
        if os.environ.get(key):
            return key
    return None


def check_prerequisites() -> tuple[list[str], dict[str, str]]:
    errors: list[str] = []
    node_binary = resolve_node_binary()
    env = build_runtime_env(node_binary)
    node_cmd = [str(node_binary), "--version"] if node_binary else ["node", "--version"]
    node_ver = probe_tool_version(node_cmd, env=env)
    if node_ver is None:
        errors.append("node not found; Node.js >= 18.0.0 is required for agy-hud")
    elif node_ver < MIN_NODE_VERSION:
        errors.append(
            f"node {format_semver(node_ver)} found; expected >= {format_semver(MIN_NODE_VERSION)}"
        )

    agy_ver = probe_tool_version(["agy", "--version"], env=env)
    if agy_ver is None:
        errors.append("agy not found; Antigravity CLI >= 1.1.0 is required for agy-hud")
    elif agy_ver < MIN_AGY_VERSION:
        errors.append(
            f"agy {format_semver(agy_ver)} found; expected >= {format_semver(MIN_AGY_VERSION)}"
        )
    return errors, env


def upstream_install_url() -> str:
    return AGY_HUD_WINDOWS_INSTALL_URL if install.is_windows() else AGY_HUD_POSIX_INSTALL_URL


def download_script(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "agent-toolkit-install-agy-hud"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def run_upstream_installer(dry_run: bool, env: Optional[dict[str, str]] = None) -> tuple[str, bool]:
    url = upstream_install_url()
    if dry_run:
        runner = "powershell -NoProfile -ExecutionPolicy Bypass -File" if install.is_windows() else "bash"
        return f"RUN      agy-hud upstream installer: download {url} and run via `{runner}`", True

    suffix = ".ps1" if install.is_windows() else ".sh"
    try:
        script_bytes = download_script(url)
    except (urllib.error.URLError, OSError) as error:
        return f"ERROR    agy-hud upstream installer: failed to download {url} ({error})", False

    with tempfile.TemporaryDirectory(prefix="agy-hud-install-") as temp_dir:
        script_path = Path(temp_dir) / f"install{suffix}"
        script_path.write_bytes(script_bytes)
        if install.is_windows():
            shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
            cmd = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
        else:
            cmd = ["bash", str(script_path)]

        try:
            result = subprocess.run(cmd, env=env)
        except OSError as error:
            return f"ERROR    agy-hud upstream installer: failed to run {cmd[0]} ({error})", False

        if result.returncode != 0:
            return (
                f"ERROR    agy-hud upstream installer: exited with code {result.returncode}",
                False,
            )

    return f"INSTALL  agy-hud upstream installer: {url}", True


def sync_managed_config(args: argparse.Namespace) -> tuple[str, bool]:
    source = install.AGY_HUD_SOURCE / "config.json"
    if not source.is_file():
        return f"ERROR    AGY HUD config: source file missing ({source})", False
    spec = install.InstallSpec(
        source,
        install.AGY_HUD_CONFIG_TARGET,
        "AGY HUD config",
        install.FILE_LINK_STRATEGY,
        backup_group="agy-hud",
    )
    install_args = argparse.Namespace(
        dry_run=args.dry_run,
        replace_managed=args.replace_managed,
    )
    return install.install_file_link(spec, install_args)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if not args.skip_upstream and not args.allow_inside_agy:
        env_key = running_inside_agy_session()
        if env_key:
            print(
                f"ERROR: detected active AGY session ({env_key}); run this script from a normal "
                "terminal shell outside agy (or pass --allow-inside-agy to override).",
                file=sys.stderr,
            )
            return 1

    if not args.skip_upstream:
        errors, runtime_env = check_prerequisites()
        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1

        message, ok = run_upstream_installer(args.dry_run, env=runtime_env)
        print(message)
        if not ok:
            return 1

    config_message, config_ok = sync_managed_config(args)
    print(config_message)
    if not config_ok:
        return 1

    mode = "dry-run" if args.dry_run else "complete"
    print(f"\nAGY HUD installation {mode}. Open a fresh `agy` session to verify the status line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
