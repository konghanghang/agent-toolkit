#!/usr/bin/env python3
"""Run bounded, read-only Prometheus HTTP API queries."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime
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
MAX_TREND_POINTS = 120
DEFAULT_TREND_POINTS = 24
METRIC_NAME_RE = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")


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


def validate_query(query: str) -> str:
    query = query.strip()
    if not query:
        raise QueryError("PromQL 不能为空")
    if len(query) > MAX_QUERY_LENGTH:
        raise QueryError(f"PromQL 长度不能超过 {MAX_QUERY_LENGTH} 个字符")
    return query


def validate_limit(value: int) -> int:
    if value < 1 or value > MAX_RESULT_LIMIT:
        raise QueryError(f"limit 必须在 1 到 {MAX_RESULT_LIMIT} 之间")
    return value


def validate_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise QueryError(f"未知时区: {name}") from exc


def parse_timestamp(value: str) -> float:
    raw = value.strip()
    try:
        timestamp = float(raw)
    except ValueError:
        pass
    else:
        if not math.isfinite(timestamp):
            raise QueryError(f"时间必须是有限数值: {value}")
        return timestamp

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise QueryError(f"无法解析时间: {value}") from exc
    if parsed.tzinfo is None:
        raise QueryError(f"时间必须包含时区偏移: {value}")
    timestamp = parsed.timestamp()
    if not math.isfinite(timestamp):
        raise QueryError(f"时间必须是有限数值: {value}")
    return timestamp


def format_timestamp(value: float, timezone: ZoneInfo) -> str:
    return datetime.fromtimestamp(value, timezone).isoformat(timespec="seconds")


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
    return payload


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


def query_metrics(base_url: str, contains: list[str], limit: int) -> dict[str, Any]:
    payload = api_get(base_url, "/api/v1/label/__name__/values", [])
    raw_names = payload.get("data") or []
    if not isinstance(raw_names, list):
        raise QueryError("指标名响应格式不正确")
    keywords = [keyword.lower() for keyword in contains if keyword.strip()]
    names = sorted(
        str(name)
        for name in raw_names
        if not keywords or any(keyword in str(name).lower() for keyword in keywords)
    )
    returned, truncated = limited(names, limit)
    return {
        "operation": "metrics",
        "keywords": contains,
        "matched": len(names),
        "returned": len(returned),
        "truncated": truncated,
        "metrics": returned,
    }


def query_series(
    base_url: str,
    metric: str,
    minutes: int,
    contains: list[str],
    limit: int,
) -> dict[str, Any]:
    if not METRIC_NAME_RE.fullmatch(metric):
        raise QueryError(f"不是有效的精确指标名: {metric}")
    if minutes < 1 or minutes > MAX_SERIES_MINUTES:
        raise QueryError(f"series 时间窗口必须在 1 到 {MAX_SERIES_MINUTES} 分钟之间")
    end = time.time()
    start = end - minutes * 60
    payload = api_get(
        base_url,
        "/api/v1/series",
        [
            ("match[]", metric),
            ("start", str(int(start))),
            ("end", str(int(end))),
        ],
    )
    raw_series = payload.get("data") or []
    if not isinstance(raw_series, list):
        raise QueryError("series 响应格式不正确")
    keywords = [keyword.lower() for keyword in contains if keyword.strip()]
    series = [
        item
        for item in raw_series
        if isinstance(item, dict)
        and (
            not keywords
            or any(keyword in str(value).lower() for keyword in keywords for value in item.values())
        )
    ]
    returned, truncated = limited(series, limit)
    return {
        "operation": "series",
        "metric": metric,
        "window_minutes": minutes,
        "keywords": contains,
        "matched": len(series),
        "returned": len(returned),
        "truncated": truncated,
        "series": returned,
    }


def query_instant(
    base_url: str,
    query: str,
    evaluation_time: float | None,
    timezone: ZoneInfo,
    limit: int,
) -> dict[str, Any]:
    query = validate_query(query)
    params = [("query", query), ("timeout", f"{SERVER_TIMEOUT_SECONDS}s")]
    if evaluation_time is not None:
        params.append(("time", str(evaluation_time)))
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
    effective_time = evaluation_time if evaluation_time is not None else time.time()
    return {
        "operation": "instant",
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
) -> dict[str, Any]:
    query = validate_query(query)
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
        ],
    )
    data = payload.get("data") or {}
    if not isinstance(data, dict) or data.get("resultType") != "matrix":
        raise QueryError("range 查询未返回 matrix")
    raw_series = data.get("result") or []
    if not isinstance(raw_series, list):
        raise QueryError("range 查询 result 格式不正确")
    summaries = [
        summarize_series(series, timezone, trend_points)
        for series in raw_series
        if isinstance(series, dict)
    ]
    returned, truncated = limited(summaries, limit)
    return {
        "operation": "range",
        "query": query,
        "start": format_timestamp(start, timezone),
        "end": format_timestamp(end, timezone),
        "timezone": str(timezone),
        "step_seconds": step,
        "expected_points_per_series": expected_points,
        "series_count": len(summaries),
        "returned": len(returned),
        "truncated": truncated,
        "series": returned,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="执行有边界的 Prometheus/VictoriaMetrics 只读查询"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    metrics = subparsers.add_parser("metrics", help="按关键词发现指标名")
    metrics.add_argument("--contains", action="append", default=[], help="关键词，可重复")
    metrics.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)

    series = subparsers.add_parser("series", help="查看精确指标名的 series 和 labels")
    series.add_argument("--metric", required=True)
    series.add_argument("--minutes", type=int, default=60)
    series.add_argument("--contains", action="append", default=[], help="按 label 值过滤，可重复")
    series.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)

    instant = subparsers.add_parser("instant", help="执行即时 PromQL")
    instant.add_argument("--query", required=True)
    instant.add_argument("--time", help="RFC 3339 时间或 Unix 时间戳")
    instant.add_argument("--timezone", default="UTC", help="输出时区，默认 UTC")
    instant.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)

    range_parser = subparsers.add_parser("range", help="执行范围 PromQL 并输出统计摘要")
    range_parser.add_argument("--query", required=True)
    range_parser.add_argument("--start", required=True, help="带时区的 RFC 3339 或 Unix 时间戳")
    range_parser.add_argument("--end", required=True, help="带时区的 RFC 3339 或 Unix 时间戳")
    range_parser.add_argument("--step", type=int, required=True, help="采样间隔，单位秒")
    range_parser.add_argument("--timezone", default="UTC", help="输出时区，默认 UTC")
    range_parser.add_argument("--limit", type=int, default=DEFAULT_RESULT_LIMIT)
    range_parser.add_argument("--trend-points", type=int, default=DEFAULT_TREND_POINTS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        base_url = runtime_base_url()
        limit = validate_limit(args.limit)
        if args.command == "metrics":
            result = query_metrics(base_url, args.contains, limit)
        elif args.command == "series":
            result = query_series(base_url, args.metric, args.minutes, args.contains, limit)
        elif args.command == "instant":
            timezone = validate_timezone(args.timezone)
            evaluation_time = parse_timestamp(args.time) if args.time else None
            result = query_instant(base_url, args.query, evaluation_time, timezone, limit)
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
            )
    except QueryError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
