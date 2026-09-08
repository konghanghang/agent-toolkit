from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


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
    pipeline = load_module(
        "gitlab_pipeline_test",
        SKILL_DIR / "scripts" / "gitlab_pipeline.py",
    )
finally:
    sys.path.pop(0)


CI_TEXT = """
variables:
  SUB_MODULE:
    value: "all"
    options: ["all", "core"]
  ENVIRONMENT_NAME:
    value: "dev"
    options: ["dev", "test", "grayscale", "prod"]

build:
  script: echo ok
"""


def lint_result(valid: bool = True) -> dict:
    return {
        "available": True,
        "valid": valid,
        "errors": [] if valid else ["invalid config"],
        "warnings": [],
        "jobs_count": 1 if valid else 0,
        "merged_yaml": CI_TEXT,
    }


def inspected_payload(*, commit_sha: str = "abc123") -> dict:
    return {
        "project": "group/project",
        "project_id": 42,
        "web_url": "https://gitlab.example.test/group/project",
        "ref": "test",
        "commit": {
            "id": commit_sha,
            "short_id": commit_sha[:8],
            "title": "test commit",
            "web_url": f"https://gitlab.example.test/group/project/-/commit/{commit_sha}",
        },
        "ci": {
            "config_sha256": "ci-config-digest",
            "variables": [
                {"key": "ENVIRONMENT_NAME", "default": "dev", "options": ["dev", "test"]},
                {"key": "SUB_MODULE", "default": "all", "options": ["all"]},
            ],
            "lint": lint_result(),
        },
    }


def pipeline_payload(status: str, pipeline_id: int = 88) -> dict:
    return {
        "id": pipeline_id,
        "iid": 8,
        "status": status,
        "source": "api",
        "ref": "test",
        "sha": "abc123",
        "duration": 21,
        "web_url": f"https://gitlab.example.test/group/project/-/pipelines/{pipeline_id}",
        "user": {"id": 1, "username": "tester", "name": "Tester"},
    }


def job_payload(
    status: str,
    *,
    job_id: int,
    name: str,
    allow_failure: bool = False,
) -> dict:
    return {
        "id": job_id,
        "name": name,
        "stage": "deploy",
        "status": status,
        "allow_failure": allow_failure,
        "failure_reason": "script_failure" if status == "failed" else None,
        "duration": 12,
        "web_url": f"https://gitlab.example.test/group/project/-/jobs/{job_id}",
    }


