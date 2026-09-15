# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""OpenCode, run headless with ``opencode run``."""

import json
from typing import Any

from vuterm._results import AgentResult
from vuterm.harnesses._base import AgentSession, Harness
from vuterm.harnesses._text import truncate


class OpenCodeHarness(Harness):
    """OpenCode, run with ``opencode run``.

    Args:
        auto_approve: Pass ``--auto``, approving every permission that is not
            explicitly denied, which suits agents running in a sandbox.
    """

    name = "opencode"

    def __init__(self, *, auto_approve: bool = True) -> None:
        self.auto_approve = auto_approve

    def start(self, task: str) -> AgentSession:
        return OpenCodeSession(task, auto_approve=self.auto_approve)


class OpenCodeSession(AgentSession):
    """Reads the ``--format json`` events of one ``opencode run``."""

    def __init__(self, task: str, *, auto_approve: bool) -> None:
        self._command = ["opencode", "run", "--format", "json"]
        if auto_approve:
            self._command.append("--auto")
        # Ends the options, so a task starting with "-" is not read as one.
        self._command += ["--", task]
        self._session_id: str | None = None
        self._errored = False

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
        # OpenCode sends no final result event, only an error event when it fails.
        return AgentResult(success=returncode == 0 and not self._errored)

    def _describe(self, event: Any) -> str | None:
        match event["type"]:
            case "step_start" if self._session_id is None:
                self._session_id = str(event["sessionID"])
                return f"Session {self._session_id} started"
            case "text":
                return str(event["part"]["text"]) or None
            case "tool_use":
                return _describe_tool(event["part"])
            case "error":
                self._errored = True
                error = event["error"]
                return f"Agent errored: {error['name']}: {error['data']['message']}"
        return None


def _describe_tool(part: Any) -> str:
    state = part["state"]
    call = f"Tool call {part['tool']}: {truncate(json.dumps(state['input']))}"
    match state["status"]:
        case "completed":
            return f"{call}\nTool result: {truncate(str(state['output']).rstrip())}"
        case "error":
            return f"{call}\nTool error: {truncate(str(state['error']))}"
    return call
