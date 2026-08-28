#!/usr/bin/env python3
"""Create an empty private GitLab credentials template without overwriting user state."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


COMMON_DIR = Path(__file__).resolve().parents[3] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from private_files import PrivatePathError, secure_private_directory, secure_private_file


DEFAULT_CONFIG_DIR = Path.home() / ".local" / "agent-toolkit" / "work"
DEFAULT_GITLAB_DIR = DEFAULT_CONFIG_DIR / "gitlab"
DEFAULT_RUNTIME_FILE = DEFAULT_CONFIG_DIR / "runtime.json"
CREDENTIALS_FILE_NAME = "gitlab-credentials.json"
CREDENTIALS_TEMPLATE: dict[str, str] = {
    "base_url": "",
    "private_token": "",
}


class ConfigInitError(RuntimeError):
    pass


def resolve_gitlab_dir(
    base_dir: str | Path | None = None,
    *,
    runtime_file: Path = DEFAULT_RUNTIME_FILE,
) -> Path:
    if base_dir is not None:
        return Path(base_dir).expanduser().resolve(strict=False)
    if runtime_file.is_file():
        try:
            runtime = json.loads(runtime_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigInitError(f"运行时配置解析失败: {runtime_file}: {exc}") from exc
        configured = runtime.get("gitlab_dir") if isinstance(runtime, dict) else None
        if configured:
            return Path(str(configured)).expanduser().resolve(strict=False)
    return DEFAULT_GITLAB_DIR.resolve(strict=False)


def render_template(template: Mapping[str, Any] = CREDENTIALS_TEMPLATE) -> bytes:
    return (json.dumps(template, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def existing_file_message(credentials_file: Path) -> str:
    return f"OK       GitLab credentials: {credentials_file} exists; preserved"


def initialize_credentials(
    base_dir: Path,
    *,
    dry_run: bool = False,
    template: Mapping[str, Any] = CREDENTIALS_TEMPLATE,
) -> tuple[str, bool]:
    base_dir = base_dir.expanduser().resolve(strict=False)
    credentials_file = base_dir / CREDENTIALS_FILE_NAME
    if os.path.lexists(base_dir) and not base_dir.is_dir():
        return f"CONFLICT GitLab directory: {base_dir} is not a directory", False
    if os.path.lexists(credentials_file):
        if credentials_file.is_symlink() or not credentials_file.is_file():
            return (
                f"CONFLICT GitLab credentials: {credentials_file} is not a regular file",
                False,
            )
        if not dry_run:
            try:
                secure_private_directory(base_dir)
                secure_private_file(credentials_file)
            except PrivatePathError as exc:
                return (
                    f"CONFLICT GitLab credentials: unable to protect {credentials_file}: {exc}",
                    False,
                )
        return existing_file_message(credentials_file), True

    message = f"CREATE   GitLab credentials: {credentials_file} (private ACL/mode)"
    if dry_run:
        return message, True

    try:
        secure_private_directory(base_dir)
    except PrivatePathError as exc:
        return f"CONFLICT GitLab directory: unable to create {base_dir}: {exc}", False

    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{CREDENTIALS_FILE_NAME}.",
            suffix=".tmp",
            dir=base_dir,
        )
        temporary_path = Path(raw_path)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(render_template(template))
            handle.flush()
            os.fsync(handle.fileno())
        secure_private_file(temporary_path)
        try:
            os.link(temporary_path, credentials_file)
        except FileExistsError:
            if credentials_file.is_symlink() or not credentials_file.is_file():
                return (
                    f"CONFLICT GitLab credentials: {credentials_file} appeared and is not regular",
                    False,
                )
            return existing_file_message(credentials_file), True
    except OSError as exc:
        return (
            f"CONFLICT GitLab credentials: unable to create {credentials_file}: {exc}",
            False,
        )
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return message, True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a private empty GitLab credentials template if missing."
    )
    parser.add_argument("--base-dir", help="Override GITLAB_DIR")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        base_dir = resolve_gitlab_dir(args.base_dir)
    except ConfigInitError as exc:
        print(f"CONFLICT GitLab credentials: {exc}")
        return 1
    message, success = initialize_credentials(base_dir, dry_run=args.dry_run)
    print(message)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
