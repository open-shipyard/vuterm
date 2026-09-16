# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code, run headless with ``claude -p``."""

import json
from collections.abc import Iterable
from typing import Any

from vuterm._results import AgentResult
from vuterm.harnesses._base import AgentSession, Harness
from vuterm.harnesses._text import truncate


class ClaudeHarness(Harness):
    """Claude Code, run with ``claude -p``.

    Args:
        permission_mode: Passed to ``--permission-mode``. The default skips every
            permission check, which suits agents running in a sandbox.
    """

    name = "claude"

    def __init__(self, *, permission_mode: str = "bypassPermissions") -> None:
        self.permission_mode = permission_mode

    def start(self, task: str) -> AgentSession:
        return ClaudeSession(task, permission_mode=self.permission_mode)


class ClaudeSession(AgentSession):
    """Reads the ``stream-json`` events of one ``claude -p`` run."""

    def __init__(self, task: str, *, permission_mode: str) -> None:
        self._command = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            # Print mode rejects stream-json output without --verbose.
            "--verbose",
            "--permission-mode",
            permission_mode,
            # Ends the options, so a task starting with "-" is not read as one.
            "--",
            task,
        ]
        self._is_error: bool | None = None
        self._response: str | None = None

    @property
    def command(self) -> list[str]:
        return list(self._command)

    def feed(self, line: str) -> str | None:
        if not line.strip():
            return None
        try:
            return self._describe(json.loads(line))
        except (ValueError, LookupError, TypeError, AttributeError):
            # Not an event this session understands: log the line as written.
            return line

    def finish(self, returncode: int) -> AgentResult:
        # A failed run can still report subtype "success"; only is_error is reliable.
        return AgentResult(
            success=returncode == 0 and self._is_error is False,
            response=self._response,
        )

    def _describe(self, event: Any) -> str | None:
        match event["type"]:
            case "system" if event["subtype"] == "init":
                return (
                    f"Session {event['session_id']} started with model {event['model']}"
                )
            case "assistant" | "user":
                return _join(_describe_block(b) for b in event["message"]["content"])
            case "result":
                # The final message; runs cut short, as by max turns, have none.
                response = event.get("result")
                self._response = response if isinstance(response, str) else None
                self._is_error = bool(event["is_error"])
                turns = event["num_turns"]
                if self._is_error:
                    return f"Agent errored (turns: {turns}): {event['result']}"
                return f"Agent completed (turns: {turns})"
        return None


def _describe_block(block: Any) -> str | None:
    match block["type"]:
        case "text":
            return str(block["text"])
        case "tool_use":
            tool_input = truncate(json.dumps(block["input"]))
            return f"Tool call {block['name']}: {tool_input}"
        case "tool_result":
            label = "Tool error" if block.get("is_error") else "Tool result"
            return f"{label}: {truncate(_text(block.get('content')))}"
    return None


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content)
    return str(content)


def _join(parts: Iterable[str | None]) -> str | None:
    return "\n".join(part for part in parts if part) or None
