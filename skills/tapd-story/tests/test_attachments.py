import io
import json
import sys
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from tapd_api import TapdClient
from tapd_story import main


class AttachmentCommandTest(unittest.TestCase):
    def run_detail(self, options=(), failure=None, list_failure=None):
        client = TapdClient()
        client.get_story = Mock(return_value=({}, {
            "id": "123", "name": "附件需求", "description": "<p>正文保留</p>",
        }))
        client.list_story_attachments = Mock(return_value=[
            {"id": "a", "filename": "a.txt"}, {"id": "b", "filename": "b.txt"},
        ])
        if list_failure is not None:
            client.list_story_attachments.side_effect = list_failure
        client.get_attachment_download = Mock(side_effect=lambda ws, aid: {
            "download_url": "https://example.invalid/" + aid + "?token=secret",
        })
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"attachment content"
        response.headers = {}
        if failure is not None:
            response.read.side_effect = [failure, b"attachment content"]
        with tempfile.TemporaryDirectory() as directory:
            argv = ["tapd_story.py", "detail", "--id", "123", "--workspace-id", "ws",
                    "--format", "json", "--attachment-dir", directory, *options]
            output = io.StringIO()
            original_write = Path.write_bytes

            def write_bytes(path, data):
                if failure == "disk" and path.name.startswith("01-"):
                    raise OSError("disk full https://example.invalid/a?token=secret")
                return original_write(path, data)

            if failure == "disk":
                response.read.side_effect = None
            with patch.object(sys, "argv", argv), patch("tapd_story.TapdClient", return_value=client), \
                    patch("tapd_api.urlopen", return_value=response) as fetch, \
                    patch.object(Path, "write_bytes", write_bytes), patch("sys.stdout", output):
                self.assertEqual(0, main())
            payload = json.loads(output.getvalue())
            files = {path.name: path.read_bytes() for path in Path(directory).iterdir()}
        return payload["story"], client, fetch.call_count, files

    def test_default_lists_without_downloading(self):
        story, client, calls, files = self.run_detail()
        self.assertEqual(2, len(story["attachments"]))
        client.get_attachment_download.assert_not_called()
        self.assertEqual(0, calls)
        self.assertEqual({}, files)

    def test_list_failure_preserves_body_without_exposing_signed_url(self):
        story, client, calls, files = self.run_detail(
            list_failure=TimeoutError("https://example.invalid/a?token=secret"),
        )
        self.assertIn("正文保留", json.dumps(story, ensure_ascii=False))
        self.assertIn("attachment_error", story)
        self.assertNotIn("token=secret", json.dumps(story))
        client.get_attachment_download.assert_not_called()
        self.assertEqual(0, calls)
        self.assertEqual({}, files)

    def test_selected_id_downloads_only_that_attachment(self):
        story, client, calls, files = self.run_detail(["--attachment-id", "b"])
        client.get_attachment_download.assert_called_once_with("ws", "b")
        self.assertNotIn("local_path", story["attachments"][0])
        self.assertEqual({"02-b.txt": b"attachment content"}, files)
        self.assertEqual(1, calls)

    def test_repeated_ids_download_once_each(self):
        _, client, calls, files = self.run_detail([
            "--attachment-id", "b", "--attachment-id", "a", "--attachment-id", "b",
        ])
        self.assertEqual(2, calls)
        self.assertEqual(2, client.get_attachment_download.call_count)
        self.assertEqual(2, len(files))

    def test_unknown_id_reports_error_without_downloading_all(self):
        story, client, calls, files = self.run_detail(["--attachment-id", "missing"])
        self.assertIn("missing", story["attachment_selection_error"])
        client.get_attachment_download.assert_not_called()
        self.assertEqual(0, calls)
        self.assertEqual({}, files)

    def test_failed_attachment_preserves_body_and_downloads_next(self):
        failures = [TimeoutError("https://example.invalid/a?token=secret"),
                    IncompleteRead(b"partial"), ConnectionResetError("reset"), "disk"]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__ if failure != "disk" else failure):
                story, _, calls, files = self.run_detail(["--download-attachments"], failure)
                self.assertIn("正文保留", json.dumps(story, ensure_ascii=False))
                self.assertIn("download_error", story["attachments"][0])
                self.assertNotIn("local_path", story["attachments"][0])
                self.assertNotIn("token=secret", json.dumps(story))
                self.assertEqual(2, calls)
                self.assertEqual({"02-b.txt": b"attachment content"}, files)

    def test_all_and_selection_are_mutually_exclusive(self):
        with patch.object(sys, "argv", ["tapd_story.py", "detail", "--id", "123",
                                       "--download-attachments", "--attachment-id", "a"]), \
                patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as raised:
            main()
        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
