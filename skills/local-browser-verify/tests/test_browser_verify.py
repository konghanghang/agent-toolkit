from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "browser_verify.py"
SPEC = importlib.util.spec_from_file_location("browser_verify", SCRIPT_PATH)
assert SPEC and SPEC.loader
browser_verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(browser_verify)


class ProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/ok":
            body = b"<html><head><title>Local Test Page</title></head></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class BrowserVerifyTest(unittest.TestCase):
    def test_url_scope_rejects_remote_and_embedded_credentials(self) -> None:
        browser_verify.validate_url("http://127.0.0.1:4173/page", allow_remote=False)
        browser_verify.validate_url("http://app.localhost/page", allow_remote=False)

        with self.assertRaisesRegex(ValueError, "Remote target"):
            browser_verify.validate_url("https://example.com", allow_remote=False)
        with self.assertRaisesRegex(ValueError, "Credentials"):
            browser_verify.validate_url(
                "http://user:secret@localhost:4173", allow_remote=False
            )

    def test_probe_reports_status_and_html_title(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            success = browser_verify.probe_url(f"{base_url}/ok")
            missing = browser_verify.probe_url(f"{base_url}/missing")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertTrue(success["ok"])
        self.assertEqual(200, success["status"])
        self.assertEqual("Local Test Page", success["title"])
        self.assertFalse(missing["ok"])
        self.assertEqual(404, missing["status"])

    def test_probe_redacts_query_values_from_results(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = (
                f"http://127.0.0.1:{server.server_port}/ok"
                "?token=secret&view=wide"
            )
            result = browser_verify.probe_url(url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        serialized = str(result)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("wide", serialized)
        self.assertIn("token=REDACTED", result["requested_url"])
        self.assertIn("view=REDACTED", result["requested_url"])

    def test_prepare_and_finish_isolate_browser_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_root = Path(temp_directory)
            repository = temp_root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repository)], check=True)

            artifact_directory = repository / ".playwright-mcp"
            artifact_directory.mkdir()
            existing_artifact = artifact_directory / "existing.png"
            existing_artifact.write_bytes(b"before")
            baseline_path = temp_root / "baseline.json"

            _, written_baseline = browser_verify.prepare(
                repository, [], baseline_path=baseline_path
            )
            self.assertEqual(baseline_path.resolve(), written_baseline)

            existing_artifact.write_bytes(b"after with a different size")
            (artifact_directory / "new.har").write_text("new", encoding="utf-8")
            (repository / "ordinary-user-file.txt").write_text("keep", encoding="utf-8")

            result = browser_verify.finish(
                baseline_path,
                repository=repository,
                remove_baseline=True,
            )

            self.assertIn(".playwright-mcp/new.har", result["created_artifacts"])
            self.assertIn(
                ".playwright-mcp/existing.png", result["modified_artifacts"]
            )
            self.assertNotIn("ordinary-user-file.txt", result["created_artifacts"])
            self.assertTrue(
                any(
                    "ordinary-user-file.txt" in entry
                    for entry in result["git_status_added"]
                )
            )
            self.assertTrue(result["baseline_removed"])
            self.assertFalse(baseline_path.exists())

    def test_finish_refuses_a_different_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_root = Path(temp_directory)
            first_repository = temp_root / "first"
            second_repository = temp_root / "second"
            first_repository.mkdir()
            second_repository.mkdir()
            baseline_path = temp_root / "baseline.json"
            browser_verify.prepare(first_repository, [], baseline_path=baseline_path)

            with self.assertRaisesRegex(ValueError, "repository mismatch"):
                browser_verify.finish(baseline_path, repository=second_repository)


if __name__ == "__main__":
    unittest.main()
