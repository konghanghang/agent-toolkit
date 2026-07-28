#!/usr/bin/env python3
"""Install Agent Toolkit skills, Codex agents, and managed Codex rules."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = TOOLKIT_ROOT / "skills"
CODEX_AGENTS_SOURCE = TOOLKIT_ROOT / "codex" / "agents"
CODEX_RULES_SOURCE = TOOLKIT_ROOT / "codex" / "rules"
CODEX_SKILLS_TARGET = Path.home() / ".agents" / "skills"
CLAUDE_SKILLS_TARGET = Path.home() / ".claude" / "skills"
DEFAULT_CODEX_HOME = Path.home() / ".codex"
LEGACY_ROOTS = (
    Path.home() / ".shared-skills",
    TOOLKIT_ROOT,
)
RULE_MANAGED_MARKER = "# Managed by agent-toolkit."
SYMLINK_STRATEGY = "symlink"
COPY_STRATEGY = "copy"
AGENT_REQUIRED_KEYS = ("name", "description", "developer_instructions")
TOML_KEY_PATTERN = re.compile(r"(?m)^\s*([A-Za-z0-9_-]+)\s*=")


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
    if (args.rules_only or args.agents_only) and args.skill:
        parser.error("--skill cannot be used with --rules-only or --agents-only")

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


def is_managed_target(path: Path) -> bool:
    for root in LEGACY_ROOTS:
        try:
            path.relative_to(root.expanduser().resolve(strict=False))
            return True
        except ValueError:
            continue
    return False


def installs_codex_rules(args: argparse.Namespace) -> bool:
    return not args.claude_only and not args.agents_only


def installs_codex_agents(args: argparse.Namespace) -> bool:
    return not args.claude_only and not args.rules_only


def installs_codex_configuration(args: argparse.Namespace) -> bool:
    return installs_codex_rules(args) or installs_codex_agents(args)


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
    skills = {} if args.rules_only or args.agents_only else selected_skills(args.skill)
    specs: list[InstallSpec] = []

    if not args.claude_only and not args.rules_only and not args.agents_only:
        specs.extend(
            InstallSpec(source, CODEX_SKILLS_TARGET / name, "Codex skill", SYMLINK_STRATEGY)
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
                SYMLINK_STRATEGY,
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

    if not args.codex_only and not args.rules_only and not args.agents_only:
        specs.extend(
            InstallSpec(source, CLAUDE_SKILLS_TARGET / name, "Claude Code skill", SYMLINK_STRATEGY)
            for name, source in skills.items()
        )

    return specs


def install_symlink(spec: InstallSpec, args: argparse.Namespace) -> tuple[str, bool]:
    source = spec.source.resolve()
    target = spec.target

    if os.path.lexists(target):
        if not target.is_symlink():
            return f"CONFLICT {spec.label}: {target} exists and is not a symlink", False

        existing = resolve_link_target(target)
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
        target.unlink()
    target.symlink_to(source, target_is_directory=source.is_dir())
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
    if spec.strategy == SYMLINK_STRATEGY:
        return install_symlink(spec, args)
    if spec.strategy == COPY_STRATEGY:
        return install_copy(spec, args)
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

    mode = "dry-run" if args.dry_run else "complete"
    print(f"\nInstallation {mode}: {len(specs)} item(s) checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
