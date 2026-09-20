import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tapd_api import (  # noqa: E402
    TapdClient,
    TapdError,
    _auth_header,
    _detect_image_extension,
    _extract_attachments,
    _extract_story,
    _redact_url_query,
    _safe_attachment_filename,
)


class _FakeResponse:
    def __init__(self, data: bytes, headers=None) -> None:
        self._data = data
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class ApiTest(unittest.TestCase):
    def test_auth_uses_tapd_token(self):
        with patch.dict(os.environ, {"TAPD_TOKEN": "demo-token"}, clear=True):
            self.assertEqual("Bearer demo-token", _auth_header())

    def test_auth_does_not_fallback_to_user_password(self):
        with patch.dict(
            os.environ,
            {"TAPD_API_USER": "demo-user", "TAPD_API_PASSWORD": "demo-password"},
            clear=True,
        ):
            with self.assertRaises(TapdError):
                _auth_header()

    def test_extract_story_from_list_wrapper(self):
        story = _extract_story([{"Story": {"id": "1", "name": "demo"}}])
        self.assertEqual("1", story["id"])

    def test_detect_png_signature(self):
        self.assertEqual(".png", _detect_image_extension(b"\x89PNG\r\n\x1a\nrest", "unknown"))

    def test_reject_unknown_image_content(self):
        with self.assertRaises(TapdError):
            _detect_image_extension(b"not-an-image", "unknown.bin")

    def test_extract_attachments_from_list_wrapper(self):
        attachments = _extract_attachments(
            [{"Attachment": {"id": "1", "filename": "客户原始需求.docx"}}]
        )
        self.assertEqual("1", attachments[0]["id"])
        self.assertEqual("客户原始需求.docx", attachments[0]["filename"])

    def test_safe_attachment_filename_keeps_unicode(self):
        self.assertEqual(
            "客户原始需求.docx",
            _safe_attachment_filename("客户原始需求.docx", "attachment-1"),
        )
        self.assertEqual("passwd", _safe_attachment_filename("../../etc/passwd", "attachment-1"))

    def test_redact_signed_download_query(self):
        self.assertEqual(
            "https://file.tapd.cn/down?<redacted>",
            _redact_url_query("https://file.tapd.cn/down?token=secret"),
        )

    def test_list_story_attachments_reads_all_pages(self):
        client = TapdClient("https://api.tapd.cn")
        client._request_json = Mock(
            side_effect=[
                {
                    "status": 1,
                    "data": [
                        {"Attachment": {"id": "1", "filename": "one.md"}},
                        {"Attachment": {"id": "2", "filename": "two.md"}},
                    ],
                },
                {
                    "status": 1,
                    "data": [{"Attachment": {"id": "3", "filename": "three.md"}}],
                },
            ]
        )

        attachments = client.list_story_attachments("ws", "story-1", limit=2)

        self.assertEqual(["1", "2", "3"], [item["id"] for item in attachments])
        self.assertEqual(2, client._request_json.call_count)
        client._request_json.assert_any_call(
            "GET",
            "/attachments",
            params={
                "workspace_id": "ws",
                "entry_id": "story-1",
                "limit": 2,
                "page": 1,
            },
        )

    def test_download_attachment_writes_local_file_without_signed_url(self):
        client = TapdClient("https://api.tapd.cn")
        client.get_attachment_download = Mock(
            return_value={
                "id": "att-1",
                "filename": "客户原始需求.docx",
                "download_url": "https://file.tapd.cn/down?token=secret",
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "tapd_api.urlopen",
            return_value=_FakeResponse(b"docx-bytes", {"Content-Type": "application/octet-stream"}),
        ):
            result = client.download_attachment("ws", "att-1", Path(temp_dir), 1, "客户原始需求.docx")
            self.assertEqual("客户原始需求.docx", result["filename"])
            self.assertTrue(Path(result["local_path"]).is_file())
            self.assertEqual(b"docx-bytes", Path(result["local_path"]).read_bytes())
            self.assertNotIn("download_url", result)
            self.assertNotIn("token=secret", str(result))


if __name__ == "__main__":
    unittest.main()
