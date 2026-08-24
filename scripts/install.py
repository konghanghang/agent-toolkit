#!/usr/bin/env python3
"""Install Agent Toolkit skills, Codex agents, and managed Codex rules."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
COMMON_DIR = TOOLKIT_ROOT / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from private_files import (
    copy_windows_acl,
    is_windows,
    secure_private_file,
)


SKILLS_DIR = TOOLKIT_ROOT / "skills"
CODEX_AGENTS_SOURCE = TOOLKIT_ROOT / "codex" / "agents"
CODEX_RULES_SOURCE = TOOLKIT_ROOT / "codex" / "rules"
CODEX_PLAYWRIGHT_CONFIG_SOURCE = (
    TOOLKIT_ROOT / "codex" / "config" / "playwright-mcp.toml.template"
)
CODEX_SKILLS_TARGET = Path.home() / ".agents" / "skills"
CLAUDE_SKILLS_TARGET = Path.home() / ".claude" / "skills"
DEFAULT_CODEX_HOME = Path.home() / ".codex"
LEGACY_ROOTS = (
    Path.home() / ".shared-skills",
    TOOLKIT_ROOT,
)
RULE_MANAGED_MARKER = "# Managed by agent-toolkit."
DIRECTORY_LINK_STRATEGY = "directory-link"
COPY_STRATEGY = "copy"
TOML_MERGE_STRATEGY = "toml-merge"
AGENT_REQUIRED_KEYS = ("name", "description", "developer_instructions")
TOML_KEY_PATTERN = re.compile(r"(?m)^\s*([A-Za-z0-9_-]+)\s*=")
TOML_TABLE_PATTERN = re.compile(r"^\s*\[([^\[\]]+)]\s*(?:#.*)?$")
TOML_ANY_TABLE_PATTERN = re.compile(r"^\s*\[\[?[^\[\]]+]]?\s*(?:#.*)?$")
PLAYWRIGHT_MANAGED_KEYS = {
    "mcp_servers.playwright": (
        "default_tools_approval_mode",
        "disabled_tools",
    ),
    "mcp_servers.playwright.env": ("npm_config_cache",),
}
WINDOWS_JUNCTION_TARGET_ENV = "AGENT_TOOLKIT_JUNCTION_TARGET"
WINDOWS_JUNCTION_SOURCE_ENV = "AGENT_TOOLKIT_JUNCTION_SOURCE"
WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x0400
WINDOWS_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
SKILL_INITIALIZERS = {
    "verify-test-db": SKILLS_DIR / "verify-test-db" / "scripts" / "init_config.py",
}


@dataclass(frozen=True)
class InstallSpec:
    source: Path
    target: Path
    label: str
    strategy: str


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install shared skills, Codex agents, and Codex rules."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--codex-only",
        action="store_true",
        help="Install only Codex skills, agents, and rules",
    )
    mode.add_argument("--claude-only", action="store_true", help="Install only Claude Code skills")
    mode.add_argument("--rules-only", action="store_true", help="Install only Codex rules")
    mode.add_argument("--agents-only", action="store_true", help="Install only Codex agents")
    mode.add_argument(
        "--config-only",
        action="store_true",
        help="Install only managed Codex config",
    )
    parser.add_argument(
        "--codex-home",
        help="Install Codex agents and rules into this existing Codex home instead of ~/.codex",
    )
    parser.add_argument("--skill", action="append", default=[], help="Install only the named skill; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing")
    parser.add_argument(
        "--replace-managed",
        action="store_true",
        help="Replace managed links that point to a legacy toolkit root or an old layout",
    )
    args = parser.parse_args(argv)

    if args.claude_only and args.codex_home:
        parser.error("--codex-home cannot be used with --claude-only")
    if (args.rules_only or args.agents_only or args.config_only) and args.skill:
        parser.error(
            "--skill cannot be used with --rules-only, --agents-only, or --config-only"
        )

    return args


def available_skills() -> dict[str, Path]:
    if not SKILLS_DIR.is_dir():
        raise SystemExit(f"Skills directory does not exist: {SKILLS_DIR}")
    return {
        path.name: path
        for path in sorted(SKILLS_DIR.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def selected_skills(names: list[str]) -> dict[str, Path]:
    available = available_skills()
    if not names:
        return available
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise SystemExit(f"Unknown skill: {', '.join(unknown)}")
    return {name: available[name] for name in names}


def selected_skill_initializers(args: argparse.Namespace) -> list[Path]:
    if args.rules_only or args.agents_only or args.config_only:
        return []
    selected = selected_skills(args.skill)
    initializers: list[Path] = []
    for name in selected:
        initializer = SKILL_INITIALIZERS.get(name)
        if initializer is None:
            continue
        if not initializer.is_file():
            raise SystemExit(f"Skill initializer does not exist: {initializer}")
        initializers.append(initializer)
    return initializers


def run_skill_initializer(initializer: Path, args: argparse.Namespace) -> bool:
    command = [sys.executable, str(initializer)]
    if args.dry_run:
        command.append("--dry-run")
    sys.stdout.flush()
    sys.stderr.flush()
    result = subprocess.run(command)
    return result.returncode == 0


def available_codex_agents() -> list[Path]:
    if not CODEX_AGENTS_SOURCE.is_dir():
        raise SystemExit(f"Codex agents directory does not exist: {CODEX_AGENTS_SOURCE}")

    agents = sorted(CODEX_AGENTS_SOURCE.glob("*.toml"))
    if not agents:
        raise SystemExit(f"No Codex agents found in: {CODEX_AGENTS_SOURCE}")

    for agent in agents:
        try:
            keys = set(TOML_KEY_PATTERN.findall(agent.read_text(encoding="utf-8")))
        except (OSError, UnicodeError) as error:
            raise SystemExit(f"Unable to read Codex agent {agent}: {error}") from error

        missing = [key for key in AGENT_REQUIRED_KEYS if key not in keys]
        if missing:
            raise SystemExit(
                f"Codex agent {agent} is missing required fields: {', '.join(missing)}"
            )

    return agents


def resolve_link_target(target: Path) -> Path:
    raw = Path(os.readlink(target))
    if not raw.is_absolute():
        raw = target.parent / raw
    return raw.expanduser().resolve(strict=False)


class DirectoryLinkError(RuntimeError):
    pass


def is_windows_junction(path: Path) -> bool:
    if os.name != "nt" or path.is_symlink():
        return False

    native_check = getattr(path, "is_junction", None)
    if native_check is not None:
        try:
            return bool(native_check())
        except OSError:
            return False

    try:
        import ctypes

        get_attributes = ctypes.windll.kernel32.GetFileAttributesW
        get_attributes.argtypes = [ctypes.c_wchar_p]
        get_attributes.restype = ctypes.c_uint32
        attributes = get_attributes(str(path))
    except (AttributeError, OSError):
        return False
    return (
        attributes != WINDOWS_INVALID_FILE_ATTRIBUTES
        and bool(attributes & WINDOWS_REPARSE_POINT_ATTRIBUTE)
    )


def directory_link_kind(path: Path) -> Optional[str]:
    if path.is_symlink():
        return "symlink"
    if is_windows_junction(path):
        return "junction"
    return None


def resolve_directory_link(path: Path) -> Path:
    kind = directory_link_kind(path)
    if kind == "symlink":
        return resolve_link_target(path)
    if kind == "junction":
        return path.resolve(strict=False)
    raise DirectoryLinkError(f"not a managed directory link: {path}")


def remove_directory_link(path: Path) -> None:
    kind = directory_link_kind(path)
    if kind == "symlink":
        path.unlink()
        return
    if kind == "junction":
        path.rmdir()
        return
    raise DirectoryLinkError(f"refusing to remove non-link path: {path}")


def create_windows_junction(source: Path, target: Path) -> None:
    environment = os.environ.copy()
    environment[WINDOWS_JUNCTION_TARGET_ENV] = str(target)
    environment[WINDOWS_JUNCTION_SOURCE_ENV] = str(source)
    command = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            "New-Item -ItemType Junction "
            f"-Path $env:{WINDOWS_JUNCTION_TARGET_ENV} "
            f"-Target $env:{WINDOWS_JUNCTION_SOURCE_ENV} "
            "-ErrorAction Stop | Out-Null"
        ),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            env=environment,
        )
    except OSError as error:
        raise DirectoryLinkError(
            "Windows PowerShell is required to create a directory junction; "
            f"no files were copied ({error})"
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        raise DirectoryLinkError(
            "unable to create Windows directory junction; keep the toolkit and target "
            f"on local NTFS volumes, or inspect PowerShell availability and permissions ({detail})"
        )


def create_directory_link(source: Path, target: Path) -> None:
    if os.name == "nt":
        create_windows_junction(source, target)
        return
    target.symlink_to(source, target_is_directory=source.is_dir())


def is_managed_target(path: Path) -> bool:
    for root in LEGACY_ROOTS:
        try:
            path.relative_to(root.expanduser().resolve(strict=False))
            return True
        except ValueError:
            continue
    return False


def installs_codex_rules(args: argparse.Namespace) -> bool:
    return not args.claude_only and not args.agents_only and not args.config_only


def installs_codex_agents(args: argparse.Namespace) -> bool:
    return not args.claude_only and not args.rules_only and not args.config_only


def installs_codex_playwright_config(args: argparse.Namespace) -> bool:
    return not args.claude_only and not args.rules_only and not args.agents_only


def installs_codex_configuration(args: argparse.Namespace) -> bool:
    return (
        installs_codex_rules(args)
        or installs_codex_agents(args)
        or installs_codex_playwright_config(args)
    )


def resolve_codex_home(args: argparse.Namespace) -> tuple[Path, str]:
    if not args.codex_home:
        return DEFAULT_CODEX_HOME, "default"

    codex_home = Path(args.codex_home).expanduser().resolve(strict=False)
    if not codex_home.exists():
        raise SystemExit(f"Custom Codex home does not exist: {codex_home}")
    if not codex_home.is_dir():
        raise SystemExit(f"Custom Codex home is not a directory: {codex_home}")
    return codex_home, "custom"


def plan_installs(args: argparse.Namespace, codex_home: Optional[Path] = None) -> list[InstallSpec]:
    skills = (
        {}
        if args.rules_only or args.agents_only or args.config_only
        else selected_skills(args.skill)
    )
    specs: list[InstallSpec] = []

    if (
        not args.claude_only
        and not args.rules_only
        and not args.agents_only
        and not args.config_only
    ):
        specs.extend(
            InstallSpec(
                source,
                CODEX_SKILLS_TARGET / name,
                "Codex skill",
                DIRECTORY_LINK_STRATEGY,
            )
            for name, source in skills.items()
        )

    if installs_codex_agents(args):
        available_codex_agents()
        if codex_home is None:
            codex_home, _ = resolve_codex_home(args)
        specs.append(
            InstallSpec(
                CODEX_AGENTS_SOURCE,
                codex_home / "agents",
                "Codex agents",
                DIRECTORY_LINK_STRATEGY,
            )
        )

    if installs_codex_rules(args):
        if codex_home is None:
            codex_home, _ = resolve_codex_home(args)
        if CODEX_RULES_SOURCE.is_dir():
            specs.extend(
                InstallSpec(rule, codex_home / "rules" / rule.name, "Codex rule", COPY_STRATEGY)
                for rule in sorted(CODEX_RULES_SOURCE.glob("*.rules"))
            )

    if installs_codex_playwright_config(args):
        if codex_home is None:
            codex_home, _ = resolve_codex_home(args)
        if not CODEX_PLAYWRIGHT_CONFIG_SOURCE.is_file():
            raise SystemExit(
                "Codex Playwright config template does not exist: "
                f"{CODEX_PLAYWRIGHT_CONFIG_SOURCE}"
            )
        specs.append(
            InstallSpec(
                CODEX_PLAYWRIGHT_CONFIG_SOURCE,
                codex_home / "config.toml",
                "Codex Playwright MCP config",
                TOML_MERGE_STRATEGY,
            )
        )

    if (
        not args.codex_only
        and not args.rules_only
        and not args.agents_only
        and not args.config_only
    ):
        specs.extend(
            InstallSpec(
                source,
                CLAUDE_SKILLS_TARGET / name,
                "Claude Code skill",
                DIRECTORY_LINK_STRATEGY,
            )
            for name, source in skills.items()
        )

    return specs


def install_directory_link(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    source = spec.source.resolve()
    target = spec.target

    if os.path.lexists(target):
        if directory_link_kind(target) is None:
            return f"CONFLICT {spec.label}: {target} exists and is not a directory link", False

        try:
            existing = resolve_directory_link(target)
        except DirectoryLinkError as error:
            return f"CONFLICT {spec.label}: {error}", False
        if existing == source:
            return f"OK       {spec.label}: {target} -> {source}", True

        if not args.replace_managed or not is_managed_target(existing):
            return f"CONFLICT {spec.label}: {target} -> {existing}", False

        action = "REPLACE"
    else:
        action = "CREATE "

    message = f"{action} {spec.label}: {target} -> {source}"
    if args.dry_run:
        return message, True

    target.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(target):
        try:
            remove_directory_link(target)
        except (DirectoryLinkError, OSError) as error:
            return f"ERROR    {spec.label}: unable to replace {target} ({error})", False
    try:
        create_directory_link(source, target)
    except (DirectoryLinkError, OSError) as error:
        return f"ERROR    {spec.label}: {target} -> {source} ({error})", False
    return message, True


def is_managed_rule_file(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as file:
            return file.readline().rstrip("\n") == RULE_MANAGED_MARKER
    except (OSError, UnicodeError):
        return False


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(source.read_bytes())
        os.chmod(temporary_path, source.stat().st_mode & 0o777)
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class TomlMergeError(RuntimeError):
    pass


def render_playwright_config_template(source: Path) -> str:
    try:
        template = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise TomlMergeError(f"unable to read template {source}: {error}") from error

    profile_path = Path.home() / ".local" / "share" / "playwright-profile"
    npm_cache_path = Path.home() / ".local" / "share" / "playwright-mcp" / "npm-cache"
    rendered = template.replace(
        "{{PLAYWRIGHT_PROFILE_PATH}}",
        json.dumps(str(profile_path), ensure_ascii=False),
    ).replace(
        "{{PLAYWRIGHT_NPM_CACHE_PATH}}",
        json.dumps(str(npm_cache_path), ensure_ascii=False),
    )
    if "{{" in rendered or "}}" in rendered:
        raise TomlMergeError(f"unresolved placeholder in template {source}")
    return rendered


def find_toml_table_span(lines: list[str], table_name: str) -> Optional[tuple[int, int]]:
    headers = []
    for index, line in enumerate(lines):
        match = TOML_TABLE_PATTERN.match(line)
        if match and match.group(1).strip() == table_name:
            headers.append(index)

    if len(headers) > 1:
        raise TomlMergeError(f"duplicate TOML table [{table_name}]")
    if not headers:
        return None

    start = headers[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if TOML_ANY_TABLE_PATTERN.match(lines[index]):
            end = index
            break
    return start, end


def find_toml_key_line(
    lines: list[str],
    table_name: str,
    key: str,
) -> Optional[int]:
    span = find_toml_table_span(lines, table_name)
    if span is None:
        return None
    start, end = span
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    matches = [index for index in range(start, end) if pattern.match(lines[index])]
    if len(matches) > 1:
        raise TomlMergeError(f"duplicate key {key} in [{table_name}]")
    return matches[0] if matches else None


def desired_toml_key_lines(fragment: str) -> dict[str, dict[str, str]]:
    lines = fragment.splitlines()
    desired: dict[str, dict[str, str]] = {}
    for table_name, keys in PLAYWRIGHT_MANAGED_KEYS.items():
        if find_toml_table_span(lines, table_name) is None:
            raise TomlMergeError(f"template is missing [{table_name}]")
        desired[table_name] = {}
        for key in keys:
            index = find_toml_key_line(lines, table_name, key)
            if index is None:
                raise TomlMergeError(f"template is missing {key} in [{table_name}]")
            desired[table_name][key] = lines[index]
    return desired


def insert_toml_table(lines: list[str], table_name: str, key_lines: list[str]) -> None:
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(f"[{table_name}]")
    lines.extend(key_lines)


def merge_playwright_config(existing: str, fragment: str) -> str:
    lines = existing.splitlines()
    fragment_lines = fragment.splitlines()
    desired = desired_toml_key_lines(fragment)
    server_table = "mcp_servers.playwright"
    env_table = "mcp_servers.playwright.env"

    server_span = find_toml_table_span(lines, server_table)
    env_span = find_toml_table_span(lines, env_table)
    if server_span is None:
        if env_span is not None:
            raise TomlMergeError(f"[{env_table}] exists without [{server_table}]")
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(fragment_lines)
        return "\n".join(lines) + "\n"

    for table_name in PLAYWRIGHT_MANAGED_KEYS:
        span = find_toml_table_span(lines, table_name)
        if span is None:
            insert_toml_table(lines, table_name, list(desired[table_name].values()))
            continue

        missing_lines = []
        for key, desired_line in desired[table_name].items():
            existing_index = find_toml_key_line(lines, table_name, key)
            if existing_index is None:
                missing_lines.append(desired_line)
                continue
            existing_value = lines[existing_index].split("=", 1)[1].strip()
            desired_value = desired_line.split("=", 1)[1].strip()
            if existing_value != desired_value:
                raise TomlMergeError(
                    f"unmanaged value for {key} in [{table_name}]: {existing_value}"
                )

        if missing_lines:
            start, end = find_toml_table_span(lines, table_name) or (0, 0)
            insert_at = end
            while insert_at > start and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines[insert_at:insert_at] = missing_lines

    return "\n".join(lines) + "\n"


def atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
        if is_windows():
            if target.exists():
                copy_windows_acl(target, temporary_path)
            else:
                secure_private_file(temporary_path)
        else:
            mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def install_toml_merge(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    source = spec.source.resolve()
    target = spec.target
    if os.path.lexists(target) and (target.is_symlink() or not target.is_file()):
        return f"CONFLICT {spec.label}: {target} is not a regular file", False

    try:
        fragment = render_playwright_config_template(source)
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        merged = merge_playwright_config(existing, fragment)
    except (OSError, UnicodeError, TomlMergeError) as error:
        return f"CONFLICT {spec.label}: {target} ({error})", False

    if merged == existing:
        return f"OK       {spec.label}: {target} already contains managed settings", True

    action = "UPDATE " if target.exists() else "CREATE "
    message = f"{action} {spec.label}: {source} -> {target}"
    if args.dry_run:
        return message, True

    try:
        atomic_write_text(target, merged)
    except OSError as error:
        return f"ERROR    {spec.label}: unable to update {target} ({error})", False
    return message, True


def install_copy(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    source = spec.source.resolve()
    target = spec.target

    if not is_managed_rule_file(source):
        return f"CONFLICT {spec.label}: {source} is missing the managed marker", False

    if os.path.lexists(target):
        if target.is_symlink():
            existing = resolve_link_target(target)
            if existing == source:
                action = "MIGRATE"
            elif args.replace_managed and is_managed_target(existing):
                action = "REPLACE"
            else:
                return f"CONFLICT {spec.label}: {target} -> {existing}", False
        elif not target.is_file():
            return f"CONFLICT {spec.label}: {target} exists and is not a regular file", False
        elif target.read_bytes() == source.read_bytes():
            return f"OK       {spec.label}: {target} matches {source}", True
        elif is_managed_rule_file(target):
            action = "UPDATE "
        else:
            return f"CONFLICT {spec.label}: {target} exists and is not managed", False
    else:
        action = "COPY   "

    message = f"{action} {spec.label}: {source} -> {target}"
    if args.dry_run:
        return message, True

    atomic_copy(source, target)
    return message, True


def install_spec(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    if spec.strategy == DIRECTORY_LINK_STRATEGY:
        return install_directory_link(spec, args)
    if spec.strategy == COPY_STRATEGY:
        return install_copy(spec, args)
    if spec.strategy == TOML_MERGE_STRATEGY:
        return install_toml_merge(spec, args)
    return f"CONFLICT {spec.label}: unknown install strategy {spec.strategy}", False


def main() -> int:
    args = parse_args()
    codex_home = None
    if installs_codex_configuration(args):
        codex_home, home_kind = resolve_codex_home(args)
        print(f"Codex home: {codex_home} ({home_kind})")
        if installs_codex_agents(args):
            print(f"Agents target: {codex_home / 'agents'}")
        if installs_codex_rules(args):
            print(f"Rules target: {codex_home / 'rules'}")
        if installs_codex_playwright_config(args):
            print(f"Config target: {codex_home / 'config.toml'}")
        print()

    specs = plan_installs(args, codex_home)
    failures = 0

    for spec in specs:
        message, success = install_spec(spec, args)
        print(message)
        if not success:
            failures += 1

    if failures:
        print(f"\n{failures} conflict(s); no conflicting path was overwritten.", file=sys.stderr)
        return 1

    initializers = selected_skill_initializers(args)
    for initializer in initializers:
        if not run_skill_initializer(initializer, args):
            failures += 1

    if failures:
        print(f"\n{failures} skill initializer(s) failed.", file=sys.stderr)
        return 1

    mode = "dry-run" if args.dry_run else "complete"
    print(f"\nInstallation {mode}: {len(specs) + len(initializers)} item(s) checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
