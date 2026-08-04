import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tapd_content import build_story_view, html_to_text, validate_description_html  # noqa: E402


class ContentTest(unittest.TestCase):
    def test_html_to_text_preserves_order(self):
        html = "<h3>背景</h3><p>第一段</p><ul><li>事项一</li><li>事项二</li></ul>"
        self.assertEqual("背景\n第一段\n- 事项一\n- 事项二", html_to_text(html))

    def test_story_view_extracts_image_and_href(self):
        story = {
            "name": "【示例】图片需求",
            "description": (
                '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
                '<p>接口文档：<a href="https://example.com/doc">查看</a> 密码：ABC123</p>'
                '<img src="/tfl/captures/demo.png?1" />'
            ),
        }
        view = build_story_view("123", "456", story)
        self.assertEqual(["https://example.com/doc"], view["urls"])
        self.assertEqual("/tfl/captures/demo.png?1", view["images"][0]["src"])
        self.assertIn("密码：***", view["detail"])
        self.assertNotIn("ABC123", view["images"][0]["context"])

    def test_html_validator_rejects_style_and_script(self):
        errors = validate_description_html('<p style="color:red">文本</p><script>x</script>')
        self.assertTrue(any("style" in error for error in errors))
        self.assertTrue(any("script" in error for error in errors))

    def test_html_validator_allows_safe_link(self):
        self.assertEqual([], validate_description_html('<p><a href="https://example.com/doc">文档</a></p>'))
        errors = validate_description_html('<p><a href="javascript:alert(1)">错误链接</a></p>')
        self.assertTrue(any("HTTP/HTTPS" in error for error in errors))

    def test_html_validator_allows_compact_description_without_acceptance_section(self):
        html = (
            "<h3>背景</h3><p>取消原因记录不准确。</p>"
            "<h3>需求说明</h3>"
            "<ul><li>智能任务取消记录为智能任务调用取消。</li>"
            "<li>外部 API 取消保持原有记录。</li></ul>"
        )
        self.assertEqual([], validate_description_html(html))


if __name__ == "__main__":
    unittest.main()
