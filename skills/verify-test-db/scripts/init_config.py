#!/usr/bin/env python3
"""Create the non-secret verify-test-db profile template without overwriting user state."""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_CONFIG_DIR = Path.home() / ".local" / "agent-toolkit" / "work"
DEFAULT_TEST_DB_DIR = DEFAULT_CONFIG_DIR / "test-db"
DEFAULT_RUNTIME_FILE = DEFAULT_CONFIG_DIR / "runtime.json"
PROFILE_FILE_NAME = "profiles.json"
PROFILE_TEMPLATE: dict[str, Any] = {
    "profiles": {
        "test-main": {
            "environment": "test",
            "engine": "mysql",
            "host": "测试库地址",
            "port": 3306,
            "username": "测试库账号",
            "password_env": "TEST_MAIN_DB_PASSWORD",
            "allowed_databases": ["*"],
            "writable_databases": ["*"],
        }
    }
}


class ConfigInitError(RuntimeError):
    pass


def resolve_test_db_dir(
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
        configured = runtime.get("test_db_dir") if isinstance(runtime, dict) else None
        if configured:
            return Path(str(configured)).expanduser().resolve(strict=False)
    return DEFAULT_TEST_DB_DIR.resolve(strict=False)


def render_template(template: Mapping[str, Any] = PROFILE_TEMPLATE) -> bytes:
    return (json.dumps(template, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def existing_file_message(profile_file: Path) -> str:
    message = f"OK       Test DB profile: {profile_file} exists; preserved"
    if os.name != "nt":
        permissions = stat.S_IMODE(profile_file.stat().st_mode)
        if permissions & 0o077:
            message += f" (WARNING mode={permissions:04o}, expected 0600)"
    return message


def initialize_profile(
    base_dir: Path,
    *,
    dry_run: bool = False,
    template: Mapping[str, Any] = PROFILE_TEMPLATE,
) -> tuple[str, bool]:
    base_dir = base_dir.expanduser().resolve(strict=False)
    profile_file = base_dir / PROFILE_FILE_NAME
    if os.path.lexists(base_dir) and not base_dir.is_dir():
        return f"CONFLICT Test DB directory: {base_dir} is not a directory", False
    if os.path.lexists(profile_file):
        if profile_file.is_symlink() or not profile_file.is_file():
            return f"CONFLICT Test DB profile: {profile_file} is not a regular file", False
        return existing_file_message(profile_file), True

    message = f"CREATE   Test DB profile: {profile_file} (template, mode 0600)"
    if dry_run:
        return message, True

    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"CONFLICT Test DB directory: unable to create {base_dir}: {exc}", False
    if os.name != "nt":
        base_dir.chmod(0o700)

    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(prefix=".profiles.json.", dir=base_dir)
        temporary_path = Path(raw_path)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(render_template(template))
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary_path.chmod(0o600)
        try:
            os.link(temporary_path, profile_file)
        except FileExistsError:
            if profile_file.is_symlink() or not profile_file.is_file():
                return f"CONFLICT Test DB profile: {profile_file} appeared and is not regular", False
            return existing_file_message(profile_file), True
    except OSError as exc:
        return f"CONFLICT Test DB profile: unable to create {profile_file}: {exc}", False
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return message, True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a private verify-test-db profiles.json template if missing."
    )
    parser.add_argument("--base-dir", help="Override TEST_DB_DIR")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        base_dir = resolve_test_db_dir(args.base_dir)
    except ConfigInitError as exc:
        print(f"CONFLICT Test DB profile: {exc}")
        return 1
    message, success = initialize_profile(base_dir, dry_run=args.dry_run)
    print(message)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
