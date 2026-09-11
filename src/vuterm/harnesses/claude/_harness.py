# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code, run headless with ``claude -p``."""

from vuterm._results import AgentResult
from vuterm.harnesses._base import AgentSession, Harness


class ClaudeSession(AgentSession):
    def __init__(self, task: str) -> None:
        self._task = task

    @property
    def command(self) -> list[str]:
        raise NotImplementedError

    def feed(self, line: str) -> str | None:
        raise NotImplementedError

    def finish(self, returncode: int) -> AgentResult:
        raise NotImplementedError


class ClaudeHarness(Harness):
    name = "claude"

    def start(self, task: str) -> AgentSession:
        raise NotImplementedError
