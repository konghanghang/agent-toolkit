import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tapd_api import TapdError  # noqa: E402
from tapd_story import _validate_spec  # noqa: E402


class SpecTest(unittest.TestCase):
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
