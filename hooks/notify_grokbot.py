#!/usr/bin/env python3
"""Unified turn-end lifecycle hook -> Grok Bot webhook.

Supports Codex, Claude Code, Grok Build, and Antigravity (AGY).
Only fires for bot-opened tmux sessions (side-* / nf-*).
Usage: notify_grokbot.py --tool codex|claude|grok|agy   (event JSON on stdin)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOOLKIT_LOCAL = Path.home() / ".local" / "agent-toolkit"
PRIMARY_ENV_FILE = TOOLKIT_LOCAL / "grokbot-notify.env"
LEGACY_ENV_FILE = Path.home() / ".codex" / "grokbot-notify.env"
DEBOUNCE_FILE = TOOLKIT_LOCAL / "grokbot-notify-debounce.json"
LOG_FILE = Path("/tmp/grokbot/hook.log")
BOT_SESSION_RE = re.compile(r"^(side-|nf-)")
WATCHED = {
    "codex": {"Stop", "SessionEnd", "Interrupt"},
    "claude": {"Stop", "StopFailure"},
    "grok": {"Stop", "StopFailure", "StopCancelled"},
    "agy": {"Stop"},
}
DEBOUNCE_SECONDS = {
    "codex": 90,
    "claude": 60,
    "grok": 60,
    "agy": 60,
}
JSON_STDOUT_TOOLS = {"codex", "agy"}


def log(tool_name: str, event: str, session: str | None, status: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as file:
            file.write(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} tool={tool_name or '-'} "
                f"event={event or '-'} session={session or '-'} {status}\n"
            )
    except OSError:
        pass


def find_binary(name: str) -> str | None:
    for candidate in (
        shutil.which(name),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"/usr/bin/{name}",
        f"/usr/sbin/{name}",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def run_cmd(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        return result.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def session_from_env(tmux: str) -> str | None:
    # 仅信任当前 hook 所在的 tmux pane，避免在 tmux 外部误读其他活跃会话。
    pane = os.environ.get("TMUX_PANE")
    if not os.environ.get("TMUX") or not pane:
        return None
    return run_cmd([tmux, "display-message", "-p", "-t", pane, "#S"]).strip() or None


def proc_cwd(lsof: str | None, pid: str) -> str | None:
    if not lsof:
        return None
    for line in run_cmd([lsof, "-a", "-p", pid, "-d", "cwd", "-Fn"]).splitlines():
        if line.startswith("n"):
            return os.path.realpath(line[1:])
    return None


def snippets(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        cleaned = re.sub(r"[\s`*_#>\[\]()|•\-]", "", line)
        if len(cleaned) >= 8:
            out.append(cleaned[:16])
        if len(out) >= 6:
            break
    return out


def session_from_tree(tmux: str, cwd: str, last: str) -> tuple[str | None, str]:
    panes = []
    for line in run_cmd(
        [tmux, "list-panes", "-a", "-F", "#{session_name}\t#{pane_id}\t#{pane_pid}\t#{window_activity}"]
    ).splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            panes.append(parts)
    children: dict[str, list[tuple[str, str]]] = {}
    for line in run_cmd(["/bin/ps", "-axo", "pid=,ppid=,command="]).splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            children.setdefault(parts[1], []).append((parts[0], parts[2]))
    lsof = find_binary("lsof")
    want = os.path.realpath(cwd) if cwd else ""
    hits = []
    for name, pane_id, pane_pid, act in panes:
        stack, codex_pids = [pane_pid], []
        while stack:
            for pid, cmd in children.get(stack.pop(), []):
                stack.append(pid)
                if "codex" in cmd and "app-server" not in cmd and "hook" not in cmd:
                    codex_pids.append(pid)
        if codex_pids and want and any(proc_cwd(lsof, p) == want for p in codex_pids):
            hits.append((name, pane_id, int(act or 0)))
    if not hits:
        return None, "no_match"
    keys = snippets(last)
    names = {name for name, _, _ in hits}
    if len(names) == 1 and not keys:
        return names.pop(), "tree"
    scores: dict[str, int] = {}
    for name, pane_id, _ in hits:
        text = re.sub(r"\s", "", run_cmd([tmux, "capture-pane", "-p", "-J", "-t", pane_id, "-S", "-200"]))
        scores[name] = max(scores.get(name, 0), sum(k in text for k in keys))
    best = max(scores.values())
    top = [name for name, value in scores.items() if value == best]
    if best > 0 and len(top) == 1:
        return top[0], "tree+text"
    acts: dict[str, int] = {}
    for name, _, act in hits:
        if name in top:
            acts[name] = max(acts.get(name, 0), act)
    ranked = sorted(acts.items(), key=lambda kv: kv[1], reverse=True)
    now = int(time.time())
    if ranked and now - ranked[0][1] <= 30 and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= 10):
        return ranked[0][0], "tree+activity"
    return None, "ambiguous"


def should_debounce(key: str, window_seconds: int) -> bool:
    now = time.time()
    data: dict = {}
    if DEBOUNCE_FILE.is_file():
        try:
            data = json.loads(DEBOUNCE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    if now - float(data.get(key, 0) or 0) < window_seconds:
        return True
    data[key] = now
    if len(data) > 60:
        data = dict(sorted(data.items(), key=lambda kv: float(kv[1] or 0), reverse=True)[:30])
    try:
        DEBOUNCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEBOUNCE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return False


def load_env() -> dict[str, str]:
    path = PRIMARY_ENV_FILE if PRIMARY_ENV_FILE.is_file() else LEGACY_ENV_FILE
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def last_from_claude_transcript(path: str) -> str:
    try:
        transcript = Path(path)
        if not transcript.is_file():
            return ""
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            texts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            text = "\n".join(t for t in texts if t).strip()
            if text:
                return text
    return ""


def parse_tool(argv: list[str]) -> str:
    if "--tool" in argv:
        idx = argv.index("--tool")
        if idx + 1 < len(argv):
            return argv[idx + 1].strip().lower()
    return ""


def main(argv: list[str] | None = None) -> int:
    tool_name = parse_tool(sys.argv[1:] if argv is None else argv)
    if tool_name in JSON_STDOUT_TOOLS:
        sys.stdout.write("{}\n")
    if tool_name not in WATCHED:
        return 0

    raw = sys.stdin.read()
    try:
        ev = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        log(tool_name, "", None, "skip reason=bad_json")
        return 0
    if not isinstance(ev, dict):
        return 0

    # Grok Build 会兼容扫描 ~/.claude/settings.json，其载荷带 camelCase 的 hookEventName，
    # 在 claude 入口跳过以避免同一轮重复通知。
    if tool_name == "claude" and "hookEventName" in ev:
        return 0

    event = str(ev.get("hook_event_name") or ev.get("hookEventName") or "")
    if event not in WATCHED[tool_name]:
        return 0

    if tool_name == "grok":
        if ev.get("subagentType"):
            return 0
        if event == "Stop" and str(ev.get("reason") or "") in {"channel_closed", "shutdown"}:
            return 0

    if tool_name == "agy" and ev.get("continuationActive"):
        return 0

    cwd = str(ev.get("cwd") or "")
    last = ev.get("last_assistant_message")
    if last is None:
        last = ev.get("last-assistant-message")
    if last is None:
        last = ev.get("lastAssistantMessage")
    last = str(last or "")

    tmux = find_binary("tmux")
    if not tmux:
        log(tool_name, event, None, "skip reason=no_tmux")
        return 0

    session, how = session_from_env(tmux), "env"
    if tool_name == "codex" and not session and not os.environ.get("TMUX_PANE"):
        session, how = session_from_tree(tmux, cwd, last)
    if not session:
        log(tool_name, event, None, f"skip reason={how}")
        return 0
    if not BOT_SESSION_RE.match(session):
        log(tool_name, event, session, "skip reason=not_bot_session")
        return 0
    if tool_name == "codex" and event == "SessionEnd" and not last.strip():
        log(tool_name, event, session, "skip reason=session_end_empty")
        return 0

    if tool_name == "agy":
        session_id = str(ev.get("conversationId") or ev.get("sessionId") or "")
        turn_id = str(ev.get("executionNum") or "")
    else:
        session_id = str(ev.get("session_id") or ev.get("sessionId") or "")
        turn_id = str(ev.get("turn_id") or ev.get("promptId") or ev.get("prompt_id") or "")

    debounce_key = f"{tool_name}:{event}:{session_id or session}:{turn_id or session}"
    if should_debounce(debounce_key, DEBOUNCE_SECONDS[tool_name]):
        log(tool_name, event, session, "skip reason=debounce")
        return 0

    env = load_env()
    url, key = env.get("WEBHOOK_URL", "").strip(), env.get("WEBHOOK_KEY", "").strip()
    if not url or not key:
        log(tool_name, event, session, "skip reason=no_env")
        return 0

    if not last.strip() and tool_name == "claude" and ev.get("transcript_path"):
        last = last_from_claude_transcript(str(ev.get("transcript_path")))
    if len(last) > 800:
        last = last[:800] + "…"

    if tool_name == "agy":
        reason = ev.get("terminationReason") or ev.get("reason")
        details = ev.get("error")
        model = ev.get("modelName") or ev.get("model")
    else:
        reason = ev.get("reason") or ev.get("error") or ev.get("error_type")
        details = ev.get("errorDetails") or ev.get("reasonDetails") or ev.get("error_details")
        model = ev.get("model")

    payload = {
        "source": f"{tool_name}-hook",
        "tool": tool_name,
        "hook_event": event,
        "session": session,
        "cwd": cwd,
        "session_id": session_id,
        "turn_id": turn_id or None,
        "reason": reason,
        "error_details": (str(details)[:300] if details else None),
        "cancelled_by": ev.get("cancelledBy"),
        "last_assistant_message": last,
        "model": model,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Automation-Key": key,
            "User-Agent": "agent-toolkit-grokbot-hook/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
        log(tool_name, event, session, f"sent via={how}")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        log(tool_name, event, session, f"send_failed via={how} err={type(error).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
