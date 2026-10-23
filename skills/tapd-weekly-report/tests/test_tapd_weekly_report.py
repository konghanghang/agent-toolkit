from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "tapd_weekly_report.py"
MEMBERS = ["成员甲", "成员乙", "成员丙"]


def load_module():
    spec = importlib.util.spec_from_file_location("tapd_weekly_report_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


weekly = load_module()


class FakeResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def story(name: str, owner: str, status: str = "resolved") -> dict:
    return {
        "Story": {
            "name": name,
            "owner": owner,
            "status": status,
        }
    }


class TapdWeeklyReportTest(unittest.TestCase):
    def test_owner_parsing_and_classification_follow_team_rules(self) -> None:
        self.assertEqual(
            ["成员丙", "成员甲", "成员乙"],
            weekly.parse_owners("成员丙; 成员甲 成员乙"),
        )
        self.assertEqual(
            ["成员丙", "成员甲", "成员乙"],
            weekly.parse_owners("成员丙; 成员甲 成员乙", MEMBERS),
        )
        cases = {
            ("成员丙",): "成员丙",
            ("成员丙", "成员甲"): "成员甲",
            ("成员丙", "成员乙"): "成员乙",
            ("成员丙", "成员甲", "成员乙"): "成员甲",
            ("成员甲", "成员乙"): "成员甲",
            ("成员乙",): "成员乙",
            ("其他成员",): None,
        }
        for owners, expected in cases.items():
            with self.subTest(owners=owners):
                self.assertEqual(expected, weekly.classify_story(list(owners), MEMBERS))

    def test_process_stories_filters_and_normalizes(self) -> None:
        processed = weekly.process_stories(
            [
                story("【客户需求】【进行中】OpenAPI 接口", "成员丙;成员甲"),
                story("【内部】监控改造", "成员乙"),
                story("【废弃】不会进入周报", "成员丙", status="rejected"),
                story("外部团队事项", "其他成员"),
            ],
            MEMBERS,
        )

        self.assertEqual(
            ["OpenAPI 接口"],
            [item["name"] for item in processed["成员甲"]],
        )
        self.assertEqual(
            ["监控改造"],
            [item["name"] for item in processed["成员乙"]],
        )
        self.assertEqual([], processed["成员丙"])
        self.assertEqual(
            "【客户需求】【进行中】OpenAPI 接口",
            processed["成员甲"][0]["original_name"],
        )

    def test_process_stories_preserves_configured_names_with_spaces(self) -> None:
        members = ["Alice Smith", "Bob Jones", "Alice", "成员甲"]
        self.assertEqual(["Alice", "Smith"], weekly.parse_owners("Alice;Smith", members))
        processed = weekly.process_stories(
            [
                story("独立需求", "Alice Smith"),
                story("共同需求", "Bob Jones Alice Smith; 成员甲"),
            ],
            members,
        )

        self.assertEqual(
            ["独立需求", "共同需求"],
            [item["name"] for item in processed["Alice Smith"]],
        )
        self.assertEqual(
            ["Bob Jones", "Alice Smith", "成员甲"],
            processed["Alice Smith"][1]["owners"],
        )
        self.assertEqual([], processed["Alice"])
        self.assertEqual([], processed["Bob Jones"])

    def test_fetch_stories_uses_bearer_get_and_validates_api_status(self) -> None:
        payload = {"status": 1, "data": [story("需求", "成员丙")]}
        with patch.object(weekly, "urlopen", return_value=FakeResponse(payload)) as urlopen:
            result = weekly.fetch_stories(
                "2026-0115",
                "test-token",
                "workspace",
                "https://tapd.example/api/",
            )

        self.assertEqual(payload["data"], result)
        request = urlopen.call_args.args[0]
        parsed = urlsplit(request.full_url)
        self.assertEqual("GET", request.get_method())
        self.assertEqual("Bearer test-token", request.get_header("Authorization"))
        self.assertEqual(["workspace"], parse_qs(parsed.query)["workspace_id"])
        self.assertEqual(["2026-0115"], parse_qs(parsed.query)["version"])
        self.assertEqual(30, urlopen.call_args.kwargs["timeout"])

        with patch.object(
            weekly,
            "urlopen",
            return_value=FakeResponse({"status": 0, "info": "denied"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "denied"):
                weekly.fetch_stories(
                    "2026-0115",
                    "test-token",
                    "workspace",
                    "https://tapd.example",
                )

    def test_generate_weekly_report_preserves_dates_tasks_and_counts(self) -> None:
        completed = {name: [] for name in MEMBERS}
        planned = {name: [] for name in MEMBERS}
        completed["成员甲"] = [{"name": "完成 A"}]
        completed["成员丙"] = [{"name": "完成 B"}]
        planned["成员乙"] = [{"name": "计划 C"}]

        report, completed_counts, planned_counts = weekly.generate_weekly_report(
            completed,
            planned,
            "2026-01-12",
            MEMBERS,
        )

        self.assertIn("# 工作周报 (2026-01-12 ~ 2026-01-18)", report)
        self.assertIn("- 完成 A", report)
        self.assertIn("- 完成 B", report)
        self.assertIn("- 计划 C", report)
        self.assertIn("共 2 个需求", report)
        self.assertIn("共 1 个需求", report)
        self.assertEqual(
            {"成员甲": 1, "成员乙": 0, "成员丙": 1},
            completed_counts,
        )
        self.assertEqual(
            {"成员甲": 0, "成员乙": 1, "成员丙": 0},
            planned_counts,
        )

    def test_version_and_output_path_validation(self) -> None:
        self.assertEqual(15, weekly.parse_version_date("2026-0115").day)
        with self.assertRaisesRegex(ValueError, "YYYY-MMDD"):
            weekly.parse_version_date("2026-01-15")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "reports"
            dry_path = weekly.build_output_path(
                root,
                "2026-01-12",
                ensure_dir=False,
            )
            self.assertEqual(
                root / "2026" / "01" / "weekly-report-2026-01-12.md",
                dry_path,
            )
            self.assertFalse(dry_path.parent.exists())

            real_path = weekly.build_output_path(root, "2026-01-12")
            self.assertTrue(real_path.parent.is_dir())

    def test_dry_run_derives_next_version_without_network_or_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir) / "reports"
            output = io.StringIO()
            argv = [
                str(SCRIPT),
                "--version",
                "2026-0115",
                "--output-root",
                str(output_root),
                "--dry-run",
            ]
            with patch.object(sys, "argv", argv), patch.object(
                weekly,
                "fetch_stories",
                side_effect=AssertionError("dry-run must not use the network"),
            ), patch.object(
                weekly,
                "load_work_config",
                side_effect=AssertionError("dry-run must not require configuration"),
            ), redirect_stdout(output):
                code = weekly.main()

            self.assertEqual(0, code)
            self.assertIn("next_version=2026-0122", output.getvalue())
            self.assertIn("week_start=2026-01-12", output.getvalue())
            self.assertFalse(output_root.exists())

    def test_missing_token_stops_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            error = io.StringIO()
            argv = [
                str(SCRIPT),
                "--version",
                "2026-0115",
                "--output-root",
                str(Path(temp_dir) / "reports"),
            ]
            with patch.object(sys, "argv", argv), patch.dict(
                os.environ,
                {},
                clear=True,
            ), patch.object(
                weekly,
                "fetch_stories",
                side_effect=AssertionError("missing token must stop before network"),
            ), redirect_stderr(error):
                code = weekly.main()

        self.assertEqual(1, code)
        self.assertIn("缺少 TAPD_TOKEN", error.getvalue())

    def test_main_uses_configured_members_and_workspace_precedence(self) -> None:
        cases = (
            ({}, [], "workspace-config"),
            ({"TAPD_WORKSPACE_ID": "workspace-env"}, [], "workspace-env"),
            (
                {"TAPD_WORKSPACE_ID": "workspace-env"},
                ["--workspace-id", "workspace-cli"],
                "workspace-cli",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "work.json"
            for index, (environment, arguments, workspace_id) in enumerate(cases):
                members = ["成员丁", "成员甲"] if index == 0 else ["成员甲", "成员丁", "成员乙", "成员丙"]
                config_path.write_text(json.dumps({
                    "schema_version": 1,
                    "tapd_weekly_report": {
                        "workspace_id": "workspace-config",
                        "members": members,
                    },
                }))
                output_root = Path(temp_dir) / f"reports-{index}"
                argv = [
                    str(SCRIPT), "--version", "2026-0115", "--config", str(config_path),
                    "--output-root", str(output_root), *arguments,
                ]
                data = [story("【客户】完成需求", "成员甲;成员丁")]
                output = io.StringIO()
                with self.subTest(workspace_id=workspace_id), patch.object(
                    sys, "argv", argv
                ), patch.dict(
                    os.environ, {"TAPD_TOKEN": "test-token", **environment}, clear=True
                ), patch.object(
                    weekly, "fetch_stories", side_effect=[data, []]
                ) as fetch, redirect_stdout(output):
                    self.assertEqual(0, weekly.main())

                self.assertEqual(2, fetch.call_count)
                self.assertTrue(all(item.args[2] == workspace_id for item in fetch.call_args_list))
                report = (output_root / "2026" / "01" / "weekly-report-2026-01-12.md").read_text()
                first_member = members[0]
                self.assertIn(f"### {first_member}\n- 完成需求", report)
                counts = "，".join(f"{member}: {int(member == first_member)}" for member in members)
                self.assertIn(f"（{counts}）", report)
                self.assertIn(f"本周完成统计 - {first_member}: 1", output.getvalue())

    def test_invalid_config_stops_before_network_or_output_creation(self) -> None:
        invalid = (
            None,
            "{invalid-json",
            {"schema_version": 1},
            {"schema_version": 1, "tapd_weekly_report": {"workspace_id": "workspace", "members": []}},
            {"schema_version": 1, "tapd_weekly_report": {"workspace_id": "workspace", "members": ["成员甲", " 成员甲 "]}},
            {"schema_version": 1, "tapd_weekly_report": {"workspace_id": "workspace", "members": ["成员甲", 1]}},
            {"schema_version": 1, "tapd_weekly_report": {"workspace_id": "workspace", "members": ["Alice;Smith"]}},
            {"schema_version": 1, "tapd_weekly_report": {"workspace_id": "", "members": MEMBERS}},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            for index, value in enumerate(invalid):
                config_path = Path(temp_dir) / f"config-{index}.json"
                if value is not None:
                    config_path.write_text(value if isinstance(value, str) else json.dumps(value))
                output_root = Path(temp_dir) / f"reports-{index}"
                argv = [
                    str(SCRIPT), "--version", "2026-0115", "--config", str(config_path),
                    "--output-root", str(output_root),
                ]
                error = io.StringIO()
                with self.subTest(config=value), patch.object(sys, "argv", argv), patch.dict(
                    os.environ, {"TAPD_TOKEN": "test-token"}, clear=True
                ), patch.object(
                    weekly, "fetch_stories", side_effect=AssertionError("invalid config must stop before network")
                ), redirect_stderr(error):
                    self.assertEqual(1, weekly.main())
                self.assertTrue(error.getvalue())
                self.assertFalse(output_root.exists())


if __name__ == "__main__":
    unittest.main()
