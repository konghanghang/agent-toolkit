from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
from sls_config import SlsRoute, constrain_window, infer_project_route, select_route
from work_config import WorkConfigError


def target(project="example-project", logstore="example-logs"):
    return {"project": project, "logstore": logstore}


def flat():
    return {"retention_days": 3, "default": target(), "services": {}}


def multiple():
    return {"scopes": {
        "production": {"retention_days": 3, "default": target(), "services": {}},
        "staging": {"retention_days": 7, "default": target("staging-project", "staging-logs"), "services": {}},
    }}


class SlsConfigTest(unittest.TestCase):
    def test_single_and_only_named_environment_do_not_need_a_scope(self):
        route = select_route(flat(), service="example-api")
        self.assertEqual(SlsRoute("example-project", "example-logs", 3, "", "example-api"), route)
        for name in ("overseas", "production"):
            route = select_route({"scopes": {name: flat()}}, service="example-api")
            self.assertEqual(name, route.scope)
            self.assertEqual(3, route.retention_days)

    def test_multiple_environments_require_an_unambiguous_selection(self):
        config = multiple()
        with self.assertRaisesRegex(WorkConfigError, "无法确定"):
            select_route(config, service="example-api")
        self.assertEqual("staging", select_route(config, "staging").scope)
        config["default_scope"] = "staging"
        self.assertEqual("staging", select_route(config, service="example-api").scope)
        self.assertEqual("production", select_route(config, "production").scope)
        with self.assertRaisesRegex(WorkConfigError, "未配置"):
            select_route(config, "unknown")

    def test_unique_service_inference_and_ambiguous_services(self):
        config = multiple()
        config["scopes"]["staging"]["services"]["example-api"] = target("api-project", "api-logs")
        for kwargs in ({"service": "example-api"}, {"query_text": "example-api and ERROR"}):
            route = select_route(config, **kwargs)
            self.assertEqual(("staging", "example-api", "api-project", 7), (route.scope, route.service, route.project, route.retention_days))
        config["scopes"]["production"]["services"]["example-api"] = target()
        with self.assertRaisesRegex(WorkConfigError, "无法确定"):
            select_route(config, service="example-api")
        config["scopes"]["production"]["services"]["example-worker"] = target()
        with self.assertRaisesRegex(WorkConfigError, "多个已配置服务"):
            select_route(config, query_text="example-api or example-worker")

    def test_service_retention_inherits_overrides_and_can_be_unknown(self):
        config = flat()
        config["services"]["example-api"] = target("api-project", "api-logs")
        self.assertEqual(3, select_route(config, service="example-api").retention_days)
        config["services"]["example-api"]["retention_days"] = 7
        self.assertEqual(7, select_route(config, service="example-api").retention_days)
        config["services"]["example-api"]["retention_days"] = None
        self.assertIsNone(select_route(config, service="example-api").retention_days)

    def test_legacy_scopes_preserve_default_and_aliases(self):
        config = {"domestic": flat(), "overseas": flat()}
        self.assertEqual("domestic", select_route(config, service="example-api").scope)
        for alias in ("overseas", "intl", "global"):
            self.assertEqual("overseas", select_route(config, alias).scope)
        self.assertEqual("domestic", select_route(config, "cn").scope)
        self.assertEqual("overseas", select_route({"overseas": flat()}).scope)
        self.assertEqual("cn", select_route({"scopes": {"cn": flat()}}, "cn").scope)

    def test_invalid_configuration_and_retention_are_rejected(self):
        cases = [
            {"scopes": {}, **flat()},
            {"scopes": {"production": flat()}, "domestic": flat()},
            {"domestic": flat(), **flat()},
            {"scopes": {}},
            {"scopes": {"": flat()}},
            {"scopes": {" production": flat()}},
            {"scopes": {"production": []}},
            {"default_scope": "unknown", **multiple()},
            {"default_scope": "production", **flat()},
        ]
        for value in (0, -1, True, 1.5, "3"):
            cases.append({**flat(), "retention_days": value})
            config = flat()
            config["services"]["example-api"] = {**target(), "retention_days": value}
            cases.append(config)
        for config in cases:
            with self.subTest(config=config), self.assertRaises(WorkConfigError):
                select_route(config)

    def test_project_inference_requires_a_unique_logstore(self):
        config = flat()
        self.assertEqual("example-logs", infer_project_route(config, "example-project").logstore)
        self.assertIsNone(infer_project_route(config, "unknown-project"))
        config["services"]["example-api"] = target(logstore="other-logs")
        self.assertIsNone(infer_project_route(config, "example-project"))


