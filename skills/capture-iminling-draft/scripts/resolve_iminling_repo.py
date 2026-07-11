#!/usr/bin/env python3
"""Resolve and validate the local iminling-pages repository path."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_PATHS = (
    "AGENTS.md",
    "hugo.yaml",
    "content/post",
    "docs/reference/article-writing.md",
    "docs/reference/url-naming.md",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve the iminling-pages repository from an explicit path, environment, Git config, or cwd."
    )
    parser.add_argument("--repo", help="Explicit repository path")
    return parser.parse_args()


def git_config_path() -> str | None:
    result = subprocess.run(
        ["git", "config", "--global", "--get", "iminling.repoPath"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def cwd_candidates() -> list[Path]:
    cwd = Path.cwd().resolve()
    return [cwd, *cwd.parents]


def validate(candidate: str | Path) -> Path | None:
    path = Path(candidate).expanduser().resolve()
    if not path.is_dir():
        return None
    if all((path / required).exists() for required in REQUIRED_PATHS):
        return path
    return None


def main() -> int:
    args = parse_args()
    configured = [
        args.repo,
        os.environ.get("IMINLING_PAGES_REPO"),
        git_config_path(),
    ]

    for candidate in configured:
        if not candidate:
            continue
        resolved = validate(candidate)
        if resolved is not None:
            print(resolved)
            return 0
        print(f"Configured iminling-pages path is invalid: {candidate}", file=sys.stderr)
        return 2

    for candidate in cwd_candidates():
        resolved = validate(candidate)
        if resolved is not None:
            print(resolved)
            return 0

    print(
        "Unable to locate iminling-pages. Set IMINLING_PAGES_REPO or run: "
        "git config --global iminling.repoPath /absolute/path/to/iminling-pages",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
