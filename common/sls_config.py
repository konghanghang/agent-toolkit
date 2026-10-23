"""Resolve private SLS targets and their configured log-retention windows."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import NamedTuple

from work_config import WorkConfigError


class SlsRoute(NamedTuple):
    project: str
    logstore: str
    retention_days: int | None = None
    scope: str = ""
    service: str = ""


SCOPE_ALIASES = {"cn": "domestic", "intl": "overseas", "global": "overseas"}


def retention_days(value: object, label: str) -> int | None:
    if value is not None and (type(value) is not int or value <= 0):
        raise WorkConfigError(f"{label}.retention_days 必须为正整数或 null（未知）")
    return value


def sections(config: dict) -> tuple[dict[str, dict], str | None]:
    """Normalize single-target, named scopes, and the older two-scope format."""
    legacy = [name for name in ("domestic", "overseas") if name in config]
    flat = any(key in config for key in ("default", "services", "retention_days"))
    if "scopes" in config:
        if legacy or flat:
            raise WorkConfigError("sls.scopes 不能与顶层 default/services/retention_days 或旧 domestic/overseas 混用")
        values = config["scopes"]
        if not isinstance(values, dict) or not values:
            raise WorkConfigError("sls.scopes 必须为非空环境对象")
    elif legacy:
        if flat:
            raise WorkConfigError("SLS 单目标配置不能与旧 domestic/overseas 配置混用")
        values = {name: config[name] for name in legacy}
    else:
        if not flat:
            raise WorkConfigError("工作配置需要 sls.default 或 sls.scopes 路由")
        if config.get("default_scope") is not None:
            raise WorkConfigError("单目标 SLS 不使用 default_scope；多环境请配置 scopes")
        values = {"": config}
    for name, section in values.items():
        if not isinstance(name, str) or (name and name.strip() != name):
            raise WorkConfigError("SLS 环境名称必须是非空且没有首尾空白的字符串")
        if not name and "scopes" in config:
            raise WorkConfigError("SLS 环境名称不得为空")
        if not isinstance(section, dict):
            raise WorkConfigError(f"SLS 环境 {name or 'default'} 必须是对象")
        retention_days(section.get("retention_days"), f"sls.{name or 'default'}")
        services = section.get("services", {})
        if not isinstance(services, dict) or any(
            not isinstance(service, str) or not service.strip()
            or service.strip() != service or not isinstance(route, dict)
            for service, route in services.items()
        ):
            raise WorkConfigError(f"SLS 环境 {name or 'default'} 的 services 必须是服务名到路由对象的映射")
        for service, route in services.items():
            if "retention_days" in route:
                retention_days(route["retention_days"], f"sls.{name}.services.{service}")
        default = section.get("default", {})
        if isinstance(default, dict) and "retention_days" in default:
            retention_days(default["retention_days"], f"sls.{name}.default")
    default_scope = config.get("default_scope")
    if default_scope is not None:
        if not isinstance(default_scope, str) or default_scope not in values:
            raise WorkConfigError("sls.default_scope 必须引用已配置的环境名称")
    elif legacy and "domestic" in values:
        # Preserve the previous default for existing private installations only.
        default_scope = "domestic"
    return values, default_scope


def _route(section: dict, scope: str, service: str = "", *, required: bool = True) -> SlsRoute | None:
    services = section.get("services", {})
    label = f"sls.{scope or 'default'}"
    value = services[service] if service in services else section.get("default", {})
    if not isinstance(value, dict):
        raise WorkConfigError(f"{label} 路由必须包含 project/logstore")
    target = [value.get(key, "") for key in ("project", "logstore")]
    if not all(isinstance(item, str) for item in target):
        raise WorkConfigError(f"{label} 的 project/logstore 必须为字符串")
    project, logstore = (item.strip() for item in target)
    if not project or not logstore:
        if not required and not project and not logstore:
            return None
        raise WorkConfigError(f"{label} 需要非空 project 和 logstore")
    days = retention_days(value.get("retention_days", section.get("retention_days")), label)
    return SlsRoute(project, logstore, days, scope, service)


def _infer_service(text: str, candidates: list[str]) -> str:
    matches = {name for name in candidates if name in text}
    if len(matches) > 1:
        raise WorkConfigError("查询包含多个已配置服务；请显式指定 --service")
    return next(iter(matches), "")


def select_route(config: dict, scope: str = "", service: str = "", query_text: str = "") -> SlsRoute:
    values, default_scope = sections(config)
    if scope:
        scope = scope if scope in values else SCOPE_ALIASES.get(scope, scope)
        if scope not in values:
            raise WorkConfigError(f"未配置 SLS 环境 {scope}；请核对 --scope 和本机配置")
    elif default_scope is not None:
        scope = default_scope
    elif len(values) == 1:
        scope = next(iter(values))
    else:
        if not service:
            service = _infer_service(query_text, [name for section in values.values() for name in section.get("services", {})])
        matching = [name for name, section in values.items() if service and service in section.get("services", {})]
        if len(matching) != 1:
            raise WorkConfigError("无法确定要查询的 SLS project；多个环境请传 --scope，或配置 default_scope/唯一服务路由")
        scope = matching[0]
    section = values[scope]
    if not service:
        service = _infer_service(query_text, list(section.get("services", {})))
    result = _route(section, scope, service)
    assert result is not None
    return result


def infer_project_route(config: dict, project: str) -> SlsRoute | None:
    values, _ = sections(config)
    matches = []
    for scope, section in values.items():
        for service in ("", *section.get("services", {})):
            route = _route(section, scope, service, required=False)
            if route and route.project == project:
                matches.append(route)
    if len({route.logstore for route in matches}) != 1:
        return None
    route = matches[0]
    if len({item.retention_days for item in matches}) > 1:
        return route._replace(retention_days=None)
    return route


def _utc(value: int) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def constrain_window(
    route: SlsRoute, *, from_ts: int | None = None, to_ts: int | None = None,
    minutes: int = 60, now: int | None = None,
) -> tuple[int, int, list[str]]:
    now = int(time.time()) if now is None else now
    if type(minutes) is not int or minutes <= 0:
        raise WorkConfigError("--minutes 必须为正整数")
    start = now - minutes * 60 if from_ts is None else from_ts
    end = now if to_ts is None else to_ts
    if end < start:
        raise WorkConfigError("--to 不能早于 --from")
    days = retention_days(route.retention_days, "SLS 路由")
    if days is None:
        return start, end, ["[!] SLS 日志保留天数未知；请在私有配置填写 retention_days，不能根据 0 条结果否定历史事实"]
    boundary = now - days * 24 * 60 * 60
    if end <= boundary:
        raise WorkConfigError(f"查询窗口全部超出配置的 {days} 天日志保留期（边界 {_utc(boundary)}）；未执行查询，不能据此排除历史事实")
    messages = [f"[*] SLS 配置保留期: {days} 天；当前保留边界 {_utc(boundary)}"]
    if start < boundary:
        messages.append(f"[!] 查询已裁剪到保留边界；无法覆盖 {_utc(start)} 至 {_utc(boundary)}，0 条结果不能否定这段历史事实")
        start = boundary
    return start, end, messages
