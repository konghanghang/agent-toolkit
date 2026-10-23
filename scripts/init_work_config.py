#!/usr/bin/env python3
"""Create an empty private work configuration without overwriting local values."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from private_files import PrivatePathError, secure_private_directory, secure_private_file
from work_config import WorkConfigError, work_config_path


CONFIG_TEMPLATE = {
    "schema_version": 1,
    "sls": {
        "domestic": {
            "default": {"project": "", "logstore": ""},
            "services": {
                "example-service": {"project": "", "logstore": ""},
            },
        },
        "overseas": {
            "default": {"project": "", "logstore": ""},
            "services": {},
        },
    },
    "archery": {"scopes": {"domestic": "", "overseas": ""}, "modules": {"example-module": ""}},
    "tapd_weekly_report": {"workspace_id": "", "members": []},
}


def initialize_config(path: Path, *, dry_run: bool = False) -> bool:
    if os.path.lexists(path):
        if path.is_symlink() or not path.is_file():
            raise WorkConfigError(f"工作配置路径不是普通文件: {path}")
        print(f"OK       工作配置已存在，保留原内容: {path}")
        return False
    if dry_run:
        print(f"CREATE   工作配置模板: {path}")
        return True

    secure_private_directory(path.parent)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(CONFIG_TEMPLATE, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        secure_private_file(temporary)
        # Linking a complete file refuses an existing target even if another
        # initializer creates it after our first check.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                raise WorkConfigError(f"工作配置路径不是普通文件: {path}")
            print(f"OK       工作配置已存在，保留原内容: {path}")
            return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"CREATE   工作配置模板: {path}；请填写所需部分")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="私有配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不创建文件")
    args = parser.parse_args(argv)
    try:
        initialize_config(work_config_path(args.config), dry_run=args.dry_run)
    except (WorkConfigError, PrivatePathError, OSError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
