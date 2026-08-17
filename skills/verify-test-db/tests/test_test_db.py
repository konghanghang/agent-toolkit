from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TOOLKIT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = TOOLKIT_ROOT / "skills" / "verify-test-db" / "scripts" / "test_db.py"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_test_db_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


test_db = load_module()


def raw_profile(**overrides):
    profile = {
        "environment": "test",
        "engine": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "username": "tester",
        "password": "secret",
        "allowed_databases": ["*"],
        "writable_databases": ["*"],
    }
    profile.update(overrides)
    return profile


def write_profiles(base_dir: Path, **profiles) -> None:
    path = base_dir / "profiles.json"
    path.write_text(json.dumps({"profiles": profiles}), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


class FakeCursor:
    def __init__(self, affected: int):
        self.affected = affected
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql):
        self.sql = sql
        return self.affected


class FakeConnection:
    def __init__(self, affected: int):
        self.cursor_value = FakeCursor(affected)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class ProfilePolicyTest(unittest.TestCase):
    def test_both_database_scopes_support_explicit_wildcard(self):
        profile = test_db.validate_profile("test-main", raw_profile())

        self.assertEqual(("*",), profile["allowed_databases"])
        self.assertEqual(("*",), profile["writable_databases"])
        self.assertEqual("ai", test_db.require_database(profile, "ai"))
        self.assertEqual("ai", test_db.require_database(profile, "ai", write=True))

    def test_empty_scope_denies_instead_of_expanding(self):
        profile = test_db.validate_profile(
            "test-main",
            raw_profile(allowed_databases=[], writable_databases=[]),
        )

        with self.assertRaisesRegex(test_db.TestDbError, "不允许读取"):
            test_db.require_database(profile, "ai")
        with self.assertRaisesRegex(test_db.TestDbError, "不允许写入"):
            test_db.require_database(profile, "ai", write=True)

    def test_wildcard_cannot_mix_with_names(self):
        with self.assertRaisesRegex(test_db.TestDbError, "不能混入"):
            test_db.validate_profile(
                "test-main",
                raw_profile(allowed_databases=["*", "ai"]),
            )

    def test_writable_scope_must_be_inside_readable_scope(self):
        with self.assertRaisesRegex(test_db.TestDbError, "子集"):
            test_db.validate_profile(
                "test-main",
                raw_profile(
                    allowed_databases=["ai"],
                    writable_databases=["ai_admin"],
                ),
            )
        with self.assertRaisesRegex(test_db.TestDbError, "allowed_databases"):
            test_db.validate_profile(
                "test-main",
                raw_profile(
                    allowed_databases=["ai"],
                    writable_databases=["*"],
                ),
            )

    def test_system_databases_are_always_denied(self):
        profile = test_db.validate_profile("test-main", raw_profile())
        for database in test_db.SYSTEM_DATABASES:
            with self.assertRaises(test_db.TestDbError):
                test_db.require_database(profile, database)
            with self.assertRaises(test_db.TestDbError):
                test_db.require_database(profile, database, write=True)

    def test_profile_listing_never_exposes_password(self):
        profile = test_db.validate_profile("test-main", raw_profile())

        public = test_db.public_profile(profile)

        self.assertNotIn("password", public)
        self.assertEqual("file", public["password_source"])
        self.assertTrue(public["configured"])

    def test_generated_template_is_visible_but_cannot_connect_or_plan_writes(self):
        profile = test_db.validate_profile(
            "test-main",
            raw_profile(host="测试库地址", username="测试库账号"),
        )

        self.assertFalse(test_db.public_profile(profile)["configured"])
        with self.assertRaisesRegex(test_db.TestDbError, "安装模板占位符"):
            test_db.require_profile_configured(profile)
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(test_db.TestDbError, "安装模板占位符"):
                test_db.create_write_plan(
                    Path(temp_dir),
                    profile,
                    "ai",
                    "insert into test_fixture(id) values (1)",
                    "delete from test_fixture where id = 1",
                    1,
                    30,
                    "测试",
                )


class RouteDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        write_profiles(
            self.base_dir,
            **{
                "test-main": raw_profile(host="mysql-main.test"),
                "test-task": raw_profile(host="mysql-task.test"),
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def databases_by_profile(self, profile):
        return {
            "test-main": ["ai", "ai_company"],
            "test-task": ["ai_task", "ai_task_file"],
        }[profile["name"]]

    def test_auto_route_discovers_unique_instance_and_reuses_private_cache(self):
        with patch.object(
            test_db, "list_databases", side_effect=self.databases_by_profile
        ) as databases:
            profile, route = test_db.resolve_profile(
                self.base_dir,
                "ai_task",
                targets={"ai_task": ("ai_task",)},
            )

        self.assertEqual("test-task", profile["name"])
        self.assertEqual("discovery", route["source"])
        self.assertEqual(2, databases.call_count)
        cache_path = test_db.route_cache_path(self.base_dir)
        self.assertTrue(cache_path.is_file())
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        cache_entry = next(iter(cache["routes"].values()))
        cache_ttl = test_db.parse_utc(cache_entry["expires_at"]) - test_db.parse_utc(
            cache_entry["discovered_at"]
        )
        self.assertEqual(24 * 60 * 60, cache_ttl.total_seconds())
        if os.name != "nt":
            self.assertEqual(0o600, cache_path.stat().st_mode & 0o777)

        with patch.object(test_db, "list_databases") as databases:
            cached_profile, cached_route = test_db.resolve_profile(
                self.base_dir,
                "ai_task",
                targets={"ai_task": ("ai_task",)},
            )

        self.assertEqual("test-task", cached_profile["name"])
        self.assertEqual("cache", cached_route["source"])
        databases.assert_not_called()

    def test_duplicate_database_is_disambiguated_by_required_table(self):
        with patch.object(
            test_db, "list_databases", return_value=["ai_task"]
        ), patch.object(
            test_db,
            "list_tables",
            side_effect=lambda profile, database: {
                "test-main": ["legacy_task"],
                "test-task": ["ai_task"],
            }[profile["name"]],
        ):
            profile, _ = test_db.resolve_profile(
                self.base_dir,
                "ai_task",
                targets={"ai_task": ("ai_task",)},
            )

        self.assertEqual("test-task", profile["name"])

    def test_cache_write_failure_does_not_block_live_discovery(self):
        with patch.object(
            test_db, "list_databases", side_effect=self.databases_by_profile
        ), patch.object(
            test_db,
            "store_route_candidates",
            side_effect=PermissionError("sandbox denied"),
        ):
            profile, route = test_db.resolve_profile(self.base_dir, "ai_task")

        self.assertEqual("test-task", profile["name"])
        self.assertEqual("discovery-no-cache", route["source"])
        self.assertIn("sandbox denied", route["warning"])

    def test_profile_change_invalidates_cached_route(self):
        with patch.object(
            test_db, "list_databases", side_effect=self.databases_by_profile
        ):
            test_db.resolve_profile(self.base_dir, "ai_task")
        write_profiles(
            self.base_dir,
            **{
                "test-main": raw_profile(host="mysql-main.test"),
                "test-task": raw_profile(host="mysql-task-new.test"),
            },
        )

        with patch.object(
            test_db, "list_databases", side_effect=self.databases_by_profile
        ) as databases:
            profile, route = test_db.resolve_profile(self.base_dir, "ai_task")

        self.assertEqual("test-task", profile["name"])
        self.assertEqual("discovery", route["source"])
        self.assertEqual(2, databases.call_count)

    def test_explicit_profile_bypasses_discovery(self):
        with patch.object(test_db, "list_databases") as databases:
            profile, route = test_db.resolve_profile(
                self.base_dir,
                "ai_task",
                profile_name="test-task",
            )

        self.assertEqual("test-task", profile["name"])
        self.assertEqual("explicit", route["source"])
        databases.assert_not_called()

    def test_duplicate_database_and_table_requires_explicit_profile(self):
        with patch.object(
            test_db, "list_databases", return_value=["ai_task"]
        ), patch.object(test_db, "list_tables", return_value=["ai_task"]):
            with self.assertRaisesRegex(test_db.TestDbError, "匹配多个测试实例"):
                test_db.resolve_profile(
                    self.base_dir,
                    "ai_task",
                    targets={"ai_task": ("ai_task",)},
                )

    def test_failed_candidate_probe_never_silently_selects_another_instance(self):
        def probe(profile):
            if profile["name"] == "test-main":
                raise test_db.TestDbError("连接超时")
            return ["ai_task"]

        with patch.object(test_db, "list_databases", side_effect=probe):
            with self.assertRaisesRegex(test_db.TestDbError, "无法保证唯一实例"):
                test_db.resolve_profile(self.base_dir, "ai_task")

    def test_write_route_only_probes_profiles_allowed_to_write(self):
        write_profiles(
            self.base_dir,
            **{
                "test-main": raw_profile(
                    host="mysql-main.test", writable_databases=[]
                ),
                "test-task": raw_profile(host="mysql-task.test"),
            },
        )
        with patch.object(
            test_db, "list_databases", return_value=["ai_task"]
        ) as databases:
            profile, route = test_db.resolve_profile(
                self.base_dir,
                "ai_task",
                targets={"ai_task": ("ai_task",)},
                write=True,
            )

        self.assertEqual("test-task", profile["name"])
        self.assertTrue(route["write"])
        databases.assert_called_once()

    def test_sql_routing_targets_ignore_cte_alias_and_keep_cross_database(self):
        targets = test_db.sql_routing_targets(
            "with recent as (select id from ai_task) "
            "select r.id from recent r join ai_company.company c on c.id = r.id",
            "ai_task",
        )

        self.assertEqual(
            {"ai_company": ("company",), "ai_task": ("ai_task",)},
            targets,
        )

    def test_database_commands_accept_omitted_profile(self):
        args = test_db.build_parser().parse_args(
            [
                "query-safe",
                "--database",
                "ai_task",
                "--sql",
                "select id from ai_task where id = 1",
            ]
        )

        self.assertIsNone(args.profile)


class SqlSafetyTest(unittest.TestCase):
    def setUp(self):
        self.profile = test_db.validate_profile(
            "test-main",
            raw_profile(allowed_databases=["ai"], writable_databases=["ai"]),
        )

    def test_read_sql_accepts_select_and_checks_qualified_database(self):
        expression = test_db.validate_read_sql(
            "select id, status from ai.ai_task where id = 1",
            self.profile,
        )

        self.assertEqual("select", expression.key)
        with self.assertRaisesRegex(test_db.TestDbError, "other"):
            test_db.validate_read_sql("select * from other.ai_task", self.profile)

    def test_read_sql_rejects_non_select_multi_statement_and_dangerous_functions(self):
        for sql in (
            "show tables",
            "update ai_task set status = 2 where id = 1",
            "select 1; select 2",
            "select sleep(10)",
            "select * from ai_task for update",
            "select * from ai_task for share",
        ):
            with self.subTest(sql=sql):
                with self.assertRaises(test_db.TestDbError):
                    test_db.validate_read_sql(sql, self.profile)

    def test_write_sql_accepts_single_table_dml_and_requires_where(self):
        for sql in (
            "insert into ai.test_fixture(id) values (1)",
            "update ai.test_fixture set status = 2 where id = 1",
            "delete from ai.test_fixture where id = 1",
        ):
            with self.subTest(sql=sql):
                test_db.validate_write_sql(sql, self.profile, "ai")

        for sql in (
            "update ai.test_fixture set status = 2",
            "delete from ai.test_fixture",
            "drop table ai.test_fixture",
            "insert into other.test_fixture(id) values (1)",
            "insert ignore into ai.test_fixture(id) values (1)",
            "insert into ai.test_fixture(id) values (1) on duplicate key update id = values(id)",
            "update ai.test_fixture t join ai.other o on t.id = o.id set t.status = 2 where t.id = 1",
            "delete t from ai.test_fixture t join ai.other o on t.id = o.id where t.id = 1",
        ):
            with self.subTest(sql=sql):
                with self.assertRaises(test_db.TestDbError):
                    test_db.validate_write_sql(sql, self.profile, "ai")


class SnapshotAndVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        self.profile = test_db.validate_profile("test-main", raw_profile())

    def tearDown(self):
        self.temp.cleanup()

    def test_redaction_is_stable_and_diff_is_keyed(self):
        before = test_db.redact_rows(
            self.base_dir,
            [{"id": 1, "phone": "13800000000", "status": 1}],
        )
        same_sensitive_value = test_db.redact_rows(
            self.base_dir,
            [{"id": 1, "phone": "13800000000", "status": 2}],
        )

        self.assertEqual(before[0]["phone"], same_sensitive_value[0]["phone"])
        self.assertNotIn("13800000000", before[0]["phone"])
        diff = test_db.diff_rows(before, same_sensitive_value, ["id"])
        self.assertEqual(1, diff["summary"]["changed"])
        self.assertEqual({"status"}, set(diff["changed"][0]["changes"]))

    def test_snapshot_and_compare_reuse_exact_query(self):
        first = {
            "columns": ["id", "status"],
            "rows": [{"id": 1, "status": 1}],
            "truncated": False,
            "limit": 10,
        }
        second = {
            "columns": ["id", "status"],
            "rows": [{"id": 1, "status": 2}],
            "truncated": False,
            "limit": 10,
        }
        with patch.object(test_db, "execute_query", return_value=first):
            snapshot = test_db.create_snapshot(
                self.base_dir,
                self.profile,
                "ai",
                "select id, status from ai_task where id = 1",
                10,
                ["id"],
                "before",
            )
        write_profiles(self.base_dir, **{"test-main": raw_profile()})

        with patch.object(test_db, "execute_query", return_value=second) as query:
            comparison = test_db.compare_snapshot(self.base_dir, snapshot)

        self.assertEqual(1, comparison["summary"]["changed"])
        query.assert_called_once_with(
            unittest.mock.ANY,
            "ai",
            "select id, status from ai_task where id = 1",
            10,
        )

    def test_verify_returns_pass_without_exposing_mismatch_values(self):
        result = {
            "columns": ["id", "status"],
            "rows": [{"id": 1, "status": 2}],
            "truncated": False,
            "limit": 10,
        }
        with patch.object(test_db, "execute_query", return_value=result):
            payload = test_db.verify_until(
                self.base_dir,
                self.profile,
                "ai",
                "select id, status from ai_task where id = 1",
                10,
                [("status", 2)],
                [],
                1,
                0,
                1,
                None,
            )

        self.assertEqual("PASS", payload["status"])
        self.assertEqual([], payload["failures"])

    def test_expectations_compare_json_serializable_values(self):
        created_at = test_db.utc_now()
        passed, failures = test_db.expectations_pass(
            [{"created_at": created_at, "amount": test_db.decimal.Decimal("1.20")}],
            None,
            [
                ("created_at", test_db.jsonable(created_at)),
                ("amount", "1.20"),
            ],
            [],
            1,
        )

        self.assertTrue(passed)
        self.assertEqual([], failures)


class WritePlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        self.raw = raw_profile()
        write_profiles(self.base_dir, **{"test-main": self.raw})
        self.profile = test_db.get_profile(self.base_dir, "test-main")

    def tearDown(self):
        self.temp.cleanup()

    def create_plan(self, max_affected=1):
        return test_db.create_write_plan(
            self.base_dir,
            self.profile,
            "ai",
            "insert into test_fixture(id, status) values (9001, 1)",
            "delete from test_fixture where id = 9001",
            max_affected,
            30,
            "接口入库验证",
        )

    def test_plan_requires_exact_confirmation_and_supports_rollback(self):
        plan = self.create_plan()
        with self.assertRaisesRegex(test_db.TestDbError, "confirmation_token"):
            test_db.execute_write_plan(self.base_dir, plan["id"], "wrong")

        apply_connection = FakeConnection(1)
        rollback_connection = FakeConnection(1)
        with patch.object(
            test_db,
            "connect_mysql",
            side_effect=[apply_connection, rollback_connection],
        ):
            applied = test_db.execute_write_plan(
                self.base_dir,
                plan["id"],
                plan["confirmation_token"],
            )
            rolled_back = test_db.execute_write_plan(
                self.base_dir,
                plan["id"],
                plan["confirmation_token"],
                rollback=True,
            )

        self.assertTrue(apply_connection.committed)
        self.assertTrue(rollback_connection.committed)
        self.assertEqual("applied", applied["plan_status"])
        self.assertEqual("rolled_back", rolled_back["plan_status"])

    def test_excess_affected_rows_roll_back_and_close_plan(self):
        plan = self.create_plan(max_affected=1)
        connection = FakeConnection(2)

        with patch.object(test_db, "connect_mysql", return_value=connection):
            with self.assertRaisesRegex(test_db.TestDbError, "超过"):
                test_db.execute_write_plan(
                    self.base_dir,
                    plan["id"],
                    plan["confirmation_token"],
                )

        self.assertTrue(connection.rolled_back)
        stored = test_db.load_artifact(self.base_dir, "plans", plan["id"])
        self.assertEqual("failed", stored["status"])

    def test_too_few_affected_rows_also_roll_back(self):
        plan = self.create_plan(max_affected=1)
        connection = FakeConnection(0)

        with patch.object(test_db, "connect_mysql", return_value=connection):
            with self.assertRaisesRegex(test_db.TestDbError, "低于"):
                test_db.execute_write_plan(
                    self.base_dir,
                    plan["id"],
                    plan["confirmation_token"],
                )

        self.assertTrue(connection.rolled_back)

    def test_plan_content_digest_detects_tampering(self):
        plan = self.create_plan()
        path = test_db.artifact_path(self.base_dir, "plans", plan["id"])
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["sql"] = "delete from test_fixture where id = 9001"
        test_db.atomic_write_json(path, stored)

        with self.assertRaisesRegex(test_db.TestDbError, "已被修改"):
            test_db.execute_write_plan(
                self.base_dir,
                plan["id"],
                plan["confirmation_token"],
            )


if __name__ == "__main__":
    unittest.main()
