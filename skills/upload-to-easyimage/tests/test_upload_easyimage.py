from __future__ import annotations

import importlib.util
import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "upload_easyimage.py"


def load_module():
    spec = importlib.util.spec_from_file_location("upload_easyimage_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


upload = load_module()


def png_bytes(width: int = 3, height: int = 2) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00" * 8
        + struct.pack(">II", width, height)
        + b"fixture"
    )


class FakeRemoteResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/octet-stream",
        final_url: str = "https://images.example/final.png",
    ):
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.final_url = final_url
        self.read_size = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size: int) -> bytes:
        self.read_size = size
        return self.body[:size]

    def geturl(self) -> str:
        return self.final_url


def write_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "picBed": {
                    "web-uploader": {
                        "url": "https://upload.example/api",
                        "paramName": "file",
                        "jsonPath": "data.links.0",
                        "customHeader": json.dumps(
                            {"Authorization": "secret-header"}
                        ),
                        "customBody": {"album": "secret-album"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )


class UploadEasyImageTest(unittest.TestCase):
    def test_config_loading_and_sanitizing_never_expose_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            write_config(config_path)

            cfg = upload.load_picgo_config(config_path)
            sanitized = upload.sanitized_config(cfg)

        self.assertEqual("file", cfg["param_name"])
        self.assertEqual("data.links.0", cfg["json_path"])
        self.assertEqual(
            ["Authorization"],
            sanitized["custom_header_keys"],
        )
        self.assertEqual(["album"], sanitized["custom_body_keys"])
        serialized = json.dumps(sanitized)
        self.assertNotIn("secret-header", serialized)
        self.assertNotIn("secret-album", serialized)

    def test_invalid_config_shapes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                json.dumps({"picBed": {"web-uploader": {"url": ""}}}),
                encoding="utf-8",
            )
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                upload.load_picgo_config(config_path)

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            upload.parse_json_object("[1, 2]", "customHeader")

    def test_extract_json_path_supports_nested_lists_and_rejects_missing_data(self) -> None:
        payload = {"data": {"links": ["https://images.example/image.png"]}}
        self.assertEqual(
            "https://images.example/image.png",
            upload.extract_json_path(payload, "data.links.0"),
        )
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            upload.extract_json_path(payload, "data.links.1")

    def test_image_detection_uses_file_content_and_dimensions(self) -> None:
        self.assertEqual(
            {"format": "png", "width": 640, "height": 360},
            upload.detect_image(png_bytes(640, 360)),
        )
        svg = b'<svg viewBox="0 0 1200 630" xmlns="http://www.w3.org/2000/svg"/>'
        self.assertEqual(
            {"format": "svg", "width": 1200, "height": 630},
            upload.detect_image(svg),
        )
        self.assertEqual(
            {"format": None, "width": None, "height": None},
            upload.detect_image(b"<html>not an image</html>"),
        )

    def test_svg_conversion_builds_a_bounded_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.svg"
            destination = root / "converted" / "source.png"
            source.write_text("<svg/>", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                ["rsvg-convert"],
                0,
                stdout="",
                stderr="",
            )
            with patch.object(upload, "ensure_tool") as ensure_tool, patch.object(
                upload.subprocess,
                "run",
                return_value=completed,
            ) as run:
                upload.convert_svg_to_png(
                    source,
                    destination,
                    width=1200,
                    height=630,
                )
            self.assertTrue(destination.parent.is_dir())

        ensure_tool.assert_called_once_with("rsvg-convert")
        self.assertEqual(
            [
                "rsvg-convert",
                "-w",
                "1200",
                "-h",
                "630",
                str(source),
                "-o",
                str(destination),
            ],
            run.call_args.args[0],
        )

    def test_upload_uses_configured_fields_and_resolves_http_url(self) -> None:
        cfg = {
            "url": "https://upload.example/api",
            "param_name": "file",
            "json_path": "data.links.0",
            "custom_header": {"Authorization": "test-header"},
            "custom_body": {"album": "test-album"},
        }
        response = {
            "result": "success",
            "data": {"links": ["https://images.example/image.png"]},
            "thumb": "https://images.example/thumb.png",
        }
        completed = subprocess.CompletedProcess(
            ["curl"],
            0,
            stdout=json.dumps(response),
            stderr="",
        )
        image_path = Path("images") / "source.png"
        with patch.object(upload, "ensure_tool") as ensure_tool, patch.object(
            upload.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = upload.upload_file(image_path, cfg)

        ensure_tool.assert_called_once_with("curl")
        command = run.call_args.args[0]
        self.assertIn("Authorization: test-header", command)
        self.assertIn(f"file=@{image_path}", command)
        self.assertIn("album=test-album", command)
        self.assertEqual(
            "https://images.example/image.png",
            result["_resolved_url"],
        )

    def test_upload_rejects_failed_or_non_http_responses(self) -> None:
        cfg = {
            "url": "https://upload.example/api",
            "param_name": "file",
            "json_path": "url",
            "custom_header": {},
            "custom_body": {},
        }
        responses = (
            {"result": "failed", "url": "https://images.example/image.png"},
            {"result": "success", "url": "file:///not-http/image.png"},
        )
        for response in responses:
            with self.subTest(response=response):
                completed = subprocess.CompletedProcess(
                    ["curl"],
                    0,
                    stdout=json.dumps(response),
                    stderr="",
                )
                with patch.object(upload, "ensure_tool"), patch.object(
                    upload.subprocess,
                    "run",
                    return_value=completed,
                ), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    upload.upload_file(Path("source.png"), cfg)

    def test_remote_verification_reports_detected_content_not_header(self) -> None:
        response = FakeRemoteResponse(
            png_bytes(16, 9),
            content_type="text/html",
        )
        with patch.object(
            upload.urllib.request,
            "urlopen",
            return_value=response,
        ) as urlopen:
            result = upload.verify_remote_image(
                "https://images.example/image.png",
                timeout=12,
                max_bytes=1024,
            )

        self.assertEqual(1025, response.read_size)
        self.assertEqual("png", result["detected_format"])
        self.assertEqual(16, result["width"])
        self.assertEqual(9, result["height"])
        self.assertEqual("text/html", result["content_type"])
        self.assertEqual(12, urlopen.call_args.kwargs["timeout"])
        request = urlopen.call_args.args[0]
        self.assertEqual(
            "upload-to-easyimage/1.0",
            request.get_header("User-agent"),
        )

    def test_remote_verification_enforces_download_limit(self) -> None:
        response = FakeRemoteResponse(png_bytes() + b"x" * 20)
        with patch.object(
            upload.urllib.request,
            "urlopen",
            return_value=response,
        ), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            upload.verify_remote_image(
                "https://images.example/image.png",
                timeout=1,
                max_bytes=24,
            )

    def test_check_config_cli_prints_only_sanitized_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            write_config(config_path)
            output = io.StringIO()
            argv = [
                str(SCRIPT),
                "--config",
                str(config_path),
                "--check-config",
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(output):
                upload.main()

        rendered = output.getvalue()
        self.assertIn("custom_header_keys", rendered)
        self.assertIn("Authorization", rendered)
        self.assertNotIn("secret-header", rendered)
        self.assertNotIn("secret-album", rendered)

    def test_cli_rejects_partial_or_unrequested_conversion_dimensions(self) -> None:
        cases = (
            ["--width", "1200", "--convert-svg"],
            ["--width", "1200", "--height", "630"],
        )
        for args in cases:
            with self.subTest(args=args):
                with patch.object(
                    sys,
                    "argv",
                    [str(SCRIPT), *args],
                ), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    upload.main()


if __name__ == "__main__":
    unittest.main()
