"""Read per-machine work settings without keeping organization data in Git."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONFIG_ENV = "AGENT_TOOLKIT_WORK_CONFIG"


class WorkConfigError(ValueError):
    """A work configuration is missing or cannot be read safely."""


def work_config_path(config_path: str | Path | None = None) -> Path:
    selected = config_path if config_path is not None else os.environ.get(CONFIG_ENV)
    if selected is not None:
        if not str(selected).strip():
            raise WorkConfigError(f"{CONFIG_ENV} 或 --config 不得为空")
        return Path(selected).expanduser()
    return Path.home() / ".local" / "agent-toolkit" / "work" / "config.json"


def load_work_config(
    config_path: str | Path | None = None, *, required: bool = True
) -> dict[str, Any]:
    path = work_config_path(config_path)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        if not required:
            return {}
        raise WorkConfigError(
            f"找不到工作配置: {path}；请先运行 scripts/init_work_config.py 创建模板并填写"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise WorkConfigError(f"无法读取工作配置: {path}") from exc
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise WorkConfigError(f"工作配置不是有效 JSON: {path}") from exc
    if not isinstance(data, dict):
        raise WorkConfigError(f"工作配置根节点必须是对象: {path}")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise WorkConfigError(f"工作配置 schema_version 必须为 1: {path}")
    return data
