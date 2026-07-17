#!/usr/bin/env python3
"""Install Agent Toolkit skills and Codex rules with safe symlinks."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = TOOLKIT_ROOT / "skills"
CODEX_RULES_SOURCE = TOOLKIT_ROOT / "codex" / "rules"
CODEX_SKILLS_TARGET = Path.home() / ".agents" / "skills"
CLAUDE_SKILLS_TARGET = Path.home() / ".claude" / "skills"
DEFAULT_CODEX_HOME = Path.home() / ".codex"
LEGACY_ROOTS = (
    Path.home() / ".shared-skills",
    TOOLKIT_ROOT,
)


@dataclass(frozen=True)
class LinkSpec:
    source: Path
    target: Path
    label: str


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install shared skills for Codex and Claude Code, plus Codex rules."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--codex-only", action="store_true", help="Install only Codex skills and rules")
    mode.add_argument("--claude-only", action="store_true", help="Install only Claude Code skills")
    mode.add_argument("--rules-only", action="store_true", help="Install only Codex rules")
    parser.add_argument(
        "--codex-home",
        help="Install Codex rules into this existing Codex home instead of ~/.codex",
    )
    parser.add_argument("--skill", action="append", default=[], help="Install only the named skill; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing")
    parser.add_argument(
        "--replace-managed",
        action="store_true",
        help="Replace symlinks that point to the legacy toolkit root or an old layout",
    )
    args = parser.parse_args(argv)

    if args.claude_only and args.codex_home:
        parser.error("--codex-home cannot be used with --claude-only")
    if args.rules_only and args.skill:
        parser.error("--skill cannot be used with --rules-only")

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
    return not args.claude_only


def resolve_codex_home(args: argparse.Namespace) -> tuple[Path, str]:
    if not args.codex_home:
        return DEFAULT_CODEX_HOME, "default"

    codex_home = Path(args.codex_home).expanduser().resolve(strict=False)
    if not codex_home.exists():
        raise SystemExit(f"Custom Codex home does not exist: {codex_home}")
    if not codex_home.is_dir():
        raise SystemExit(f"Custom Codex home is not a directory: {codex_home}")
    return codex_home, "custom"


def plan_links(args: argparse.Namespace, codex_home: Optional[Path] = None) -> list[LinkSpec]:
    skills = {} if args.rules_only else selected_skills(args.skill)
    specs: list[LinkSpec] = []

    if not args.claude_only and not args.rules_only:
        specs.extend(
            LinkSpec(source, CODEX_SKILLS_TARGET / name, "Codex skill")
            for name, source in skills.items()
        )

    if installs_codex_rules(args):
        if codex_home is None:
            codex_home, _ = resolve_codex_home(args)
        if CODEX_RULES_SOURCE.is_dir():
            specs.extend(
                LinkSpec(rule, codex_home / "rules" / rule.name, "Codex rule")
                for rule in sorted(CODEX_RULES_SOURCE.glob("*.rules"))
            )

    if not args.codex_only and not args.rules_only:
        specs.extend(
            LinkSpec(source, CLAUDE_SKILLS_TARGET / name, "Claude Code skill")
            for name, source in skills.items()
        )

    return specs


def install_link(spec: LinkSpec, args: argparse.Namespace) -> tuple[str, bool]:
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


def main() -> int:
    args = parse_args()
    codex_home = None
    if installs_codex_rules(args):
        codex_home, home_kind = resolve_codex_home(args)
        print(f"Codex home: {codex_home} ({home_kind})")
        print(f"Rules target: {codex_home / 'rules'}\n")

    specs = plan_links(args, codex_home)
    failures = 0

    for spec in specs:
        message, success = install_link(spec, args)
        print(message)
        if not success:
            failures += 1

    if failures:
        print(f"\n{failures} conflict(s); no conflicting path was overwritten.", file=sys.stderr)
        return 1

    mode = "dry-run" if args.dry_run else "complete"
    print(f"\nInstallation {mode}: {len(specs)} link(s) checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
