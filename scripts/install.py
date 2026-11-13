#!/usr/bin/env python3
"""Install Agent Toolkit skills, Codex agents and instructions, OpenCode config, and managed Codex rules."""

from __future__ import annotations

import argparse
import hashlib
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


PROFILE_MANIFEST = TOOLKIT_ROOT / "install-profiles.json"
PROFILE_STATE = Path.home() / ".local" / "agent-toolkit" / "install-profile.json"
LEGACY_PROFILE_STATE = TOOLKIT_ROOT / ".local" / "install-profile.json"
SKILLS_DIR = TOOLKIT_ROOT / "skills"
CODEX_AGENTS_SOURCE = TOOLKIT_ROOT / "codex" / "agents"
CODEX_RULES_SOURCE = TOOLKIT_ROOT / "codex" / "rules"
# Shared by every agent; only the generated target lives in a Codex home.
INSTRUCTIONS_SOURCE = TOOLKIT_ROOT / "instructions"
LEGACY_INSTRUCTIONS_SOURCE = TOOLKIT_ROOT / "codex" / "instructions"
CODEX_PLAYWRIGHT_CONFIG_SOURCE = (
    TOOLKIT_ROOT / "codex" / "config" / "playwright-mcp.toml.template"
)
CODEX_SKILLS_TARGET = Path.home() / ".agents" / "skills"
CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_SKILLS_TARGET = CLAUDE_HOME / "skills"
CLAUDE_AGENTS_SOURCE = TOOLKIT_ROOT / "claude" / "agents"
CLAUDE_AGENT_GUARD = CLAUDE_AGENTS_SOURCE / "hooks" / "bash_guard.py"
CLAUDE_AGENT_REQUIRED_KEYS = ("name", "description", "hooks")
CLAUDE_SETTINGS = CLAUDE_HOME / "settings.json"
CCLINE_SOURCE = TOOLKIT_ROOT / "claude" / "ccline"
CCLINE_HOME = CLAUDE_HOME / "ccline"
CCLINE_VERSION = "2.0.1"
CCLINE_INSTALL_COMMAND = f"npm install -g @cometix/ccline@{CCLINE_VERSION}"
# Keys from claude/settings*.json that point at the ccline binary.
CCLINE_STATUS_LINE_KEYS = ("statusLine", "subagentStatusLine")
CLAUDE_SETTINGS_SOURCE = TOOLKIT_ROOT / "claude"
DEFAULT_CODEX_HOME = Path.home() / ".codex"
OPENCODE_SOURCE = TOOLKIT_ROOT / "opencode"
OPENCODE_AGENTS_SOURCE = OPENCODE_SOURCE / "agents"
OPENCODE_CONFIG_SOURCE = OPENCODE_SOURCE / "opencode.json"
DEFAULT_OPENCODE_HOME = Path.home() / ".config" / "opencode"
FILE_LINK_STRATEGY = "file-link"
OPENCODE_AGENT_REQUIRED_KEYS = ("description", "mode")
LEGACY_ROOTS = (
    Path.home() / ".shared-skills",
    TOOLKIT_ROOT,
)
RULE_MANAGED_MARKER = "# Managed by agent-toolkit."
DIRECTORY_LINK_STRATEGY = "directory-link"
COPY_STRATEGY = "copy"
TOML_MERGE_STRATEGY = "toml-merge"
JSON_SETTINGS_MERGE_STRATEGY = "json-settings-merge"
INSTRUCTIONS_STRATEGY = "instructions"
INSTRUCTIONS_MARKER_PATTERN = re.compile(
    r"^<!-- Managed by agent-toolkit\. .* sha256=([0-9a-f]{64}) -->$"
)
AGENT_REQUIRED_KEYS = ("name", "description", "developer_instructions")
TOML_KEY_PATTERN = re.compile(r"(?m)^\s*([A-Za-z0-9_-]+)\s*=")
FRONTMATTER_KEY_PATTERN = re.compile(r"(?m)^([A-Za-z0-9_-]+):")
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
    "gitlab-run-pipeline": SKILLS_DIR / "gitlab-run-pipeline" / "scripts" / "init_config.py",
    "verify-test-db": SKILLS_DIR / "verify-test-db" / "scripts" / "init_config.py",
}


