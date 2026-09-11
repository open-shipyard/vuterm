# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Runs terminal commands; the only place vuterm starts processes."""

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

OnLine = Callable[[str], None]


@dataclass(frozen=True)
class CompletedCommand:
    """A command that ran to completion."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(ABC):
    """Runs non-interactive commands: nothing is written to their stdin.

    Inject a fake implementation to test without running ``git``, ``gh`` or
    agent CLIs.
    """

    @abstractmethod
    def run(self, args: Sequence[str], *, cwd: Path) -> CompletedCommand:
        """Run `args` in `cwd` and wait for it to exit, capturing its output.

        For short commands such as ``git`` and ``gh``. A non-zero exit status is
        returned, not raised.
        """

    @abstractmethod
    def stream(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        on_stdout: OnLine,
        on_stderr: OnLine,
    ) -> int:
        """Run `args` in `cwd`, passing each output line on as soon as it is written.

        For long-running agents. Returns the exit status; a non-zero one is not
        raised.
        """


class SubprocessRunner(CommandRunner):
    """Runs commands with `subprocess`."""

    def run(self, args: Sequence[str], *, cwd: Path) -> CompletedCommand:
        raise NotImplementedError

    def stream(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        on_stdout: OnLine,
        on_stderr: OnLine,
    ) -> int:
        raise NotImplementedError
