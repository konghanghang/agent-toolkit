import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tapd_api import TapdError  # noqa: E402
from tapd_story import (  # noqa: E402
    _attachment_dir,
    _hydrate_story_media,
    _image_dir,
    _list_attachments,
    _render_attachment_lines,
    _validate_spec,
)


class SpecTest(unittest.TestCase):
    def test_default_image_directory_uses_system_temporary_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "tapd_story.tempfile.gettempdir",
            return_value=temp_dir,
        ):
            path = _image_dir(SimpleNamespace(image_dir=None), "123")

        self.assertEqual(Path(temp_dir) / "tapd-story-123" / "images", path)

    def test_default_attachment_directory_uses_system_temporary_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "tapd_story.tempfile.gettempdir",
            return_value=temp_dir,
        ):
            path = _attachment_dir(SimpleNamespace(attachment_dir=None), "123")

        self.assertEqual(Path(temp_dir) / "tapd-story-123" / "attachments", path)

    def test_hydrate_uses_raw_story_id_as_entry_id(self):
        captured = {}

        def list_story_attachments(workspace_id, story_id):
            captured["entry_id"] = story_id
            return [{"id": "att-1", "filename": "客户原始需求.docx"}]

        args = SimpleNamespace(
            download_images=False,
            download_attachments=False,
            id="123",
            image_dir=None,
            attachment_dir=None,
        )
        story = {"story_id": "123", "workspace_id": "ws", "images": []}
        _hydrate_story_media(
            SimpleNamespace(list_story_attachments=list_story_attachments),
            args,
            story,
            {"id": "1110000000001000123"},
        )
        self.assertEqual("1110000000001000123", captured["entry_id"])
        self.assertEqual("att-1", story["attachments"][0]["id"])
        self.assertNotIn("local_path", story["attachments"][0])

    def test_list_attachments_records_error_without_raising(self):
        client = SimpleNamespace(
            list_story_attachments=lambda workspace_id, story_id: (_ for _ in ()).throw(
                TapdError("附件接口失败")
            )
        )
        story = {"workspace_id": "ws", "story_id": "123"}
        _list_attachments(client, story, "123")
        self.assertEqual([], story["attachments"])
        self.assertEqual("附件接口失败", story["attachment_error"])

    def test_render_attachment_lines_includes_local_path_and_guidance(self):
        lines = _render_attachment_lines(
            {
                "attachments": [
                    {
                        "index": 1,
                        "id": "att-1",
                        "filename": "客户原始需求.docx",
                        "local_path": "/tmp/tapd-story-123/attachments/01-客户原始需求.docx",
                    }
                ]
            }
        )
        rendered = "\n".join(lines)
        self.assertIn("## 需求附件", rendered)
        self.assertIn("客户原始需求.docx", rendered)
        self.assertIn("正文足够时不要打开", rendered)

    def test_create_single_is_valid(self):
        operations = _validate_spec(
            "create",
            {
                "story": {
                    "name": "需求标题",
                    "description_html": "<h3>背景</h3><p>正文</p>",
                }
            },
        )
        self.assertEqual("story", operations[0]["role"])

    def test_create_rejects_id(self):
        with self.assertRaises(TapdError):
            _validate_spec(
                "create",
                {
                    "story": {
                        "id": "1",
                        "name": "需求标题",
                        "description_html": "<p>正文</p>",
                    }
                },
            )

    def test_update_requires_id(self):
        with self.assertRaises(TapdError):
            _validate_spec("update", {"stories": [{"description_html": "<p>正文</p>"}]})

    def test_update_only_changes_explicit_fields(self):
        operations = _validate_spec(
            "update",
            {"stories": [{"id": "1", "owner": ""}]},
        )
        self.assertEqual(["owner"], operations[0]["fields"])

    def test_owner_must_be_string(self):
        with self.assertRaises(TapdError):
            _validate_spec(
                "create",
                {
                    "story": {
                        "name": "需求标题",
                        "description_html": "<p>正文</p>",
                        "owner": None,
                    }
                },
            )


if __name__ == "__main__":
    unittest.main()
