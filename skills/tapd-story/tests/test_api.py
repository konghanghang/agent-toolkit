import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tapd_api import TapdError, _detect_image_extension, _extract_story  # noqa: E402


class ApiTest(unittest.TestCase):
    def test_extract_story_from_list_wrapper(self):
        story = _extract_story([{"Story": {"id": "1", "name": "demo"}}])
        self.assertEqual("1", story["id"])

    def test_detect_png_signature(self):
        self.assertEqual(".png", _detect_image_extension(b"\x89PNG\r\n\x1a\nrest", "unknown"))

    def test_reject_unknown_image_content(self):
        with self.assertRaises(TapdError):
            _detect_image_extension(b"not-an-image", "unknown.bin")


if __name__ == "__main__":
    unittest.main()
