from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_toolkit_notify_grokbot",
    ROOT / "hooks" / "notify_grokbot.py",
)
assert SPEC is not None and SPEC.loader is not None
notify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notify)


class _DummyResp:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"ok"


class NotifyGrokBotTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name)
        self.primary_env = self.temp / "primary.env"
        self.legacy_env = self.temp / "legacy.env"
        self.debounce = self.temp / "debounce.json"
        self.log_file = self.temp / "hook.log"
        self.legacy_env.write_text(
            "WEBHOOK_URL=https://example.invalid/hook\nWEBHOOK_KEY=secret-key\n",
            encoding="utf-8",
        )
        patcher = mock.patch.multiple(
            notify,
            PRIMARY_ENV_FILE=self.primary_env,
            LEGACY_ENV_FILE=self.legacy_env,
            DEBOUNCE_FILE=self.debounce,
            LOG_FILE=self.log_file,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_hook(self, tool_name: str, payload: dict, session: str | None = "side-demo"):
        sent = []

        def fake_urlopen(req, timeout=3):
            sent.append((req.full_url, json.loads(req.data.decode("utf-8"))))
            return _DummyResp()

        stdout = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%1"}),
            mock.patch.object(notify, "find_binary", return_value="/usr/bin/tmux"),
            mock.patch.object(notify, "session_from_env", return_value=session),
            mock.patch.object(notify.urllib.request, "urlopen", side_effect=fake_urlopen),
            mock.patch("sys.stdin", io.StringIO(json.dumps(payload))),
            mock.patch("sys.stdout", stdout),
        ):
            rc = notify.main(["--tool", tool_name])
        return rc, stdout.getvalue(), sent

    def test_agy_stop_outputs_json_and_sends_webhook(self) -> None:
        rc, out, sent = self._run_hook(
            "agy",
            {
                "hookEventName": "Stop",
                "conversationId": "conv-123",
                "executionNum": 2,
                "terminationReason": "NO_TOOL_CALL",
                "modelName": "gemini-pro",
                "cwd": "/tmp/work",
                "continuationActive": False,
            },
        )
        self.assertEqual(0, rc)
        self.assertEqual("{}\n", out)
        self.assertEqual(1, len(sent))
        url, body = sent[0]
        self.assertEqual("https://example.invalid/hook", url)
        self.assertEqual("agy-hook", body["source"])
        self.assertEqual("agy", body["tool"])
        self.assertEqual("Stop", body["hook_event"])
        self.assertEqual("conv-123", body["session_id"])
        self.assertEqual("NO_TOOL_CALL", body["reason"])
        self.assertEqual("gemini-pro", body["model"])

    def test_agy_skips_continuation_active_but_still_outputs_json(self) -> None:
        rc, out, sent = self._run_hook(
            "agy",
            {"hookEventName": "Stop", "conversationId": "c1", "continuationActive": True},
        )
        self.assertEqual(0, rc)
        self.assertEqual("{}\n", out)
        self.assertEqual([], sent)

    def test_claude_skips_grok_compat_payload_and_reads_transcript(self) -> None:
        rc, out, sent = self._run_hook(
            "claude",
            {"hookEventName": "Stop", "sessionId": "s1"},
        )
        self.assertEqual(0, rc)
        self.assertEqual("", out)
        self.assertEqual([], sent)

        transcript = self.temp / "transcript.jsonl"
        transcript.write_text(
            json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}}) + "\n",
            encoding="utf-8",
        )
        rc, out, sent = self._run_hook(
            "claude",
            {"hook_event_name": "Stop", "session_id": "s2", "transcript_path": str(transcript)},
        )
        self.assertEqual(0, rc)
        self.assertEqual("", out)
        self.assertEqual(1, len(sent))
        self.assertEqual("done", sent[0][1]["last_assistant_message"])

    def test_grok_skips_subagent_and_shutdown(self) -> None:
        _, _, sent1 = self._run_hook("grok", {"hookEventName": "Stop", "subagentType": "worker"})
        _, _, sent2 = self._run_hook("grok", {"hookEventName": "Stop", "reason": "shutdown"})
        self.assertEqual([], sent1)
        self.assertEqual([], sent2)

    def test_non_bot_session_and_debounce_are_skipped(self) -> None:
        _, _, sent = self._run_hook("codex", {"hook_event_name": "Stop", "session_id": "s1"}, session="main")
        self.assertEqual([], sent)

        _, out1, sent1 = self._run_hook("codex", {"hook_event_name": "Stop", "session_id": "s1", "turn_id": "t1"})
        _, out2, sent2 = self._run_hook("codex", {"hook_event_name": "Stop", "session_id": "s1", "turn_id": "t1"})
        self.assertEqual("{}\n", out1)
        self.assertEqual("{}\n", out2)
        self.assertEqual(1, len(sent1))
        self.assertEqual(0, len(sent2))

    def test_primary_env_overrides_legacy_env(self) -> None:
        self.primary_env.write_text(
            "WEBHOOK_URL=https://primary.invalid/hook\nWEBHOOK_KEY=primary-key\n",
            encoding="utf-8",
        )
        _, _, sent = self._run_hook("agy", {"hookEventName": "Stop", "conversationId": "c-primary"})
        self.assertEqual("https://primary.invalid/hook", sent[0][0])


if __name__ == "__main__":
    unittest.main()