@dataclass(frozen=True)
class InstallSpec:
    source: Path
    target: Path
    label: str
    strategy: str
    sources: tuple[Path, ...] = ()
    backup_group: str = "opencode"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install shared skills, Codex agents and instructions, OpenCode config, and Codex rules."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--codex-only",
        action="store_true",
        help="Install only Codex skills, agents, instructions, and rules",
    )
    mode.add_argument(
        "--claude-only",
        action="store_true",
        help="Install only Claude Code skills, agents, instructions, and ccline status line",
    )
    mode.add_argument("--rules-only", action="store_true", help="Install only Codex rules")
    mode.add_argument("--agents-only", action="store_true", help="Install only Codex agents")
    mode.add_argument(
        "--opencode-only",
        action="store_true",
        help="Install only OpenCode config and agents",
    )
    mode.add_argument(
        "--config-only",
        action="store_true",
        help="Install only managed Codex config",
    )
    parser.add_argument(
        "--codex-home",
        help="Install Codex agents and rules into this existing Codex home instead of ~/.codex",
    )
    parser.add_argument(
        "--profile",
        choices=("general", "work"),
        help="general: personal tools; work: general plus work extensions",
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
    if (args.rules_only or args.agents_only or args.config_only or args.opencode_only) and args.skill:
        parser.error(
            "--skill cannot be used with --rules-only, --agents-only, --config-only, or --opencode-only"
        )
    if args.opencode_only and args.codex_home:
        parser.error("--codex-home cannot be used with --opencode-only")

    return args


def available_skills() -> dict[str, Path]:
    if not SKILLS_DIR.is_dir():
        raise SystemExit(f"Skills directory does not exist: {SKILLS_DIR}")
    return {
        path.name: path
        for path in sorted(SKILLS_DIR.iterdir())
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def resolve_profile(args: argparse.Namespace) -> str:
    if args.profile is not None:
        return args.profile
    try:
        for state_path in (PROFILE_STATE, LEGACY_PROFILE_STATE):
            try:
                state_path.lstat()
            except FileNotFoundError:
                continue
            break
        else:
            raise SystemExit("Choose an installation profile: --profile general or --profile work")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (
            not isinstance(state, dict)
            or type(state.get("schema_version")) is not int
            or state["schema_version"] != 1
        ):
            raise ValueError("invalid profile state")
        profile = state.get("profile")
        if profile not in ("general", "work"):
            raise ValueError("invalid saved profile")
    except (OSError, ValueError) as error:
        raise SystemExit(f"Unable to read {state_path}: {error}") from error
    args.profile = profile
    return profile


def read_profile_manifest() -> dict:
    try:
        data = json.loads(PROFILE_MANIFEST.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("unsupported schema_version")
        for section, present in (
            ("skills", set(available_skills())),
            ("rules", {p.name for p in CODEX_RULES_SOURCE.glob("*.rules")}),
            ("instructions", {p.name for p in INSTRUCTIONS_SOURCE.glob("*.md")}),
            ("claude_settings", {p.name for p in CLAUDE_SETTINGS_SOURCE.glob("settings*.json")}),
        ):
            entries = data.get(section)
            if not isinstance(entries, dict) or any(
                value not in ("general", "work") for value in entries.values()
            ):
                raise ValueError(f"{section} must map names to general or work")
            missing = present - entries.keys()
            if missing:
                raise ValueError(f"Unclassified {section}: {', '.join(sorted(missing))}")
        return data
    except (OSError, ValueError) as error:
        raise SystemExit(f"Invalid profile manifest {PROFILE_MANIFEST}: {error}") from error


def save_profile(profile: str) -> None:
    atomic_write_text(
        PROFILE_STATE,
        json.dumps({"schema_version": 1, "profile": profile}, indent=2) + "\n",
    )


def selected_skills(args: argparse.Namespace) -> dict[str, Path]:
    profile = resolve_profile(args)
    categories = read_profile_manifest()["skills"]
    available = available_skills()
    names = args.skill
    unknown = sorted(set(names) - set(available))
    if unknown:
        raise SystemExit(f"Unknown or unavailable skill: {', '.join(unknown)}")
    excluded = [name for name in names if profile == "general" and categories[name] == "work"]
    if excluded:
        raise SystemExit(f"Skills outside general profile: {', '.join(excluded)}; use --profile work")
    return {
        name: available[name] for name in (names or available)
        if profile == "work" or categories[name] == "general"
    }


def selected_skill_initializers(args: argparse.Namespace) -> list[Path]:
    if args.rules_only or args.agents_only or args.config_only or args.opencode_only:
        return []
    selected = selected_skills(args)
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


def available_claude_agents() -> list[Path]:
    if not CLAUDE_AGENTS_SOURCE.is_dir():
        raise SystemExit(f"Claude Code agents directory does not exist: {CLAUDE_AGENTS_SOURCE}")
    if not CLAUDE_AGENT_GUARD.is_file():
        raise SystemExit(f"Claude Code agent Bash guard does not exist: {CLAUDE_AGENT_GUARD}")

    agents = sorted(CLAUDE_AGENTS_SOURCE.glob("*.md"))
    if not agents:
        raise SystemExit(f"No Claude Code agents found in: {CLAUDE_AGENTS_SOURCE}")

    for agent in agents:
        try:
            parts = agent.read_text(encoding="utf-8").split("---", 2)
        except (OSError, UnicodeError) as error:
            raise SystemExit(f"Unable to read Claude Code agent {agent}: {error}") from error
        if len(parts) != 3 or parts[0].strip() or not parts[2].strip():
            raise SystemExit(f"Claude Code agent {agent} is missing frontmatter or instructions")
        keys = set(FRONTMATTER_KEY_PATTERN.findall(parts[1]))
        missing = [key for key in CLAUDE_AGENT_REQUIRED_KEYS if key not in keys]
        if missing:
            raise SystemExit(
                f"Claude Code agent {agent} is missing required fields: {', '.join(missing)}"
            )

    return agents


def available_opencode_agents() -> list[Path]:
    if not OPENCODE_AGENTS_SOURCE.is_dir():
        raise SystemExit(f"OpenCode agents directory does not exist: {OPENCODE_AGENTS_SOURCE}")
    if not OPENCODE_CONFIG_SOURCE.is_file():
        raise SystemExit(f"OpenCode config does not exist: {OPENCODE_CONFIG_SOURCE}")

    agents = sorted(OPENCODE_AGENTS_SOURCE.glob("*.md"))
    if not agents:
        raise SystemExit(f"No OpenCode agents found in: {OPENCODE_AGENTS_SOURCE}")

    for agent in agents:
        try:
            text = agent.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SystemExit(f"Unable to read OpenCode agent {agent}: {error}") from error
        if not text.startswith("---\n"):
            raise SystemExit(f"OpenCode agent {agent} is missing YAML frontmatter")
        end = text.find("\n---", 4)
        if end < 0:
            raise SystemExit(f"OpenCode agent {agent} has unclosed YAML frontmatter")
        frontmatter = text[4:end]
        keys = set(FRONTMATTER_KEY_PATTERN.findall(frontmatter))
        missing = [key for key in OPENCODE_AGENT_REQUIRED_KEYS if key not in keys]
        if missing:
            raise SystemExit(
                f"OpenCode agent {agent} is missing required fields: {', '.join(missing)}"
            )
        if not text[end + 4 :].strip():
            raise SystemExit(f"OpenCode agent {agent} is missing instructions")

    return agents


def resolve_link_target(target: Path) -> Path:
    link = os.readlink(target)
    if is_windows():
        # readlink() includes the Win32 namespace prefix, which resolve()
        # preserves when it was already present in its input.
        if link.startswith("\\\\?\\UNC\\"):
            link = "\\\\" + link[8:]
        elif link.startswith("\\\\?\\"):
            link = link[4:]
    raw = Path(link)
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
    return (
        not args.claude_only
        and not args.agents_only
        and not args.config_only
        and not args.opencode_only
    )


def installs_codex_agents(args: argparse.Namespace) -> bool:
    return (
        not args.claude_only
        and not args.rules_only
        and not args.config_only
        and not args.opencode_only
    )


def installs_claude(args: argparse.Namespace) -> bool:
    return (
        not args.codex_only
        and not args.rules_only
        and not args.agents_only
        and not args.config_only
        and not args.opencode_only
    )


def installs_codex_instructions(args: argparse.Namespace) -> bool:
    return (
        not args.claude_only
        and not args.rules_only
        and not args.agents_only
        and not args.config_only
        and not args.opencode_only
    )


def selected_profile_files(
    profile: str, categories: dict[str, str], root: Path, label: str
) -> tuple[Path, ...]:
    """General files come first; work files extend them and never replace them."""
    names = sorted(
        (name for name, category in categories.items()
         if profile == "work" or category == "general"),
        key=lambda name: (categories[name] != "general", name),
    )
    missing = [name for name in names if not (root / name).is_file()]
    if missing:
        raise SystemExit(f"{label} do not exist: {', '.join(missing)}")
    return tuple(root / name for name in names)


def selected_instructions(profile: str, categories: dict[str, str]) -> tuple[Path, ...]:
    return selected_profile_files(profile, categories, INSTRUCTIONS_SOURCE, "Codex instructions")


def installs_codex_playwright_config(args: argparse.Namespace) -> bool:
    return (
        not args.claude_only
        and not args.rules_only
        and not args.agents_only
        and not args.opencode_only
    )


def installs_opencode(args: argparse.Namespace) -> bool:
    return (
        not args.claude_only
        and not args.codex_only
        and not args.rules_only
        and not args.agents_only
        and not args.config_only
    )


def installs_codex_configuration(args: argparse.Namespace) -> bool:
    return (
        installs_codex_rules(args)
        or installs_codex_agents(args)
        or installs_codex_instructions(args)
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
    profile = resolve_profile(args)
    categories = read_profile_manifest()
    skills = (
        {}
        if args.rules_only or args.agents_only or args.config_only or args.opencode_only
        else selected_skills(args)
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
                if profile == "work" or categories["rules"][rule.name] == "general"
            )

    if installs_codex_instructions(args):
        if codex_home is None:
            codex_home, _ = resolve_codex_home(args)
        # Default homes serve every project, so they only get the general rules;
        # a dedicated (custom) Codex home follows the installation profile.
        home_profile = profile if args.codex_home else "general"
        sources = selected_instructions(home_profile, categories["instructions"])
        if sources:
            specs.append(
                InstallSpec(
                    INSTRUCTIONS_SOURCE,
                    codex_home / "AGENTS.md",
                    "Codex instructions",
                    INSTRUCTIONS_STRATEGY,
                    sources,
                )
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

    if installs_claude(args):
        specs.extend(
            InstallSpec(
                source,
                CLAUDE_SKILLS_TARGET / name,
                "Claude Code skill",
                DIRECTORY_LINK_STRATEGY,
            )
            for name, source in skills.items()
        )
        available_claude_agents()
        specs.append(
            InstallSpec(
                CLAUDE_AGENTS_SOURCE,
                CLAUDE_HOME / "agents",
                "Claude Code agents",
                DIRECTORY_LINK_STRATEGY,
            )
        )
        sources = selected_instructions("general", categories["instructions"])
        if sources:
            specs.append(
                InstallSpec(
                    INSTRUCTIONS_SOURCE,
                    CLAUDE_HOME / "CLAUDE.md",
                    "Claude Code instructions",
                    INSTRUCTIONS_STRATEGY,
                    sources,
                )
            )
        if not (CCLINE_SOURCE / "config.toml").is_file():
            raise SystemExit(f"ccline config does not exist: {CCLINE_SOURCE / 'config.toml'}")
        specs.append(
            InstallSpec(
                CCLINE_SOURCE / "config.toml",
                CCLINE_HOME / "config.toml",
                "ccline config",
                FILE_LINK_STRATEGY,
                backup_group="ccline",
            )
        )
        specs.append(
            InstallSpec(
                CLAUDE_SETTINGS_SOURCE,
                CLAUDE_SETTINGS,
                "Claude Code settings",
                JSON_SETTINGS_MERGE_STRATEGY,
                selected_profile_files(
                    profile, categories["claude_settings"], CLAUDE_SETTINGS_SOURCE,
                    "Claude Code settings fragments",
                ),
            )
        )

    if installs_opencode(args):
        available_opencode_agents()
        specs.append(
            InstallSpec(
                OPENCODE_AGENTS_SOURCE,
                DEFAULT_OPENCODE_HOME / "agents",
                "OpenCode agents",
                DIRECTORY_LINK_STRATEGY,
            )
        )
        specs.append(
            InstallSpec(
                OPENCODE_CONFIG_SOURCE,
                DEFAULT_OPENCODE_HOME / "opencode.json",
                "OpenCode config",
                FILE_LINK_STRATEGY,
            )
        )

    return specs


def backup_replaced_path(target: Path, group: str = "opencode") -> Path:
    stamp = target.name + "." + os.urandom(4).hex()
    root = Path(os.environ.get("AGENT_TOOLKIT_BACKUP_DIR", str(TOOLKIT_ROOT / ".local" / "backups")))
    destination = root / group / stamp
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(target, destination)
    return destination


def adopt_matching_file(target: Path, source: Path) -> bool:
    """Replace a regular file with a link when its bytes already match the source."""
    try:
        if target.is_symlink() or not target.is_file():
            return False
        return target.read_bytes() == source.read_bytes()
    except OSError:
        return False


def install_file_link(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    if is_windows():
        return install_windows_file_copy(spec, args)

    source = spec.source.resolve()
    target = spec.target

    if os.path.lexists(target):
        if target.is_symlink():
            try:
                existing = resolve_link_target(target)
            except OSError as error:
                return f"CONFLICT {spec.label}: {target} ({error})", False
            if existing == source:
                return f"OK       {spec.label}: {target} -> {source}", True
            if not args.replace_managed or not is_managed_target(existing):
                return f"CONFLICT {spec.label}: {target} -> {existing}", False
            action = "REPLACE"
        elif target.is_dir():
            return f"CONFLICT {spec.label}: {target} exists and is not a file link", False
        elif adopt_matching_file(target, source):
            action = "ADOPT  "
        else:
            return (
                f"CONFLICT {spec.label}: {target} exists and differs from {source}",
                False,
            )
    else:
        action = "CREATE "

    message = f"{action} {spec.label}: {target} -> {source}"
    if args.dry_run:
        return message, True

    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if os.path.lexists(target) and not target.is_symlink():
        try:
            backup = backup_replaced_path(target, spec.backup_group)
        except OSError as error:
            return f"ERROR    {spec.label}: unable to preserve {target} ({error})", False
    elif os.path.lexists(target):
        try:
            target.unlink()
        except OSError as error:
            return f"ERROR    {spec.label}: unable to replace {target} ({error})", False
    try:
        target.symlink_to(source)
    except OSError as error:
        if backup is not None:
            os.replace(backup, target)
        return f"ERROR    {spec.label}: {target} -> {source} ({error})", False
    if backup is not None:
        message = f"{message} (preserved {backup})"
    return message, True


def install_windows_file_copy(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    """Manage a file copy without requiring Windows symlink privileges."""
    source = spec.source.resolve()
    target = spec.target
    receipt = target.with_name(f".{target.name}.agent-toolkit.json")
    metadata = None
    try:
        if os.path.lexists(receipt):
            if receipt.is_symlink() or not receipt.is_file():
                raise ValueError("installation receipt is not a regular file")
            metadata = json.loads(receipt.read_text(encoding="utf-8"))
            if (
                not isinstance(metadata, dict)
                or metadata.get("schema_version") != 1
                or metadata.get("managed_by") != "agent-toolkit"
                or metadata.get("component") != spec.label
                or not isinstance(metadata.get("sha256"), str)
            ):
                raise ValueError("unrecognized installation receipt")
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if target.is_symlink():
            existing = resolve_link_target(target)
            if existing == source:
                return f"OK       {spec.label}: {target} -> {source}", True
            if not args.replace_managed or not is_managed_target(existing):
                return f"CONFLICT {spec.label}: {target} -> {existing}", False
            action = "REPLACE"
        elif os.path.lexists(target):
            if not target.is_file():
                return f"CONFLICT {spec.label}: {target} is not a regular file", False
            current_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if metadata is not None and metadata["sha256"] != current_digest:
                return f"CONFLICT {spec.label}: {target} was modified locally", False
            if current_digest == source_digest:
                if metadata is not None:
                    return f"OK       {spec.label}: {target} matches {source} (managed copy)", True
                action = "ADOPT  "
            elif metadata is not None:
                action = "UPDATE "
            else:
                return f"CONFLICT {spec.label}: {target} exists and differs from {source}", False
        else:
            action = "CREATE "
    except (OSError, UnicodeError, ValueError) as error:
        return f"CONFLICT {spec.label}: {target} ({error})", False

    message = f"{action} {spec.label}: {source} -> {target} (managed copy)"
    if args.dry_run:
        return message, True

    backup = None
    try:
        if os.path.lexists(target):
            backup = backup_replaced_path(target, spec.backup_group)
        atomic_copy(source, target)
        metadata = {
            "schema_version": 1,
            "managed_by": "agent-toolkit",
            "component": spec.label,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        atomic_write_text(receipt, json.dumps(metadata, indent=2) + "\n")
    except OSError as error:
        if backup is not None:
            os.replace(backup, target)
        elif action.strip() == "CREATE":
            target.unlink(missing_ok=True)
        return f"ERROR    {spec.label}: unable to install {target} ({error})", False
    if backup is not None:
        message = f"{message} (preserved {backup})"
    return message, True


def install_directory_link(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    source = spec.source.resolve()
    target = spec.target

    adopting = False
    if os.path.lexists(target):
        if directory_link_kind(target) is None:
            if not adopt_matching_directory(target, source):
                return f"CONFLICT {spec.label}: {target} exists and is not a directory link", False
            adopting = True
            action = "ADOPT  "
        else:
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
    backup = None
    if os.path.lexists(target):
        try:
            if adopting:
                backup = backup_replaced_path(target)
            else:
                remove_directory_link(target)
        except (DirectoryLinkError, OSError) as error:
            return f"ERROR    {spec.label}: unable to replace {target} ({error})", False
    try:
        create_directory_link(source, target)
    except (DirectoryLinkError, OSError) as error:
        if backup is not None and not os.path.lexists(target):
            os.replace(backup, target)
        return f"ERROR    {spec.label}: {target} -> {source} ({error})", False
    if backup is not None:
        message = f"{message} (preserved {backup})"
    return message, True


def adopt_matching_directory(target: Path, source: Path) -> bool:
    """A real directory can be replaced only when every file already matches the source."""
    if not target.is_dir() or directory_link_kind(target) is not None:
        return False
    source_files = {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    target_files = {
        path.relative_to(target): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    return source_files == target_files and bool(source_files)


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


def render_instructions(sources: tuple[Path, ...]) -> str:
    body = "\n".join(source.read_text(encoding="utf-8").rstrip("\n") + "\n" for source in sources)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    names = ", ".join(source.relative_to(TOOLKIT_ROOT).as_posix() for source in sources)
    marker = (
        f"<!-- Managed by agent-toolkit. Generated from {names}; "
        f"edit the sources and reinstall. sha256={digest} -->"
    )
    return f"{marker}\n\n{body}"


def instructions_state(text: str) -> str:
    """Return "managed", "modified" or "unmanaged" for an existing instructions file."""
    marker, separator, rest = text.partition("\n")
    match = INSTRUCTIONS_MARKER_PATTERN.match(marker)
    if match is None or not separator or not rest.startswith("\n"):
        return "unmanaged"
    body = rest[1:]
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != match.group(1):
        return "modified"
    return "managed"


def install_instructions(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    target = spec.target
    names = " + ".join(source.name for source in spec.sources)
    try:
        desired = render_instructions(spec.sources)
    except (OSError, UnicodeError) as error:
        return f"CONFLICT {spec.label}: unable to read sources ({error})", False

    if os.path.lexists(target):
        if target.is_symlink():
            try:
                existing = resolve_link_target(target)
            except OSError as error:
                return f"CONFLICT {spec.label}: {target} ({error})", False
            if existing.parent in (
                spec.source.resolve(),
                LEGACY_INSTRUCTIONS_SOURCE.resolve(strict=False),
            ):
                # Older setups linked the entry straight to one source file,
                # possibly under the pre-move codex/instructions directory.
                action = "MIGRATE"
            elif args.replace_managed and is_managed_target(existing):
                action = "REPLACE"
            else:
                return f"CONFLICT {spec.label}: {target} -> {existing}", False
        elif not target.is_file():
            return f"CONFLICT {spec.label}: {target} exists and is not a regular file", False
        else:
            try:
                current = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                return f"CONFLICT {spec.label}: {target} ({error})", False
            state = instructions_state(current)
            if state == "unmanaged":
                return f"CONFLICT {spec.label}: {target} exists and is not managed", False
            if state == "modified":
                return f"CONFLICT {spec.label}: {target} was modified locally", False
            if current == desired:
                return f"OK       {spec.label}: {target} matches {names}", True
            action = "UPDATE "
    else:
        action = "CREATE "

    message = f"{action} {spec.label}: {names} -> {target}"
    if args.dry_run:
        return message, True

    try:
        if target.is_symlink():
            target.unlink()
        atomic_write_text(target, desired)
    except OSError as error:
        return f"ERROR    {spec.label}: unable to write {target} ({error})", False
    return message, True


def ccline_binary() -> Path:
    return CCLINE_HOME / ("ccline.exe" if is_windows() else "ccline")


def ccline_version(binary: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", result.stdout)
    return match.group(1) if match else None


class SettingsMergeError(RuntimeError):
    pass


def is_settings_defaults(source: Path) -> bool:
    """`*.defaults.json` fragments only fill keys the machine has not set yet."""
    return source.name.endswith(".defaults.json")


def load_settings_fragment(sources: tuple[Path, ...]) -> dict:
    fragment: dict = {}
    for source in sources:
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SettingsMergeError(f"{source.name} must contain a JSON object")
        conflicts: list[str] = []
        merge_settings(fragment, data, [], conflicts)
        if conflicts:
            raise SettingsMergeError(f"{source.name} contradicts earlier fragments: {', '.join(conflicts)}")
    return fragment


def load_settings_fragments(sources: tuple[Path, ...]) -> tuple[dict, dict]:
    """Return (required, defaults); defaults may not contradict required values."""
    required = load_settings_fragment(tuple(s for s in sources if not is_settings_defaults(s)))
    defaults = load_settings_fragment(tuple(s for s in sources if is_settings_defaults(s)))
    conflicts: list[str] = []
    merge_settings(json.loads(json.dumps(required)), defaults, [], conflicts)
    if conflicts:
        raise SettingsMergeError(f"defaults contradict required fragments: {', '.join(conflicts)}")
    return required, defaults


def merge_settings(
    target: dict,
    fragment: dict,
    added: list[str],
    conflicts: list[str],
    prefix: str = "",
    fill_only: bool = False,
) -> None:
    """Add fragment entries to target in place: lists gain missing items, objects
    recurse, other values are added only when absent and otherwise must match.
    With fill_only, existing values (including lists) are kept and never conflict."""
    for key, value in fragment.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in target:
            target[key] = json.loads(json.dumps(value))
            added.append(path)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            merge_settings(target[key], value, added, conflicts, path, fill_only)
        elif fill_only:
            continue
        elif isinstance(target[key], list) and isinstance(value, list):
            missing = [item for item in value if item not in target[key]]
            if missing:
                target[key].extend(missing)
                added.append(f"{path} +{len(missing)}")
        elif target[key] != value:
            conflicts.append(path)


def install_json_settings_merge(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    target = spec.target
    names = " + ".join(source.name for source in spec.sources)
    try:
        fragment, defaults = load_settings_fragments(spec.sources)
    except (OSError, UnicodeError, ValueError, SettingsMergeError) as error:
        return f"CONFLICT {spec.label}: invalid fragment {names} ({error})", False

    notes = []
    binary = ccline_binary()
    if is_windows() or not binary.is_file():
        # A status line pointing at a missing (or unverified) binary only shows errors.
        for key in CCLINE_STATUS_LINE_KEYS:
            fragment.pop(key, None)
        notes.append(
            "status line not managed on Windows" if is_windows()
            else f"status line skipped: {binary} not found; run `{CCLINE_INSTALL_COMMAND}`, then reinstall"
        )
    else:
        version = ccline_version(binary)
        if version != CCLINE_VERSION:
            notes.append(f"ccline {version or 'unknown'} != pinned {CCLINE_VERSION}")
    note = f" (NOTE: {'; '.join(notes)})" if notes else ""

    if os.path.lexists(target) and (target.is_symlink() or not target.is_file()):
        return f"CONFLICT {spec.label}: {target} is not a regular file", False
    try:
        existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, UnicodeError, ValueError) as error:
        return f"CONFLICT {spec.label}: {target} is not valid JSON ({error})", False
    if not isinstance(existing, dict):
        return f"CONFLICT {spec.label}: {target} must contain a JSON object", False

    merged = json.loads(json.dumps(existing))
    added: list[str] = []
    conflicts: list[str] = []
    merge_settings(merged, fragment, added, conflicts)
    if conflicts:
        return (
            f"CONFLICT {spec.label}: {target} already sets {', '.join(conflicts)} "
            "to a different value; nothing was written",
            False,
        )
    merge_settings(merged, defaults, added, conflicts, fill_only=True)
    if not added:
        return f"OK       {spec.label}: {target} contains {names}{note}", True

    action = "UPDATE " if target.exists() else "CREATE "
    message = f"{action} {spec.label}: add {', '.join(added)} from {names} to {target}{note}"
    if args.dry_run:
        return message, True
    try:
        atomic_write_text(target, json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        return f"ERROR    {spec.label}: unable to update {target} ({error})", False
    return message, True


def install_spec(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    if spec.strategy == DIRECTORY_LINK_STRATEGY:
        return install_directory_link(spec, args)
    if spec.strategy == FILE_LINK_STRATEGY:
        return install_file_link(spec, args)
    if spec.strategy == COPY_STRATEGY:
        return install_copy(spec, args)
    if spec.strategy == TOML_MERGE_STRATEGY:
        return install_toml_merge(spec, args)
    if spec.strategy == INSTRUCTIONS_STRATEGY:
        return install_instructions(spec, args)
    if spec.strategy == JSON_SETTINGS_MERGE_STRATEGY:
        return install_json_settings_merge(spec, args)
    return f"CONFLICT {spec.label}: unknown install strategy {spec.strategy}", False


def main() -> int:
    args = parse_args()
    profile = resolve_profile(args)
    print(f"Installation profile: {profile}")
    if (
        not args.skill
        and not (SKILLS_DIR / "ui-ux-pro-max" / "SKILL.md").is_file()
        and not (args.rules_only or args.agents_only or args.config_only or args.opencode_only)
    ):
        print(
            "NOTE: ui-ux-pro-max is not materialized; run "
            "scripts/update_ui_ux_pro_max.py --from-lock, then reinstall."
        )
    codex_home = None
    if installs_codex_configuration(args):
        codex_home, home_kind = resolve_codex_home(args)
        print(f"Codex home: {codex_home} ({home_kind})")
        if installs_codex_agents(args):
            print(f"Agents target: {codex_home / 'agents'}")
        if installs_codex_rules(args):
            print(f"Rules target: {codex_home / 'rules'}")
        if installs_codex_instructions(args):
            print(f"Instructions target: {codex_home / 'AGENTS.md'}")
        if installs_codex_playwright_config(args):
            print(f"Config target: {codex_home / 'config.toml'}")
        print()
    if installs_claude(args):
        print(f"Claude Code agents: {CLAUDE_HOME / 'agents'}")
        print(f"Claude Code instructions: {CLAUDE_HOME / 'CLAUDE.md'}")
        print(f"ccline config: {CCLINE_HOME / 'config.toml'} (binary {CCLINE_VERSION}: {CCLINE_INSTALL_COMMAND})")
        print()
    if installs_opencode(args):
        print(f"OpenCode home: {DEFAULT_OPENCODE_HOME}")
        print(f"OpenCode config: {DEFAULT_OPENCODE_HOME / 'opencode.json'}")
        print(f"OpenCode agents: {DEFAULT_OPENCODE_HOME / 'agents'}")
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

    if not args.dry_run:
        save_profile(profile)
    mode = "dry-run" if args.dry_run else "complete"
    print(f"\nInstallation {mode}: {len(specs) + len(initializers)} item(s) checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
