import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tapd_repo_scan import (  # noqa: E402
    _run_rg,
    build_repo_terms,
    classify_path,
    detect_project_root,
    rank_candidate_files,
)


class RepoScanTest(unittest.TestCase):
    def test_rg_json_preserves_posix_and_windows_paths(self):
        events = [
            {
                "type": "match",
                "data": {
                    "path": {"text": "/work/repo/src/Demo.java"},
                    "lines": {"text": "class Demo {}\n"},
                    "line_number": 12,
                },
            },
            {
                "type": "match",
                "data": {
                    "path": {"text": "C:\\work\\repo\\src\\Demo.java"},
                    "lines": {"text": "String value = \"a:b\";\r\n"},
                    "line_number": 34,
                },
            },
            {"type": "summary", "data": {}},
        ]
        output = "\n".join(json.dumps(event) for event in events)
        completed = subprocess.CompletedProcess(["rg"], 0, stdout=output, stderr="")

        with mock.patch("tapd_repo_scan.subprocess.run", return_value=completed) as run:
            hits = _run_rg("Demo", Path("C:/work/repo"))

        command = run.call_args.args[0]
        self.assertIn("--json", command)
        self.assertEqual(
            [
                {
                    "path": "/work/repo/src/Demo.java",
                    "line": "12",
                    "snippet": "class Demo {}",
                    "term": "Demo",
                    "category": "source",
                },
                {
                    "path": "C:\\work\\repo\\src\\Demo.java",
                    "line": "34",
                    "snippet": 'String value = "a:b";',
                    "term": "Demo",
                    "category": "source",
                },
            ],
            hits,
        )

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