class FakeResponse:
    def __init__(self, payload, status: int = 200):
        if isinstance(payload, bytes):
            self.body = payload
        elif isinstance(payload, str):
            self.body = payload.encode("utf-8")
        else:
            self.body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = {"Content-Length": str(len(self.body))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size: int) -> bytes:
        return self.body[:size]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class GitLabPipelineTest(unittest.TestCase):
    def test_repository_files_do_not_define_a_default_gitlab_url(self) -> None:
        self.assertFalse(hasattr(pipeline, "DEFAULT_BASE_URL"))

    def test_parse_git_remote_supports_scp_http_and_ssh_port(self) -> None:
        self.assertEqual(
            {"host": "gitlab.example.test", "project": "group/project"},
            pipeline.parse_git_remote("git@gitlab.example.test:group/project.git"),
        )
        self.assertEqual(
            {"host": "gitlab.example.test", "project": "group/project"},
            pipeline.parse_git_remote("https://gitlab.example.test/group/project.git"),
        )
        self.assertEqual(
            {"host": "gitlab.example.test", "project": "group/project"},
            pipeline.parse_git_remote("ssh://git@gitlab.example.test:2222/group/project.git"),
        )

    def test_remote_host_must_match_configured_gitlab(self) -> None:
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        pipeline.validate_remote_host("gitlab.example.test", credentials)
        with self.assertRaisesRegex(pipeline.PipelineError, "host 与 GitLab 配置不一致"):
            pipeline.validate_remote_host("github.com", credentials)

    def test_fuzzy_project_search_returns_candidates_without_auto_selecting_ambiguous_name(self) -> None:
        payload = [
            {
                "id": 1,
                "name": "company",
                "path": "company",
                "path_with_namespace": "backend/company",
                "web_url": "https://gitlab.example.test/backend/company",
            },
            {
                "id": 2,
                "name": "company",
                "path": "company",
                "path_with_namespace": "platform/company",
                "web_url": "https://gitlab.example.test/platform/company",
            },
        ]
        with patch.object(pipeline, "api_request", return_value=payload) as request:
            result = pipeline.search_projects(
                {"base_url": "https://gitlab.example.test", "private_token": "secret"},
                "company",
            )
        self.assertEqual(2, len(result["candidates"]))
        self.assertTrue(result["selection_required"])
        self.assertIn(("membership", "true"), request.call_args.kwargs["query"])

    def test_ref_is_resolved_to_authoritative_commit(self) -> None:
        with patch.object(
            pipeline,
            "api_request",
            return_value={
                "id": "full-commit-sha",
                "short_id": "full-com",
                "title": "release",
                "web_url": "https://gitlab.example.test/group/project/-/commit/full-commit-sha",
            },
        ) as request:
            commit = pipeline.fetch_ref_commit(
                {"base_url": "https://gitlab.example.test", "private_token": "secret"},
                "group/project",
                "release/2026-08",
            )
        self.assertEqual("full-commit-sha", commit["id"])
        self.assertIn("release%2F2026-08", request.call_args.args[2])

    def test_trigger_plan_binds_project_commit_ci_and_parameters(self) -> None:
        inspected = inspected_payload()
        variables = pipeline.build_pipeline_variables(
            environment="test",
            module="all",
            extra=["FEATURE_FLAG=enabled"],
        )
        plan = pipeline.build_trigger_plan(
            inspected,
            environment="test",
            module="all",
            variables=variables,
        )
        same_plan = pipeline.build_trigger_plan(
            inspected,
            environment="test",
            module="all",
            variables=list(reversed(variables)),
        )
        moved_plan = pipeline.build_trigger_plan(
            inspected_payload(commit_sha="changed-sha"),
            environment="test",
            module="all",
            variables=variables,
        )
        self.assertEqual(plan["plan_id"], same_plan["plan_id"])
        self.assertNotEqual(plan["plan_id"], moved_plan["plan_id"])
        self.assertEqual(42, plan["project_id"])
        self.assertEqual("ci-config-digest", plan["ci_config_sha256"])

    def test_inspect_returns_confirmation_plan_for_exact_inputs(self) -> None:
        args = pipeline.build_parser().parse_args(
            [
                "inspect",
                "--project",
                "group/project",
                "--ref",
                "test",
                "--env",
                "test",
                "--module",
                "all",
            ]
        )
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        with patch.object(pipeline, "load_credentials", return_value=credentials), patch.object(
            pipeline,
            "_resolved_project",
            return_value=("group/project", None),
        ), patch.object(pipeline, "inspect_project", return_value=inspected_payload()):
            result = pipeline.cmd_inspect(args)
        self.assertTrue(result["trigger_plan"]["plan_id"].startswith("sha256:"))
        self.assertEqual("abc123", result["trigger_plan"]["commit_sha"])

    def test_inspect_rejects_plan_parameters_without_environment_choice(self) -> None:
        args = pipeline.build_parser().parse_args(
            [
                "inspect",
                "--project",
                "group/project",
                "--ref",
                "test",
                "--module",
                "all",
            ]
        )
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        with patch.object(pipeline, "load_credentials", return_value=credentials), patch.object(
            pipeline,
            "_resolved_project",
            return_value=("group/project", None),
        ), patch.object(pipeline, "inspect_project", return_value=inspected_payload()):
            with self.assertRaisesRegex(pipeline.PipelineError, "--env 或 --no-environment"):
                pipeline.cmd_inspect(args)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not Windows ACLs")
    def test_credentials_come_only_from_private_runtime_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            gitlab_dir = Path(temp_dir) / "gitlab"
            gitlab_dir.mkdir()
            credentials_path = gitlab_dir / "gitlab-credentials.json"
            credentials_path.write_text(
                json.dumps(
                    {
                        "base_url": "https://gitlab.example.test/",
                        "private_token": "glpat-secret-token",
                    }
                ),
                encoding="utf-8",
            )
            credentials_path.chmod(0o644)
            with patch.object(pipeline, "load_runtime", return_value={"gitlab_dir": str(gitlab_dir)}):
                loaded = pipeline.load_credentials()
            self.assertEqual("https://gitlab.example.test", loaded["base_url"])
            self.assertEqual("glpat-secret-token", loaded["private_token"])
            self.assertEqual(0o600, stat.S_IMODE(credentials_path.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(gitlab_dir.stat().st_mode))

    def test_missing_credentials_reports_runtime_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(pipeline, "load_runtime", return_value={"gitlab_dir": temp_dir}):
                with self.assertRaisesRegex(pipeline.PipelineError, "gitlab-credentials.json"):
                    pipeline.load_credentials()

    def test_build_pipeline_variables_rejects_reserved_and_duplicate_keys(self) -> None:
        variables = pipeline.build_pipeline_variables(
            environment="test",
            module="all",
            extra=["FEATURE_FLAG=enabled"],
        )
        self.assertEqual(
            [
                {"key": "ENVIRONMENT_NAME", "value": "test"},
                {"key": "SUB_MODULE", "value": "all"},
                {"key": "FEATURE_FLAG", "value": "enabled"},
            ],
            variables,
        )
        with self.assertRaisesRegex(pipeline.PipelineError, "预定义变量"):
            pipeline.build_pipeline_variables(
                environment="test",
                module=None,
                extra=["CI_COMMIT_BRANCH=main"],
            )
        with self.assertRaisesRegex(pipeline.PipelineError, "重复"):
            pipeline.build_pipeline_variables(
                environment=None,
                module=None,
                extra=["FLAG=one", "FLAG=two"],
            )

    def test_trigger_parser_requires_explicit_environment_choice(self) -> None:
        parser = pipeline.build_parser()
        with patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "trigger",
                    "--project",
                    "group/project",
                    "--ref",
                    "test",
                    "--plan-id",
                    "sha256:plan",
                    "--confirm",
                ]
            )
        with patch.object(sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "trigger",
                    "--project",
                    "group/project",
                    "--ref",
                    "test",
                    "--no-environment",
                    "--confirm",
                ]
            )
        args = parser.parse_args(
            [
                "trigger",
                "--project",
                "group/project",
                "--ref",
                "test",
                "--no-environment",
                "--plan-id",
                "sha256:plan",
                "--confirm",
            ]
        )
        self.assertTrue(args.no_environment)

    def test_trigger_plan_requires_confirmation_prod_flag_and_valid_lint(self) -> None:
        ci = pipeline.load_ci_yaml(CI_TEXT)
        specs = [
            spec
            for key in pipeline.VARIABLE_KEYS
            if (spec := pipeline.extract_variable_spec(ci, key))
        ]
        with self.assertRaisesRegex(pipeline.PipelineError, "--confirm"):
            pipeline.validate_trigger_plan(
                environment="test",
                module="all",
                ci_variables=specs,
                lint=lint_result(),
                allow_prod=False,
                confirm=False,
            )
        with self.assertRaisesRegex(pipeline.PipelineError, "--allow-prod"):
            pipeline.validate_trigger_plan(
                environment="prod",
                module="all",
                ci_variables=specs,
                lint=lint_result(),
                allow_prod=False,
                confirm=True,
            )
        with self.assertRaisesRegex(pipeline.PipelineError, "CI 配置校验失败"):
            pipeline.validate_trigger_plan(
                environment="test",
                module="all",
                ci_variables=specs,
                lint=lint_result(False),
                allow_prod=False,
                confirm=True,
            )

    def test_fetch_ci_text_always_requests_exact_remote_ref(self) -> None:
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        with patch.object(pipeline, "api_request", return_value=CI_TEXT) as request:
            text = pipeline.fetch_ci_text(credentials, "group/project", "release")
        self.assertEqual(CI_TEXT, text)
        self.assertEqual([("ref", "release")], request.call_args.kwargs["query"])
        self.assertTrue(request.call_args.kwargs["raw"])

    def test_lint_uses_server_side_content_ref_and_falls_back_for_old_gitlab(self) -> None:
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        unsupported = pipeline.PipelineRequestError(
            "unsupported",
            status_code=400,
        )
        response = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "jobs": [{"name": "build"}],
            "merged_yaml": CI_TEXT,
        }
        with patch.object(
            pipeline,
            "api_request",
            side_effect=[unsupported, response],
        ) as request:
            result = pipeline.lint_ci_config(credentials, "group/project", "release")
        self.assertTrue(result["valid"])
        self.assertEqual(1, result["jobs_count"])
        self.assertEqual([("sha", "release"), ("include_jobs", "true")], request.call_args.kwargs["query"])

    def test_lint_falls_back_when_old_gitlab_returns_missing_content_as_result(self) -> None:
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        missing_content = {
            "valid": False,
            "errors": ["Please provide content of .gitlab-ci.yml"],
            "warnings": [],
        }
        response = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "jobs": [{"name": "build"}],
            "merged_yaml": CI_TEXT,
        }
        with patch.object(
            pipeline,
            "api_request",
            side_effect=[missing_content, response],
        ) as request:
            result = pipeline.lint_ci_config(credentials, "group/project", "release")
        self.assertTrue(result["available"])
        self.assertTrue(result["valid"])
        self.assertEqual(2, request.call_count)
        self.assertEqual([("sha", "release"), ("include_jobs", "true")], request.call_args.kwargs["query"])

    def test_lint_marks_old_gitlab_missing_content_response_unavailable(self) -> None:
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        missing_content = {
            "valid": False,
            "errors": ["Please provide content of .gitlab-ci.yml"],
            "warnings": [],
        }
        with patch.object(
            pipeline,
            "api_request",
            side_effect=[missing_content, missing_content],
        ) as request:
            result = pipeline.lint_ci_config(credentials, "group/project", "release")
        self.assertFalse(result["available"])
        self.assertIsNone(result["valid"])
        self.assertEqual(2, request.call_count)
        self.assertEqual(
            ["Please provide content of .gitlab-ci.yml"],
            result["warnings"],
        )

    def test_lint_preserves_real_validation_error(self) -> None:
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        invalid_config = {
            "valid": False,
            "errors": ["jobs:build config should contain a script"],
            "warnings": [],
        }
        with patch.object(pipeline, "api_request", return_value=invalid_config) as request:
            result = pipeline.lint_ci_config(credentials, "group/project", "release")
        self.assertTrue(result["available"])
        self.assertFalse(result["valid"])
        self.assertEqual(1, request.call_count)

    def test_inspect_project_returns_canonical_project_commit_and_ci_digest(self) -> None:
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        project_info = {
            "id": 42,
            "path_with_namespace": "group/project",
            "web_url": "https://gitlab.example.test/group/project",
            "default_branch": "main",
        }
        with patch.object(pipeline, "api_request", return_value=project_info), patch.object(
            pipeline,
            "lint_ci_config",
            return_value=lint_result(),
        ), patch.object(
            pipeline,
            "fetch_ref_commit",
            return_value={"id": "abc123", "short_id": "abc123"},
        ):
            result = pipeline.inspect_project(credentials, "group/project", "test")
        self.assertEqual(42, result["project_id"])
        self.assertEqual("group/project", result["project"])
        self.assertEqual("abc123", result["commit"]["id"])
        self.assertEqual(
            hashlib.sha256(CI_TEXT.encode("utf-8")).hexdigest(),
            result["ci"]["config_sha256"],
        )

    def test_trigger_posts_then_monitors_by_default(self) -> None:
        inspected = inspected_payload()
        trigger_plan = pipeline.build_trigger_plan(
            inspected,
            environment="test",
            module="all",
            variables=pipeline.build_pipeline_variables(
                environment="test",
                module="all",
            ),
        )
        args = pipeline.build_parser().parse_args(
            [
                "trigger",
                "--project",
                "group/project",
                "--ref",
                "test",
                "--env",
                "test",
                "--module",
                "all",
                "--plan-id",
                trigger_plan["plan_id"],
                "--confirm",
            ]
        )
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        final = pipeline.build_pipeline_result(
            pipeline.summarize_pipeline(pipeline_payload("success")),
            [pipeline.summarize_job(job_payload("success", job_id=1, name="deploy"))],
        )
        with patch.object(pipeline, "load_credentials", return_value=credentials), patch.object(
            pipeline,
            "_resolved_project",
            return_value=("group/project", None),
        ), patch.object(pipeline, "inspect_project", return_value=inspected), patch.object(
            pipeline,
            "api_request",
            return_value=pipeline_payload("created"),
        ) as request, patch.object(
            pipeline,
            "monitor_pipeline",
            return_value=final,
        ) as monitor:
            result = pipeline.cmd_trigger(args)
        self.assertTrue(result["monitoring"])
        self.assertEqual("success", result["result"]["outcome"])
        self.assertEqual("POST", request.call_args.args[1])
        self.assertEqual("test", request.call_args.kwargs["payload"]["ref"])
        self.assertEqual(trigger_plan["plan_id"], result["triggered"]["trigger_plan"]["plan_id"])
        monitor.assert_called_once()

    def test_trigger_rejects_stale_plan_before_post(self) -> None:
        args = pipeline.build_parser().parse_args(
            [
                "trigger",
                "--project",
                "group/project",
                "--ref",
                "test",
                "--env",
                "test",
                "--module",
                "all",
                "--plan-id",
                "sha256:stale",
                "--confirm",
            ]
        )
        credentials = {
            "base_url": "https://gitlab.example.test",
            "private_token": "secret",
        }
        with patch.object(pipeline, "load_credentials", return_value=credentials), patch.object(
            pipeline,
            "_resolved_project",
            return_value=("group/project", None),
        ), patch.object(
            pipeline,
            "inspect_project",
            return_value=inspected_payload(commit_sha="new-sha"),
        ), patch.object(pipeline, "api_request") as request:
            with self.assertRaisesRegex(pipeline.PipelineError, "计划已变化"):
                pipeline.cmd_trigger(args)
        request.assert_not_called()

    def test_fixed_commit_checks_before_and_after_creation(self) -> None:
        inspected = inspected_payload()
        plan = pipeline.build_trigger_plan(
            inspected, environment="test", module="all",
            variables=pipeline.build_pipeline_variables(environment="test", module="all"),
        )
        for expected, actual in (("different", "abc123"), ("abc123", "new-sha"), ("abc123", None), ("abc123", "abc123")):
            with self.subTest(expected=expected, actual=actual):
                args = pipeline.build_parser().parse_args([
                    "trigger", "--project", "group/project", "--ref", "test",
                    "--env", "test", "--module", "all", "--plan-id", plan["plan_id"],
                    "--confirm", "--expected-sha", expected,
                ])
                created = {**pipeline_payload("created"), "sha": actual}
                with patch.object(pipeline, "load_credentials", return_value={}), patch.object(
                    pipeline, "_resolved_project", return_value=("group/project", None)
                ), patch.object(pipeline, "inspect_project", return_value=inspected), patch.object(
                    pipeline, "api_request", return_value=created
                ) as request, patch.object(pipeline, "monitor_pipeline", return_value={"outcome": "success"}) as monitor:
                    if expected != "abc123":
                        with self.assertRaisesRegex(pipeline.PipelineError, "尚未创建"):
                            pipeline.cmd_trigger(args)
                        request.assert_not_called()
                        monitor.assert_not_called()
                        continue
                    result = pipeline.cmd_trigger(args)
                    request.assert_called_once()
                    self.assertEqual(created["id"], result["triggered"]["pipeline"]["id"])
                    if actual != expected:
                        self.assertEqual("commit_mismatch", result["result"]["outcome"])
                        self.assertEqual(2, pipeline.result_exit_code(result))
                        self.assertFalse(result["monitoring"])
                        monitor.assert_not_called()
                    else:
                        self.assertEqual(0, pipeline.result_exit_code(result))
                        monitor.assert_called_once()

    def test_result_aggregates_failed_allowed_and_manual_jobs(self) -> None:
        jobs = [
            pipeline.summarize_job(job_payload("success", job_id=1, name="build")),
            pipeline.summarize_job(job_payload("failed", job_id=2, name="deploy")),
            pipeline.summarize_job(
                job_payload("failed", job_id=3, name="optional", allow_failure=True)
            ),
            pipeline.summarize_job(job_payload("manual", job_id=4, name="approve")),
        ]
        result = pipeline.build_pipeline_result(
            pipeline.summarize_pipeline(pipeline_payload("failed")),
            jobs,
        )
        self.assertEqual("failed", result["outcome"])
        self.assertEqual(4, result["job_summary"]["total"])
        self.assertEqual("deploy", result["failed_jobs"][0]["name"])
        self.assertEqual("optional", result["allowed_failures"][0]["name"])
        self.assertEqual("approve", result["manual_jobs"][0]["name"])

    def test_build_stage_can_complete_while_deploy_is_running_or_failed(self) -> None:
        for deploy_status in ("running", "failed"):
            with self.subTest(deploy_status=deploy_status):
                jobs = [
                    {**job_payload("success", job_id=1, name="publish-api"), "stage": "build_artifacts"},
                    job_payload(deploy_status, job_id=2, name="deploy"),
                ]
                result = pipeline.build_pipeline_result(pipeline_payload(deploy_status), jobs)
                self.assertTrue(result["stage_summary"]["build_artifacts"]["all_success"])
                self.assertFalse(result["stage_summary"]["deploy"]["all_success"])
                self.assertEqual("in_progress" if deploy_status == "running" else "failed", result["outcome"])

    def test_build_stage_requires_every_job_to_succeed(self) -> None:
        for status in ("running", "pending", "manual", "failed", "canceled", "skipped", "unknown"):
            with self.subTest(status=status):
                jobs = [
                    {**job_payload("success", job_id=1, name="publish-api"), "stage": "build_artifacts"},
                    {**job_payload(status, job_id=2, name="publish-client", allow_failure=True), "stage": "build_artifacts"},
                ]
                result = pipeline.build_pipeline_result(pipeline_payload("running"), jobs)
                stage = result["stage_summary"]["build_artifacts"]
                self.assertEqual(2, stage["total"])
                self.assertEqual({"success": 1, status: 1}, stage["counts"])
                self.assertFalse(stage["all_success"])

    def test_all_build_jobs_success_does_not_wait_for_other_stages(self) -> None:
        jobs = [
            {**job_payload("success", job_id=1, name="publish-api"), "stage": "build_artifacts"},
            {**job_payload("success", job_id=2, name="publish-client"), "stage": "build_artifacts"},
            job_payload("manual", job_id=3, name="deploy"),
        ]
        result = pipeline.build_pipeline_result(pipeline_payload("manual"), jobs)
        self.assertEqual("requires_action", result["outcome"])
        self.assertTrue(result["stage_summary"]["build_artifacts"]["all_success"])
        self.assertEqual({"success": 2}, result["stage_summary"]["build_artifacts"]["counts"])

    def test_missing_or_truncated_stage_cannot_release_dependents(self) -> None:
        empty = pipeline.build_pipeline_result(pipeline_payload("success"), [])
        self.assertNotIn("build_artifacts", empty["stage_summary"])
        jobs = [{**job_payload("success", job_id=1, name="publish-api"), "stage": "build_artifacts"}]
        truncated = pipeline.build_pipeline_result(pipeline_payload("running"), jobs, jobs_truncated=True)
        self.assertTrue(truncated["job_summary"]["truncated"])
        self.assertFalse(truncated["stage_summary"]["build_artifacts"]["all_success"])

    def test_optional_manual_job_does_not_stop_running_pipeline(self) -> None:
        jobs = [
            pipeline.summarize_job(job_payload("running", job_id=1, name="deploy")),
            pipeline.summarize_job(job_payload("manual", job_id=2, name="optional-approval")),
        ]
        result = pipeline.build_pipeline_result(
            pipeline.summarize_pipeline(pipeline_payload("running")),
            jobs,
        )
        self.assertEqual("in_progress", result["outcome"])

    def test_jobs_are_paginated_with_a_fixed_upper_bound(self) -> None:
        first_page = [job_payload("success", job_id=index, name=f"job-{index}") for index in range(100)]
        second_page = [job_payload("failed", job_id=101, name="last-job")]
        with patch.object(pipeline, "api_request", side_effect=[first_page, second_page]) as request:
            jobs, truncated = pipeline.fetch_pipeline_jobs(
                {"base_url": "https://gitlab.example.test", "private_token": "secret"},
                "group/project",
                88,
            )
        self.assertEqual(101, len(jobs))
        self.assertFalse(truncated)
        self.assertEqual("2", request.call_args.kwargs["query"][1][1])

    def test_result_includes_bridges_and_waits_for_downstream_success(self) -> None:
        ordinary = {**job_payload("success", job_id=1, name="publish-api"), "stage": "build_artifacts"}
        for bridge_status, downstream_status in (
            ("running", "running"), ("failed", "failed"), ("success", "running"),
            ("success", None), ("success", "success"),
        ):
            with self.subTest(bridge=bridge_status, downstream=downstream_status):
                bridge = {
                    **job_payload(bridge_status, job_id=2, name="publish-child"),
                    "stage": "build_artifacts",
                    "downstream_pipeline": pipeline_payload(downstream_status) if downstream_status else None,
                }
                with patch.object(pipeline, "api_request", side_effect=[
                    pipeline_payload("running"), [ordinary], [bridge],
                ]) as request:
                    result = pipeline.get_pipeline_result({}, "group/project", 88, parameters={"available": True})
                self.assertTrue(request.call_args_list[2].args[2].endswith("/bridges"))
                stage = result["stage_summary"]["build_artifacts"]
                self.assertEqual(2, stage["total"])
                self.assertEqual(downstream_status == "success", stage["all_success"])

    def test_bridge_truncation_blocks_stage_success(self) -> None:
        bridge = {
            **job_payload("success", job_id=2, name="publish-child"),
            "downstream_pipeline": pipeline_payload("success"),
        }
        with patch.object(pipeline, "JOBS_PER_PAGE", 1), patch.object(pipeline, "MAX_JOB_PAGES", 2), patch.object(
            pipeline, "api_request", side_effect=[pipeline_payload("running"), [], [bridge], [bridge]]
        ) as request:
            result = pipeline.get_pipeline_result({}, "group/project", 88, parameters={"available": True})
        self.assertEqual("2", dict(request.call_args.kwargs["query"])["page"])
        self.assertTrue(result["job_summary"]["truncated"])
        self.assertFalse(result["stage_summary"]["deploy"]["all_success"])

    def test_bridge_query_failure_does_not_release_stage(self) -> None:
        for response in (pipeline.PipelineRequestError("unavailable", status_code=403), [None]):
            with self.subTest(response=response), patch.object(pipeline, "api_request", side_effect=[
                pipeline_payload("running"), [job_payload("success", job_id=1, name="publish")], response,
            ]):
                with self.assertRaises(pipeline.PipelineError):
                    pipeline.get_pipeline_result({}, "group/project", 88, parameters={"available": True})

    def test_pipeline_parameters_expose_only_environment_and_module(self) -> None:
        payload = [
            {"key": "ENVIRONMENT_NAME", "value": "test"},
            {"key": "SUB_MODULE", "value": "all"},
            {"key": "DEPLOY_PASSWORD", "value": "must-not-be-returned"},
        ]
        with patch.object(pipeline, "api_request", return_value=payload):
            parameters = pipeline.fetch_pipeline_parameters(
                {"base_url": "https://gitlab.example.test", "private_token": "secret"},
                "group/project",
                88,
            )
        self.assertEqual("test", parameters["environment"])
        self.assertEqual("all", parameters["module"])
        self.assertNotIn("must-not-be-returned", json.dumps(parameters))

    def test_latest_result_is_filtered_by_ref(self) -> None:
        with patch.object(
            pipeline,
            "api_request",
            return_value=[pipeline_payload("success", pipeline_id=99)],
        ) as request:
            pipeline_id = pipeline.latest_pipeline_id(
                {"base_url": "https://gitlab.example.test", "private_token": "secret"},
                "group/project",
                "release",
            )
        self.assertEqual(99, pipeline_id)
        self.assertIn(("ref", "release"), request.call_args.kwargs["query"])

    def test_monitor_emits_changes_and_stops_on_success(self) -> None:
        running = pipeline.build_pipeline_result(
            pipeline.summarize_pipeline(pipeline_payload("running")),
            [pipeline.summarize_job(job_payload("running", job_id=1, name="deploy"))],
        )
        success = pipeline.build_pipeline_result(
            pipeline.summarize_pipeline(pipeline_payload("success")),
            [pipeline.summarize_job(job_payload("success", job_id=1, name="deploy"))],
        )
        clock = FakeClock()
        events = []
        with patch.object(
            pipeline,
            "fetch_pipeline_parameters",
            return_value={"available": True, "environment": "test", "module": "all"},
        ), patch.object(pipeline, "get_pipeline_result", side_effect=[running, success]):
            result = pipeline.monitor_pipeline(
                {"base_url": "https://gitlab.example.test", "private_token": "secret"},
                "group/project",
                88,
                interval=5,
                timeout=30,
                emit=events.append,
                sleeper=clock.sleep,
                clock=clock,
            )
        self.assertEqual("success", result["outcome"])
        self.assertEqual(2, len(events))

    def test_monitor_timeout_does_not_turn_pipeline_into_failure(self) -> None:
        running = pipeline.build_pipeline_result(
            pipeline.summarize_pipeline(pipeline_payload("running")),
            [pipeline.summarize_job(job_payload("running", job_id=1, name="deploy"))],
        )
        clock = FakeClock()
        with patch.object(
            pipeline,
            "fetch_pipeline_parameters",
            return_value={"available": True, "environment": "test", "module": "all"},
        ), patch.object(pipeline, "get_pipeline_result", return_value=running):
            result = pipeline.monitor_pipeline(
                {"base_url": "https://gitlab.example.test", "private_token": "secret"},
                "group/project",
                88,
                interval=5,
                timeout=10,
                emit=None,
                sleeper=clock.sleep,
                clock=clock,
            )
        self.assertEqual("timeout", result["outcome"])
        self.assertEqual("running", result["pipeline"]["status"])
        self.assertTrue(result["timed_out"])

    def test_api_request_sends_private_token_only_in_header(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout=30):
            captured["url"] = request.full_url
            captured["token"] = request.headers.get("Private-token") or request.headers.get("PRIVATE-TOKEN")
            return FakeResponse(pipeline_payload("created"))

        with patch.object(pipeline.urllib_request, "urlopen", side_effect=fake_urlopen):
            pipeline.api_request(
                {
                    "base_url": "https://gitlab.example.test",
                    "private_token": "glpat-secret-token",
                },
                "GET",
                "/user",
            )
        self.assertEqual("glpat-secret-token", captured["token"])
        self.assertNotIn("glpat-secret-token", captured["url"])

    def test_api_request_marks_5xx_retryable_but_not_401(self) -> None:
        for status, retryable in ((503, True), (401, False)):
            error = HTTPError(
                "https://gitlab.example.test/api/v4/user",
                status,
                "error",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"request failed"}'),
            )
            with self.subTest(status=status), patch.object(
                pipeline.urllib_request,
                "urlopen",
                side_effect=error,
            ):
                with self.assertRaises(pipeline.PipelineRequestError) as caught:
                    pipeline.api_request(
                        {
                            "base_url": "https://gitlab.example.test",
                            "private_token": "secret",
                        },
                        "GET",
                        "/user",
                    )
                self.assertEqual(retryable, caught.exception.retryable)


if __name__ == "__main__":
    unittest.main()
