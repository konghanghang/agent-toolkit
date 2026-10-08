from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
TOOLKIT_ROOT = SKILL_DIR.parents[1]
COMMON_DIR = TOOLKIT_ROOT / "common"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(COMMON_DIR))
try:
    query = load_module(
        "nacos_query_test",
        SKILL_DIR / "scripts" / "nacos_query.py",
    )
finally:
    sys.path.pop(0)


RUNTIME = {
    "agent_toolkit_root": str(TOOLKIT_ROOT),
    "nacos_cn_base_url": "http://nacos-cn.example",
    "nacos_os_base_url": "http://nacos-os.example",
}


class NacosQueryTest(unittest.TestCase):
    def test_service_names_normalize_to_exact_data_ids(self) -> None:
        self.assertEqual("call-task.yaml", query.service_to_data_id("call-task"))
        self.assertEqual("call-task.yaml", query.service_to_data_id("call_task"))
        self.assertEqual("call-task.yaml", query.service_to_data_id("Call-Task"))
        self.assertEqual("call-task.yaml", query.service_to_data_id("call-task.yml"))
        self.assertEqual("ai-quartz", query.service_to_data_id("ai-quartz.yaml"))
        self.assertEqual("ai-common.properties", query.service_to_data_id("ai-common.properties"))
        self.assertEqual(
            "common-lang.zh_CN.properties",
            query.service_to_data_id("common-lang.zh.properties"),
        )

    def test_explicit_data_id_stays_exact_except_lang_locale(self) -> None:
        self.assertEqual("call-task.yml", query.exact_data_id("call-task.yml"))
        self.assertEqual("ai-quartz.yaml", query.exact_data_id("ai-quartz.yaml"))
        self.assertEqual(
            "common-lang.zh_CN.properties",
            query.exact_data_id("common-lang.zh.properties"),
        )
        self.assertEqual(
            "DEFAULT_GROUP",
            query.infer_group("changelog.properties", None),
        )
        self.assertEqual(
            "LANGUAGE_GROUP",
            query.infer_group("common-lang.en.properties", None),
        )

    def test_redaction_covers_yaml_json_and_url_secrets(self) -> None:
        text = "\n".join(
            [
                "url: jdbc:mysql://10.0.0.8:3306/ai?user=root&password=s3cret",
                "username: app",
                "password: plain-pass",
                "pwd: also-secret",
                '"password": "hide-me"',
                "private-key: |",
                "  -----BEGIN PRIVATE KEY-----",
                "  LEAKEDMATERIAL",
                "next: visible",
            ]
        )
        redacted = query.redact_text(text)
        self.assertNotIn("s3cret", redacted)
        self.assertNotIn("plain-pass", redacted)
        self.assertNotIn("also-secret", redacted)
        self.assertNotIn("LEAKEDMATERIAL", redacted)
        self.assertNotIn("hide-me", redacted)
        self.assertIn("username: app", redacted)
        self.assertIn("next: visible", redacted)

        array = query.redact_text('[{"password":"hide-me","host":"db"}]')
        self.assertNotIn("hide-me", array)
        self.assertIn("db", array)

    def test_get_redacts_content_and_returns_success(self) -> None:
        def fake_get(url: str):
            self.assertEqual("http://nacos-cn.example/config/call-task.yaml", url)
            return 200, "text/yaml", "password: secret\nhost: db\n"

        stdout = io.StringIO()
        with patch.object(query, "http_get", side_effect=fake_get), patch.object(
            query, "load_runtime_or_exit", return_value=RUNTIME
        ), redirect_stdout(stdout):
            code = query.main(["get", "--scope", "cn", "--service", "call-task"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertTrue(payload["ok"])
        self.assertEqual("call-task.yaml", payload["data_id"])
        self.assertEqual("DEFAULT_GROUP", payload["group"])
        self.assertNotIn("secret", payload["content"])
        self.assertIn("host: db", payload["content"])

    def test_missing_config_returns_json_and_nonzero_status(self) -> None:
        with patch.object(
            query,
            "http_get",
            return_value=(404, "text/plain", "Config not found"),
        ), patch.object(query, "load_runtime_or_exit", return_value=RUNTIME):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = query.main(["get", "--scope", "cn", "--service", "missing"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(1, code)
        self.assertFalse(payload["ok"])
        self.assertEqual(404, payload["http_status"])
        self.assertIn("精确 dataId", payload["hint"])

    def test_both_keeps_the_successful_scope_when_the_other_fails(self) -> None:
        def fake_get(url: str):
            if url.startswith("http://nacos-cn.example/"):
                return 200, "text/yaml", "host: cn\n"
            raise query.QueryError("超时 " + url)

        stdout = io.StringIO()
        with patch.object(query, "http_get", side_effect=fake_get), patch.object(
            query, "load_runtime_or_exit", return_value=RUNTIME
        ), redirect_stdout(stdout):
            code = query.main(["get", "--scope", "both", "--service", "gateway"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(1, code)
        self.assertEqual("both", payload["scope"])
        self.assertTrue(payload["results"][0]["ok"])
        self.assertFalse(payload["results"][1]["ok"])
        self.assertIn("host: cn", payload["results"][0]["content"])

    def test_lang_uses_language_group_and_locale_alias(self) -> None:
        def fake_get(url: str):
            self.assertEqual(
                "http://nacos-cn.example/config/common-lang.zh_CN.properties?group=LANGUAGE_GROUP",
                url,
            )
            return 200, "text/plain", "hangup=挂机\n"

        stdout = io.StringIO()
        with patch.object(query, "http_get", side_effect=fake_get), patch.object(
            query, "load_runtime_or_exit", return_value=RUNTIME
        ), redirect_stdout(stdout):
            code = query.main(["lang", "--scope", "cn", "--locale", "zh"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("common-lang.zh_CN.properties", payload["data_id"])
        self.assertEqual("LANGUAGE_GROUP", payload["group"])

    def test_missing_runtime_url_exits_before_request(self) -> None:
        stderr = io.StringIO()
        with patch.object(
            query,
            "load_runtime_or_exit",
            return_value={"agent_toolkit_root": str(TOOLKIT_ROOT)},
        ), patch.object(query, "http_get") as http_get, redirect_stderr(stderr):
            code = query.main(["get", "--scope", "cn", "--service", "call-task"])

        self.assertEqual(2, code)
        self.assertIn("nacos_cn_base_url", stderr.getvalue())
        http_get.assert_not_called()

    def test_get_requires_a_service_or_data_id(self) -> None:
        stderr = io.StringIO()
        with patch.object(query, "load_runtime_or_exit", return_value=RUNTIME), redirect_stderr(
            stderr
        ):
            code = query.main(["get"])

        self.assertEqual(2, code)
        self.assertIn("--service", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
