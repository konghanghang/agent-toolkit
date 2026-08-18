from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_TMP_DOCUMENTS = (
    ROOT / "skills" / "aliyun-sls-log-query" / "SKILL.md",
    ROOT / "skills" / "aliyun-sls-log-query" / "references" / "advanced-query.md",
    ROOT / "skills" / "online-issue-triage" / "references" / "trace-triage.md",
)
SYSTEM_TMP_DOCUMENTS = (
    ROOT / "skills" / "tapd-story" / "SKILL.md",
    ROOT / "skills" / "tapd-story" / "references" / "agent-orchestration.md",
)


class CrossPlatformSkillPathTest(unittest.TestCase):
    def test_local_artifact_docs_do_not_hardcode_posix_tmp(self) -> None:
        for path in (*RUNTIME_TMP_DOCUMENTS, *SYSTEM_TMP_DOCUMENTS):
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn("/tmp", path.read_text(encoding="utf-8"))

    def test_sls_docs_use_runtime_tmp_dir(self) -> None:
        for path in RUNTIME_TMP_DOCUMENTS:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn("TMP_DIR", path.read_text(encoding="utf-8"))

    def test_tapd_docs_use_python_system_temporary_directory(self) -> None:
        for path in SYSTEM_TMP_DOCUMENTS:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn("tempfile.gettempdir()", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