class SlsRetentionTest(unittest.TestCase):
    NOW = 1_800_000_000
    BOUNDARY = NOW - 3 * 86400

    def test_partial_window_and_large_minutes_are_clipped(self):
        route = SlsRoute("example-project", "example-logs", 3)
        for kwargs in ({"from_ts": 0, "to_ts": self.NOW}, {"minutes": 7 * 1440}):
            start, end, messages = constrain_window(route, now=self.NOW, **kwargs)
            self.assertEqual((self.BOUNDARY, self.NOW), (start, end))
            self.assertIn("无法覆盖", " ".join(messages))
            self.assertIn("不能否定", " ".join(messages))

    def test_window_on_boundary_is_kept_and_wholly_expired_is_rejected(self):
        route = SlsRoute("example-project", "example-logs", 3)
        start, end, messages = constrain_window(route, from_ts=self.BOUNDARY, to_ts=self.NOW, now=self.NOW)
        self.assertEqual((self.BOUNDARY, self.NOW), (start, end))
        self.assertNotIn("裁剪", " ".join(messages))
        for start, end in ((0, 0), (0, self.BOUNDARY), (self.BOUNDARY - 1, self.BOUNDARY - 1)):
            with self.subTest(start=start, end=end), self.assertRaisesRegex(WorkConfigError, "全部超出"):
                constrain_window(route, from_ts=start, to_ts=end, now=self.NOW)

    def test_unknown_retention_does_not_default_to_three_days(self):
        start, end, messages = constrain_window(SlsRoute("example-project", "example-logs"), from_ts=0, to_ts=0, now=self.NOW)
        self.assertEqual((0, 0), (start, end))
        self.assertIn("保留天数未知", " ".join(messages))
        self.assertIn("不能根据 0 条", " ".join(messages))

    def test_invalid_minutes_and_reversed_windows_are_rejected(self):
        route = SlsRoute("example-project", "example-logs", 3)
        for minutes in (0, -1, True):
            with self.assertRaises(WorkConfigError):
                constrain_window(route, minutes=minutes, now=self.NOW)
        with self.assertRaisesRegex(WorkConfigError, "不能早于"):
            constrain_window(route, from_ts=self.NOW, to_ts=self.NOW - 1, now=self.NOW)


class SlsCliConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("sls_config_cli_test", ROOT / "common/entrypoints/sls_cli.py")
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def setUp(self):
        self.config = {"schema_version": 1, "sls": flat()}
        patcher = patch.object(self.cli, "load_work_config", return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        output = contextlib.redirect_stderr(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_single_target_safe_search_and_aggregate_use_the_same_route(self):
        query = self.cli.resolve_route_args(self.cli.resolve_search_safe_args(["--service", "example-api"]))
        agg = self.cli.resolve_agg_args(self.cli.resolve_agg_safe_args(["--service", "example-api", "--sql", "SELECT count(*) AS c"]))
        for args in (query, agg):
            self.assertEqual("example-project", args[args.index("--project") + 1])
            self.assertIn("--redact", args)

    def test_custom_scope_is_accepted_by_safe_entrypoints(self):
        self.config["sls"] = multiple()
        for args in (
            self.cli.resolve_route_args(self.cli.resolve_search_safe_args(["--scope=staging", "--service=example-api"])),
            self.cli.resolve_agg_args(self.cli.resolve_agg_safe_args(["--scope=staging", "--service=example-api", "--sql=SELECT count(*)"])),
        ):
            self.assertEqual("staging-project", args[args.index("--project") + 1])

    def test_explicit_logstore_is_not_overwritten_or_given_wrong_retention(self):
        args = self.cli.resolve_route_args(["--service=example-api", "--logstore=requested-logs", "--from=0", "--to=0"])
        self.assertEqual(1, args.count("--logstore"))
        self.assertEqual("requested-logs", args[args.index("--logstore") + 1])
        self.assertEqual("0", args[args.index("--from") + 1])

    def test_explicit_complete_target_preserves_service_and_index_without_reading_config(self):
        target_args = ["--project=selected-project", "--logstore=selected-logs"]
        with patch.object(self.cli, "load_work_config", side_effect=AssertionError("complete target must not read routes")):
            for service_args, index_args, expected in (
                (["--service=wanted-service"], [], "wanted-service"),
                ([], [], "*"),
                (["--service=wanted-service"], ["--index=custom-filter and ERROR"], "custom-filter and ERROR"),
            ):
                with self.subTest(service_args=service_args, index_args=index_args):
                    args = self.cli.resolve_agg_args([*target_args, *service_args, *index_args, "--sql=SELECT count(*) AS c"])
                    self.assertEqual(f"{expected} | SELECT count(*) AS c", args[args.index("--query") + 1])
            query = self.cli.resolve_route_args([*target_args, "--service=wanted-service"])
            self.assertEqual("wanted-service | with_pack_meta", query[query.index("--query") + 1])

    def test_project_only_inference_never_inherits_the_configured_route_service(self):
        self.config["sls"]["services"]["other-service"] = target("selected-project", "selected-logs")
        for service_args, index_args, expected in (
            (["--service=wanted-service"], [], "wanted-service"),
            ([], [], "*"),
            (["--service=wanted-service"], ["--index=custom-filter"], "custom-filter"),
        ):
            with self.subTest(service_args=service_args, index_args=index_args):
                args = self.cli.resolve_agg_args(["--project=selected-project", *service_args, *index_args, "--sql=SELECT count(*) AS c"])
                self.assertEqual("selected-logs", args[args.index("--logstore") + 1])
                self.assertEqual(f"{expected} | SELECT count(*) AS c", args[args.index("--query") + 1])
        query = self.cli.resolve_route_args(["--project=selected-project", "--service=wanted-service"])
        self.assertEqual("wanted-service | with_pack_meta", query[query.index("--query") + 1])
        no_service = self.cli.resolve_route_args(["--project=selected-project"])
        self.assertNotIn("--query", no_service)

    def test_expired_query_and_aggregate_stop_before_runtime_login_or_network(self):
        for kind in ("query", "agg", "search-safe", "agg-safe"):
            args = ["--service=example-api", "--from=0", "--to=0"]
            if "agg" in kind:
                args.append("--sql=SELECT count(*)")
            with self.subTest(kind=kind), patch.object(self.cli, "runtime_and_paths") as runtime, patch.object(self.cli, "run_login_auto") as login, patch.object(self.cli, "run_subprocess") as network:
                with self.assertRaisesRegex(SystemExit, "全部超出"):
                    self.cli.main([kind, *args])
                runtime.assert_not_called()
                login.assert_not_called()
                network.assert_not_called()

    def test_partial_window_is_clipped_for_query_and_aggregate(self):
        for resolve, args in (
            (self.cli.resolve_route_args, ["--service=example-api", "--from=0"]),
            (self.cli.resolve_agg_args, ["--service=example-api", "--from=0", "--sql=SELECT count(*)"]),
        ):
            with patch("sls_config.time.time", return_value=SlsRetentionTest.NOW):
                resolved = resolve(args)
            self.assertEqual(str(SlsRetentionTest.BOUNDARY), resolved[resolved.index("--from") + 1])


if __name__ == "__main__":
    unittest.main()
