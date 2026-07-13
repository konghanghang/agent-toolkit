import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tapd_repo_scan import build_repo_terms, classify_path, detect_project_root, rank_candidate_files  # noqa: E402


class RepoScanTest(unittest.TestCase):
    def test_classification_is_not_backend_first(self):
        self.assertEqual("source", classify_path("src/main/java/Demo.java"))
        self.assertEqual("source", classify_path("src/views/Demo.vue"))
        self.assertEqual("test", classify_path("tests/test_demo.py"))

    def test_ranking_prefers_term_diversity(self):
        hits = [
            {"path": "a.md", "term": "one", "category": "doc"},
            {"path": "a.md", "term": "two", "category": "doc"},
            {"path": "b.py", "term": "one", "category": "source"},
            {"path": "b.py", "term": "one", "category": "source"},
            {"path": "b.py", "term": "one", "category": "source"},
        ]
        self.assertEqual("a.md", rank_candidate_files(hits)[0]["path"])

    def test_repo_terms_drop_generic_protocol_words(self):
        terms = build_repo_terms(
            {
                "story_id": "123",
                "search_terms": ["完整标题", "cashcash", "POST", "platform", "data"],
            }
        )
        self.assertEqual(["123", "完整标题", "cashcash"], terms)

    def test_detect_project_root_uses_git_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "group" / "project"
            file_path = project / "src" / "Demo.java"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("class Demo {}", encoding="utf-8")
            (project / ".git").mkdir()
            self.assertEqual(project.resolve(), detect_project_root(str(file_path), root))


if __name__ == "__main__":
    unittest.main()
