# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""The port every agent CLI implements."""

from abc import ABC, abstractmethod
from typing import ClassVar

from vuterm._results import AgentResult


class AgentSession(ABC):
    """Translates one agent run between vuterm and a vendor's agent CLI.

    Sessions keep state across a run but do no I/O: `Client` runs `command`
    through its `CommandRunner` and feeds the output to the session. Agents are
    non-interactive, so nothing flows back to the agent once it is launched.
    """

    @property
    @abstractmethod
    def command(self) -> list[str]:
        """Command line that runs the agent headless on the task."""

    @abstractmethod
    def feed(self, line: str) -> str | None:
        """Return the log message for one stdout line, or None to skip it."""

    @abstractmethod
    def finish(self, returncode: int) -> AgentResult:
        """Read whether the agent completed successfully or errored, and its
        response, from the exit status and the lines fed so far.
        """


class Harness(ABC):
    """Creates sessions for one vendor's agent CLI."""

    name: ClassVar[str]
    """Name passed to `Client.launch_agent`, e.g. ``"claude"``."""

    @abstractmethod
    def start(self, task: str) -> AgentSession:
        """Create the session for one run of the agent on `task`."""
