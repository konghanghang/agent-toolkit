#!/usr/bin/env python3
"""Run bounded, read-only Prometheus HTTP API queries."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


COMMON_DIR = Path(__file__).resolve().parents[3] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

from runtime import RuntimeConfigError, load_runtime_or_exit, normalize_http_base_url


DEFAULT_TIMEOUT_SECONDS = 30
SERVER_TIMEOUT_SECONDS = 25
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_QUERY_LENGTH = 4096
MAX_RANGE_SECONDS = 3 * 24 * 60 * 60
MAX_RANGE_POINTS = 1440
MAX_SERIES_MINUTES = 24 * 60
MAX_RESULT_LIMIT = 200
DEFAULT_RESULT_LIMIT = 50
MAX_SCAN_LIMIT = 5000
DEFAULT_SCAN_LIMIT = 1000
MAX_TREND_POINTS = 120
DEFAULT_TREND_POINTS = 24
METRIC_NAME_RE = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")
LABEL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROMQL_BRACKET_RE = re.compile(r"\[([^\[\]]+)\]")
PROMETHEUS_DURATION_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|[smhdwy])")
PROMETHEUS_DURATION_SECONDS = {
    "ms": 0.001,
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
    "y": 365 * 24 * 60 * 60,
}


class QueryError(RuntimeError):
    """A user-facing query validation or HTTP error."""


def validate_base_url(value: str) -> str:
    try:
        return normalize_http_base_url(value, "prometheus_base_url")
    except RuntimeConfigError as exc:
        raise QueryError(str(exc)) from exc


def runtime_base_url(runtime: dict[str, Any] | None = None) -> str:
    runtime = runtime if runtime is not None else load_runtime_or_exit(__file__)
    value = str(runtime.get("prometheus_base_url") or "").strip()
    if not value:
        toolkit_root = Path(runtime["agent_toolkit_root"])
        raise QueryError(
            "work 运行时配置缺少 prometheus_base_url；请交互运行 "
            f"{toolkit_root / 'common' / 'bootstrap_work.py'}"
        )
    return validate_base_url(value)


def validate_query(query: str, allow_expensive: bool = False) -> str:
    query = query.strip()
    if not query:
        raise QueryError("PromQL 不能为空")
    if len(query) > MAX_QUERY_LENGTH:
        raise QueryError(f"PromQL 长度不能超过 {MAX_QUERY_LENGTH} 个字符")
    issues = query_cost_issues(query)
    if issues and not allow_expensive:
        raise QueryError(
            "PromQL 超过默认成本边界: "
            + "; ".join(issues)
            + "。仅在向用户展示 PromQL、时间范围、step 和风险，"
            "并获得明确确认后，"
            "才可使用 --allow-expensive"
        )
    return query


def validate_limit(value: int) -> int:
    if value < 1 or value > MAX_RESULT_LIMIT:
        raise QueryError(f"limit 必须在 1 到 {MAX_RESULT_LIMIT} 之间")
    return value


def validate_scan_limit(value: int) -> int:
    if value < 1 or value > MAX_SCAN_LIMIT:
        raise QueryError(f"scan-limit 必须在 1 到 {MAX_SCAN_LIMIT} 之间")
    return value


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise QueryError(f"未知时区: {name}") from exc


def validate_timestamp_bounds(timestamp: float, original: str) -> float:
    try:
        datetime.fromtimestamp(timestamp, datetime_timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise QueryError(f"时间超出支持范围: {original}") from exc
    return timestamp


def parse_timestamp(value: str) -> float:
    raw = value.strip()
    try:
        timestamp = float(raw)
    except ValueError:
        pass
    else:
        if not math.isfinite(timestamp):
            raise QueryError(f"时间必须是有限数值: {value}")
        return validate_timestamp_bounds(timestamp, value)

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise QueryError(f"无法解析时间: {value}") from exc
    if parsed.tzinfo is None:
        raise QueryError(f"时间必须包含时区偏移: {value}")
    try:
        timestamp = parsed.timestamp()
    except (OverflowError, OSError, ValueError) as exc:
        raise QueryError(f"时间超出支持范围: {value}") from exc
    if not math.isfinite(timestamp):
        raise QueryError(f"时间必须是有限数值: {value}")
    return validate_timestamp_bounds(timestamp, value)


def format_timestamp(value: float, timezone: ZoneInfo) -> str:
    try:
        return datetime.fromtimestamp(value, timezone).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError) as exc:
        raise QueryError(f"时间戳超出支持范围: {value}") from exc


def mask_promql_strings(query: str) -> str:
    """Mask quoted strings so brackets in label regexes are not treated as selectors."""
    masked = list(query)
    quote: str | None = None
    escaped = False
    for index, char in enumerate(query):
        if quote is None:
            if char in {'"', "'", "`"}:
                quote = char
                masked[index] = " "
            continue
        masked[index] = " "
        if quote != "`" and escaped:
            escaped = False
        elif quote != "`" and char == "\\":
            escaped = True
        elif char == quote:
            quote = None
    return "".join(masked)


def parse_prometheus_duration(value: str) -> float | None:
    position = 0
    total = 0.0
    for match in PROMETHEUS_DURATION_TOKEN_RE.finditer(value):
        if match.start() != position:
            return None
        total += float(match.group(1)) * PROMETHEUS_DURATION_SECONDS[match.group(2)]
        position = match.end()
    if position != len(value) or position == 0 or total <= 0 or not math.isfinite(total):
        return None
    return total


def query_cost_issues(query: str) -> list[str]:
    issues: list[str] = []
    for match in PROMQL_BRACKET_RE.finditer(mask_promql_strings(query)):
        selector = match.group(1)
        lookback_text, separator, resolution_text = selector.partition(":")
        lookback = parse_prometheus_duration(lookback_text)
        if lookback is None:
            continue
        if lookback > MAX_RANGE_SECONDS:
            issues.append(f"内部回看窗口 {lookback_text} 超过 3 天")
        if separator and resolution_text:
            resolution = parse_prometheus_duration(resolution_text)
            if resolution is not None:
                points = math.floor(lookback / resolution) + 1
                if points > MAX_RANGE_POINTS:
                    issues.append(
                        f"子查询 [{selector}] 会产生约 {points} 个内部评估点，"
                        f"超过 {MAX_RANGE_POINTS} 个"
                    )
    return list(dict.fromkeys(issues))


def build_series_selector(metric: str, labels: Sequence[str]) -> tuple[str, dict[str, str]]:
    if not METRIC_NAME_RE.fullmatch(metric):
        raise QueryError(f"不是有效的精确指标名: {metric}")
    exact_labels: dict[str, str] = {}
    for item in labels:
        name, separator, value = item.partition("=")
        name = name.strip()
        if not separator or not LABEL_NAME_RE.fullmatch(name):
            raise QueryError(f"label 必须使用 name=value 格式且 name 合法: {item}")
        if name in exact_labels:
            raise QueryError(f"label 重复: {name}")
        exact_labels[name] = value
    if not exact_labels:
        return metric, exact_labels
    matchers = ",".join(
        f"{name}={json.dumps(value, ensure_ascii=False)}"
        for name, value in exact_labels.items()
    )
    return f"{metric}{{{matchers}}}", exact_labels


def validate_range(start: float, end: float, step: int) -> int:
    if start >= end:
        raise QueryError("start 必须早于 end")
    if step < 1:
        raise QueryError("step 必须大于 0")
    duration = end - start
    if duration > MAX_RANGE_SECONDS:
        raise QueryError("range 查询最多覆盖 3 天")
    points = math.floor(duration / step) + 1
    if points > MAX_RANGE_POINTS:
        raise QueryError(
            f"当前时间范围和 step 会产生约 {points} 个点，最多允许 {MAX_RANGE_POINTS} 个"
        )
    return points


def api_get(
    base_url: str,
    path: str,
    params: Sequence[tuple[str, str]],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    query_string = urllib_parse.urlencode(params, doseq=True)
    url = f"{base_url}{path}"
    if query_string:
        url = f"{url}?{query_string}"
    request = urllib_request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "agent-toolkit-prometheus-query/1",
        },
        method="GET",
    )

    try:
        with urllib_request.urlopen(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > MAX_RESPONSE_BYTES:
                    raise QueryError(
                        f"Prometheus 响应超过 {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB 限制"
                    )
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise QueryError(
                    f"Prometheus 响应超过 {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB 限制"
                )
    except urllib_error.HTTPError as exc:
        raise QueryError(f"Prometheus HTTP {exc.code}: {exc.reason}") from exc
    except urllib_error.URLError as exc:
        raise QueryError(f"无法连接 Prometheus: {exc.reason}") from exc
    except TimeoutError as exc:
        raise QueryError(f"Prometheus 查询超过 {timeout} 秒") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QueryError("Prometheus 返回的不是有效 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise QueryError("Prometheus 返回的 JSON 顶层不是对象")
    if payload.get("status") != "success":
        error_type = payload.get("errorType") or "unknown"
        message = payload.get("error") or "unknown error"
        raise QueryError(f"Prometheus 查询失败 ({error_type}): {message}")
    if "isPartial" in payload and not isinstance(payload["isPartial"], bool):
        raise QueryError("VictoriaMetrics isPartial 字段格式不正确")
    if payload.get("isPartial") is True:
        raise QueryError(
            "VictoriaMetrics 返回部分响应 (isPartial=true)，"
            "不能把结果作为完整证据；"
            "请检查集群状态或缩小查询范围后重试"
        )
    return payload


def response_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("warnings", "infos"):
        raw = payload.get(key)
        if raw is None:
            metadata[key] = []
        elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            metadata[key] = raw
        else:
            raise QueryError(f"Prometheus {key} 字段格式不正确")
    if "isPartial" in payload:
        partial = payload["isPartial"]
        if not isinstance(partial, bool):
            raise QueryError("VictoriaMetrics isPartial 字段格式不正确")
        if partial:
            raise QueryError(
                "VictoriaMetrics 返回部分响应 (isPartial=true)，"
                "不能把结果作为完整证据"
            )
        metadata["is_partial"] = partial
    if "stats" in payload:
        stats = payload["stats"]
        if not isinstance(stats, dict):
            raise QueryError("VictoriaMetrics stats 字段格式不正确")
        metadata["query_stats"] = stats
    return metadata


def limited(items: Sequence[Any], limit: int) -> tuple[list[Any], bool]:
    return list(items[:limit]), len(items) > limit


def evenly_sample(values: Sequence[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    if len(values) <= count:
        return list(values)
    if count == 1:
        return [values[-1]]
    indexes = {
        round(index * (len(values) - 1) / (count - 1))
        for index in range(count)
    }
    return [values[index] for index in sorted(indexes)]


def numeric_values(raw_values: Iterable[Sequence[Any]]) -> list[tuple[float, float]]:
    values: list[tuple[float, float]] = []
    for raw in raw_values:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        try:
            timestamp = float(raw[0])
            value = float(raw[1])
        except (TypeError, ValueError):
            continue
        if math.isfinite(timestamp) and math.isfinite(value):
            values.append((timestamp, value))
    return values


def summarize_series(
    series: dict[str, Any],
    timezone: ZoneInfo,
    trend_points: int,
) -> dict[str, Any]:
    values = numeric_values(series.get("values") or [])
    summary: dict[str, Any] = {
        "metric": series.get("metric") or {},
        "samples": len(values),
    }
    if not values:
        summary["note"] = "没有有限数值样本"
        return summary

    peak_timestamp, peak_value = max(values, key=lambda item: item[1])
    top_peaks = sorted(values, key=lambda item: item[1], reverse=True)[:5]
    trend = evenly_sample(values, trend_points)
    summary.update(
        {
            "min": min(value for _, value in values),
            "max": peak_value,
            "avg": sum(value for _, value in values) / len(values),
            "last": values[-1][1],
            "peak_at": format_timestamp(peak_timestamp, timezone),
            "top_peaks": [
                {"time": format_timestamp(timestamp, timezone), "value": value}
                for timestamp, value in top_peaks
            ],
            "trend": [
                {"time": format_timestamp(timestamp, timezone), "value": value}
                for timestamp, value in trend
            ],
        }
    )
    return summary


def query_metrics(
    base_url: str,
    contains: list[str],
    limit: int,
    minutes: int = 60,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> dict[str, Any]:
    if minutes < 1 or minutes > MAX_SERIES_MINUTES:
        raise QueryError(f"metrics 时间窗口必须在 1 到 {MAX_SERIES_MINUTES} 分钟之间")
    scan_limit = validate_scan_limit(scan_limit)
    end = time.time()
    start = end - minutes * 60
    payload = api_get(
        base_url,
        "/api/v1/label/__name__/values",
        [
            ("start", str(int(start))),
            ("end", str(int(end))),
            ("limit", str(scan_limit + 1)),
            ("deny_partial_response", "1"),
            ("timeout", f"{SERVER_TIMEOUT_SECONDS}s"),
        ],
    )
    raw_names = payload.get("data") or []
    if not isinstance(raw_names, list):
        raise QueryError("指标名响应格式不正确")
    scanned_names, scan_truncated = limited(raw_names, scan_limit)
    keywords = [keyword.lower() for keyword in contains if keyword.strip()]
    names = sorted(
        str(name)
        for name in scanned_names
        if not keywords or any(keyword in str(name).lower() for keyword in keywords)
    )
    returned, truncated = limited(names, limit)
    return {
        "operation": "metrics",
        **response_metadata(payload),
        "window_minutes": minutes,
        "keywords": contains,
        "scanned": len(scanned_names),
        "scan_limit": scan_limit,
        "scan_truncated": scan_truncated,
        "matched": len(names),
        "returned": len(returned),
        "truncated": truncated or scan_truncated,
        "metrics": returned,
    }


def query_series(
    base_url: str,
    metric: str,
    minutes: int,
    contains: list[str],
    limit: int,
    labels: Sequence[str] = (),
    scan_limit: int = DEFAULT_SCAN_LIMIT,
) -> dict[str, Any]:
    selector, exact_labels = build_series_selector(metric, labels)
    if minutes < 1 or minutes > MAX_SERIES_MINUTES:
        raise QueryError(f"series 时间窗口必须在 1 到 {MAX_SERIES_MINUTES} 分钟之间")
    scan_limit = validate_scan_limit(scan_limit)
    end = time.time()
    start = end - minutes * 60
    payload = api_get(
        base_url,
        "/api/v1/series",
        [
            ("match[]", selector),
            ("start", str(int(start))),
            ("end", str(int(end))),
            ("limit", str(scan_limit + 1)),
            ("deny_partial_response", "1"),
            ("timeout", f"{SERVER_TIMEOUT_SECONDS}s"),
        ],
    )
    raw_series = payload.get("data") or []
    if not isinstance(raw_series, list):
        raise QueryError("series 响应格式不正确")
    scanned_series, scan_truncated = limited(raw_series, scan_limit)
    keywords = [keyword.lower() for keyword in contains if keyword.strip()]
    series = [
        item
        for item in scanned_series
        if isinstance(item, dict)
        and (
            not keywords
            or any(keyword in str(value).lower() for keyword in keywords for value in item.values())
        )
    ]
    returned, truncated = limited(series, limit)
    return {
        "operation": "series",
        **response_metadata(payload),
        "metric": metric,
        "exact_labels": exact_labels,
        "window_minutes": minutes,
        "keywords": contains,
        "scanned": len(scanned_series),
        "scan_limit": scan_limit,
        "scan_truncated": scan_truncated,
        "matched": len(series),
        "returned": len(returned),
        "truncated": truncated or scan_truncated,
        "series": returned,
    }


def query_instant(
    base_url: str,
    query: str,
    evaluation_time: float | None,
    timezone: ZoneInfo,
    limit: int,
    allow_expensive: bool = False,
) -> dict[str, Any]:
    query = validate_query(query, allow_expensive)
    effective_time = evaluation_time if evaluation_time is not None else time.time()
    params = [
        ("query", query),
        ("time", str(effective_time)),
        ("timeout", f"{SERVER_TIMEOUT_SECONDS}s"),
        ("limit", str(limit + 1)),
        ("deny_partial_response", "1"),
    ]
    payload = api_get(base_url, "/api/v1/query", params)
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise QueryError("即时查询响应格式不正确")
    result_type = data.get("resultType")
    result = data.get("result")
    truncated = False
    if result_type in {"vector", "matrix"}:
        if not isinstance(result, list):
            raise QueryError("即时查询 result 格式不正确")
        result, truncated = limited(result, limit)
    return {
        "operation": "instant",
        **response_metadata(payload),
        "query": query,
        "evaluation_time": format_timestamp(effective_time, timezone),
        "timezone": str(timezone),
        "result_type": result_type,
        "truncated": truncated,
        "result": result,
    }


def query_range(
    base_url: str,
    query: str,
    start: float,
    end: float,
    step: int,
    timezone: ZoneInfo,
    limit: int,
    trend_points: int,
    allow_expensive: bool = False,
) -> dict[str, Any]:
    query = validate_query(query, allow_expensive)
    expected_points = validate_range(start, end, step)
    if trend_points < 2 or trend_points > MAX_TREND_POINTS:
        raise QueryError(f"trend-points 必须在 2 到 {MAX_TREND_POINTS} 之间")
    payload = api_get(
        base_url,
        "/api/v1/query_range",
        [
            ("query", query),
            ("start", str(start)),
            ("end", str(end)),
            ("step", str(step)),
            ("timeout", f"{SERVER_TIMEOUT_SECONDS}s"),
            ("limit", str(limit + 1)),
            ("deny_partial_response", "1"),
        ],
    )
    data = payload.get("data") or {}
    if not isinstance(data, dict) or data.get("resultType") != "matrix":
        raise QueryError("range 查询未返回 matrix")
    raw_series = data.get("result") or []
    if not isinstance(raw_series, list):
        raise QueryError("range 查询 result 格式不正确")
    valid_series = [series for series in raw_series if isinstance(series, dict)]
    returned_series, truncated = limited(valid_series, limit)
    summaries = [
        summarize_series(series, timezone, trend_points)
        for series in returned_series
    ]
    return {
        "operation": "range",
        **response_metadata(payload),
        "query": query,
        "start": format_timestamp(start, timezone),
        "end": format_timestamp(end, timezone),
        "timezone": str(timezone),
        "step_seconds": step,
        "expected_points_per_series": expected_points,
        "series_count": len(valid_series),
        "series_count_is_lower_bound": truncated,
        "received_series_count": len(valid_series),
        "returned": len(summaries),
        "truncated": truncated,
        "series": summaries,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="执行有边界的 Prometheus/VictoriaMetrics 只读查询"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    metrics = subparsers.add_parser("metrics", help="按关键词发现指标名")
    metrics.add_argument(
        "--contains",
        action="append",
        default=[],
        help="关键词，可重复；多个关键词按 OR 匹配",
    )
    metrics.add_argument(
        "--minutes",
        type=int,
        default=60,
        help="发现窗口，默认最近 60 分钟",
    )
    metrics.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)
    metrics.add_argument("--scan-limit", type=int, default=DEFAULT_SCAN_LIMIT)

    series = subparsers.add_parser("series", help="查看精确指标名的 series 和 labels")
    series.add_argument("--metric", required=True)
    series.add_argument("--minutes", type=int, default=60)
    series.add_argument(
        "--label",
        action="append",
        default=[],
        help="服务端精确 label matcher，格式 name=value，可重复",
    )
    series.add_argument(
        "--contains",
        action="append",
        default=[],
        help="客户端按 label 值过滤，可重复；多个关键词按 OR 匹配",
    )
    series.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)
    series.add_argument("--scan-limit", type=int, default=DEFAULT_SCAN_LIMIT)

    instant = subparsers.add_parser("instant", help="执行即时 PromQL")
    instant.add_argument("--query", required=True)
    instant.add_argument("--time", help="RFC 3339 时间或 Unix 时间戳")
    instant.add_argument("--timezone", default="UTC", help="输出时区，默认 UTC")
    instant.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)
    instant.add_argument(
        "--allow-expensive",
        action="store_true",
        help="在已展示查询风险并获得明确确认后放宽内部回看/子查询限制",
    )

    range_parser = subparsers.add_parser("range", help="执行范围 PromQL 并输出统计摘要")
    range_parser.add_argument("--query", required=True)
    range_parser.add_argument("--start", required=True, help="带时区的 RFC 3339 或 Unix 时间戳")
    range_parser.add_argument("--end", required=True, help="带时区的 RFC 3339 或 Unix 时间戳")
    range_parser.add_argument("--step", type=int, required=True, help="采样间隔，单位秒")
    range_parser.add_argument("--timezone", default="UTC", help="输出时区，默认 UTC")
    range_parser.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)
    range_parser.add_argument("--trend-points", type=int, default=DEFAULT_TREND_POINTS)
    range_parser.add_argument(
        "--allow-expensive",
        action="store_true",
        help="在已展示查询风险并获得明确确认后放宽内部回看/子查询限制",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        base_url = runtime_base_url()
        limit = validate_limit(args.limit)
        if args.command == "metrics":
            result = query_metrics(
                base_url,
                args.contains,
                limit,
                args.minutes,
                args.scan_limit,
            )
        elif args.command == "series":
            result = query_series(
                base_url,
                args.metric,
                args.minutes,
                args.contains,
                limit,
                args.label,
                args.scan_limit,
            )
        elif args.command == "instant":
            timezone = validate_timezone(args.timezone)
            evaluation_time = parse_timestamp(args.time) if args.time else None
            result = query_instant(
                base_url,
                args.query,
                evaluation_time,
                timezone,
                limit,
                args.allow_expensive,
            )
        else:
            timezone = validate_timezone(args.timezone)
            result = query_range(
                base_url,
                args.query,
                parse_timestamp(args.start),
                parse_timestamp(args.end),
                args.step,
                timezone,
                limit,
                args.trend_points,
                args.allow_expensive,
            )
    except QueryError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
