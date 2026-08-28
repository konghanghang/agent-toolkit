from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SKILL_DIR = Path(__file__).resolve().parents[1]
TOOLKIT_ROOT = SKILL_DIR.parents[1]
COMMON_DIR = TOOLKIT_ROOT / "common"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(COMMON_DIR))
try:
    query = load_module(
        "prometheus_query_test",
        SKILL_DIR / "scripts" / "prometheus_query.py",
    )
finally:
    sys.path.pop(0)


class FakeResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Length": str(len(self.body))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size: int) -> bytes:
        return self.body[:size]


class PrometheusQueryTest(unittest.TestCase):
    def test_base_url_rejects_embedded_credentials_and_query(self) -> None:
        self.assertEqual(
            "https://monitor.example/prometheus",
            query.validate_base_url("https://monitor.example/prometheus/"),
        )
        with self.assertRaisesRegex(query.QueryError, "用户名或密码"):
            query.validate_base_url("https://user:secret@monitor.example/prometheus")
        with self.assertRaisesRegex(query.QueryError, "query string"):
            query.validate_base_url("https://monitor.example/prometheus?token=secret")

    def test_runtime_requires_separate_prometheus_url(self) -> None:
        with self.assertRaisesRegex(query.QueryError, "缺少 prometheus_base_url"):
            query.runtime_base_url({"agent_toolkit_root": str(TOOLKIT_ROOT)})

    def test_range_guard_limits_duration_and_points(self) -> None:
        self.assertEqual(121, query.validate_range(0, 3600, 30))
        with self.assertRaisesRegex(query.QueryError, "最多覆盖 3 天"):
            query.validate_range(0, query.MAX_RANGE_SECONDS + 1, 600)
        with self.assertRaisesRegex(query.QueryError, "最多允许 1440"):
            query.validate_range(0, 3600, 1)

    def test_api_get_uses_get_and_encodes_promql(self) -> None:
        payload = {
            "status": "success",
            "data": {"resultType": "vector", "result": []},
        }
        with patch.object(
            query.urllib_request,
            "urlopen",
            return_value=FakeResponse(payload),
        ) as urlopen:
            result = query.api_get(
                "https://monitor.example/prometheus",
                "/api/v1/query",
                [("query", 'metric{group="a%b"}')],
            )

        request = urlopen.call_args.args[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual("GET", request.get_method())
        self.assertEqual(
            ['metric{group="a%b"}'],
            parse_qs(parsed.query)["query"],
        )
        self.assertEqual("success", result["status"])

    def test_api_get_rejects_victoriametrics_partial_response(self) -> None:
        payload = {
            "status": "success",
            "isPartial": True,
            "data": {"resultType": "vector", "result": []},
        }
        with patch.object(
            query.urllib_request,
            "urlopen",
            return_value=FakeResponse(payload),
        ):
            with self.assertRaisesRegex(query.QueryError, "部分响应"):
                query.api_get(
                    "https://monitor.example/prometheus",
                    "/api/v1/query",
                    [("query", "vector(1)")],
                )

    def test_metrics_filters_and_truncates(self) -> None:
        payload = {
            "status": "success",
            "data": ["node_cpu", "rocketmq_group_diff", "rocketmq_tps"],
        }
        with patch.object(query, "api_get", return_value=payload):
            result = query.query_metrics(
                "https://monitor.example/prometheus",
                ["rocket"],
                1,
            )

        self.assertEqual(2, result["matched"])
        self.assertEqual(["rocketmq_group_diff"], result["metrics"])
        self.assertTrue(result["truncated"])

    def test_metrics_bounds_server_scan_and_time_window(self) -> None:
        payload = {
            "status": "success",
            "data": ["metric_a", "metric_b", "metric_c", "metric_d"],
        }
        with patch.object(query, "api_get", return_value=payload) as api_get, patch.object(
            query.time,
            "time",
            return_value=3600,
        ):
            result = query.query_metrics(
                "https://monitor.example/prometheus",
                [],
                2,
                minutes=30,
                scan_limit=3,
            )

        params = dict(api_get.call_args.args[2])
        self.assertEqual("1800", params["start"])
        self.assertEqual("3600", params["end"])
        self.assertEqual("4", params["limit"])
        self.assertEqual("1", params["deny_partial_response"])
        self.assertTrue(result["scan_truncated"])
        self.assertTrue(result["truncated"])

    def test_series_requires_exact_metric_name(self) -> None:
        with self.assertRaisesRegex(query.QueryError, "精确指标名"):
            query.query_series(
                "https://monitor.example/prometheus",
                'metric{job="x"}',
                60,
                [],
                10,
            )

    def test_series_sends_exact_labels_and_scan_limit_to_server(self) -> None:
        payload = {"status": "success", "data": []}
        with patch.object(query, "api_get", return_value=payload) as api_get:
            result = query.query_series(
                "https://monitor.example/prometheus",
                "rocketmq_group_diff",
                60,
                [],
                10,
                labels=['group=example"group', "topic=example-topic"],
                scan_limit=25,
            )

        params = dict(api_get.call_args.args[2])
        self.assertEqual(
            'rocketmq_group_diff{group="example\\"group",topic="example-topic"}',
            params["match[]"],
        )
        self.assertEqual("26", params["limit"])
        self.assertEqual(
            {"group": 'example"group', "topic": "example-topic"},
            result["exact_labels"],
        )

    def test_series_rejects_duplicate_or_invalid_labels(self) -> None:
        with self.assertRaisesRegex(query.QueryError, "label 重复"):
            query.build_series_selector("metric", ["group=a", "group=b"])
        with self.assertRaisesRegex(query.QueryError, "name=value"):
            query.build_series_selector("metric", ["group"])

    def test_expensive_promql_requires_explicit_override(self) -> None:
        with self.assertRaisesRegex(query.QueryError, "内部回看窗口 4d"):
            query.validate_query("sum_over_time(metric[4d])")
        with self.assertRaisesRegex(query.QueryError, "内部评估点"):
            query.validate_query("max_over_time(metric[1h:1s])")
        self.assertEqual(
            "sum_over_time(metric[4d])",
            query.validate_query("sum_over_time(metric[4d])", allow_expensive=True),
        )

    def test_cost_check_ignores_brackets_inside_label_strings(self) -> None:
        expression = 'metric{pattern=~"[0-9]+"}[5m]'
        self.assertEqual(expression, query.validate_query(expression))

    def test_instant_uses_request_time_and_preserves_response_metadata(self) -> None:
        payload = {
            "status": "success",
            "warnings": ["warning"],
            "infos": ["info"],
            "isPartial": False,
            "stats": {"seriesFetched": "1"},
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [100, "1"]}],
            },
        }
        with patch.object(query, "api_get", return_value=payload) as api_get, patch.object(
            query.time,
            "time",
            return_value=100,
        ) as current_time:
            result = query.query_instant(
                "https://monitor.example/prometheus",
                "vector(1)",
                None,
                query.validate_timezone("UTC"),
                10,
            )

        params = dict(api_get.call_args.args[2])
        self.assertEqual("100", params["time"])
        self.assertEqual("11", params["limit"])
        self.assertEqual("1970-01-01T00:01:40+00:00", result["evaluation_time"])
        self.assertEqual(["warning"], result["warnings"])
        self.assertEqual(["info"], result["infos"])
        self.assertFalse(result["is_partial"])
        self.assertEqual({"seriesFetched": "1"}, result["query_stats"])
        current_time.assert_called_once_with()

    def test_range_summary_reports_peak_time_and_bounded_trend(self) -> None:
        payload = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {"broker": "broker-a"},
                        "values": [
                            [0, "1"],
                            [60, "9"],
                            [120, "3"],
                            [180, "NaN"],
                        ],
                    }
                ],
            },
        }
        with patch.object(query, "api_get", return_value=payload):
            result = query.query_range(
                "https://monitor.example/prometheus",
                "metric",
                0,
                180,
                60,
                query.validate_timezone("UTC"),
                10,
                2,
            )

        summary = result["series"][0]
        self.assertEqual(3, summary["samples"])
        self.assertEqual(9.0, summary["max"])
        self.assertEqual("1970-01-01T00:01:00+00:00", summary["peak_at"])
        self.assertEqual(2, len(summary["trend"]))

    def test_naive_iso_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(query.QueryError, "必须包含时区"):
            query.parse_timestamp("2026-08-17T10:00:00")

    def test_non_finite_timestamp_is_rejected(self) -> None:
        with self.assertRaisesRegex(query.QueryError, "有限数值"):
            query.parse_timestamp("NaN")

    def test_out_of_range_timestamp_and_invalid_timezone_are_rejected(self) -> None:
        with self.assertRaisesRegex(query.QueryError, "超出支持范围"):
            query.parse_timestamp("1e308")
        with self.assertRaisesRegex(query.QueryError, "未知时区"):
            query.validate_timezone("/etc/passwd")

    def test_unix_examples_use_the_project_interpreter(self) -> None:
        paths = [
            SKILL_DIR / "SKILL.md",
            SKILL_DIR / "references" / "rocketmq-triage.md",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertNotRegex(content, r"(?m)^PYTHON_BIN ")
                self.assertIn('$AGENT_TOOLKIT_ROOT/.venv/bin/python', content)


if __name__ == "__main__":
    unittest.main()
