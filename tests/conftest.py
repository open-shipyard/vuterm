# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""A runner that records commands instead of running them."""

import contextlib
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from vuterm import CommandRunner, CommandTimeoutError, CompletedCommand
from vuterm._runner import OnLine

Call = tuple[tuple[str, ...], Path]
Output = str | Callable[[tuple[str, ...], Path], str]


def rev_parse(command: tuple[str, ...], cwd: Path) -> str:
    """What git would answer in a clone at ``<root>/repos/<name>`` or in its
    worktree at ``<root>/workspaces/wsNNNN/<name>``: the folder is the top
    of a repository, its common dir is the clone's, and nothing is in
    progress.
    """
    if "--git-common-dir" in command:
        if cwd.parent.parent.name == "workspaces":
            return f"{cwd.parents[2] / 'repos' / cwd.name / '.git'}\n"
        return f"{cwd / '.git'}\n"
    if "--git-path" in command:
        names = [word for word in command[2:] if word != "--git-path"]
        return "".join(f"{cwd / '.git' / name}\n" for name in names)
    return f"{cwd}\n"


def remote(command: tuple[str, ...], cwd: Path) -> str:
    """``git remote get-url origin`` in a clone named after its repository,
    under the owner ``Org``; ``set-head`` says nothing.
    """
    if command[2] == "get-url":
        return f"https://github.com/Org/{cwd.name}.git\n"
    return ""


class FakeRunner(CommandRunner):
    """Records every command; answers `run` from outputs keyed by the command's
    first two words and failures keyed by any leading words, and `stream` with
    fixed lines, then its timeout if told to run past it.

    Like git and gh, it makes the folder of a worktree it is asked to add or
    a repository it is asked to clone, and ``rev-parse --show-toplevel``
    answers with the folder it runs in, so any folder passes for a clone.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.streamed: list[Call] = []
        self.outputs: dict[str, Output] = {
            "git symbolic-ref": "refs/remotes/origin/main\n",
            "git rev-parse": rev_parse,
            "git remote": remote,
            "gh pr": "https://github.com/org/repo/pull/7\n",
        }
        self.failures: dict[str, str] = {}
        self.before_run: Callable[[tuple[str, ...], Path], None] | None = None
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self.returncode = 0
        self.runs_past_timeout = False
        self.timeouts: list[float | None] = []
        self.while_streaming: Callable[[], None] | None = None

    def run(self, args: Sequence[str], *, cwd: Path) -> CompletedCommand:
        command = tuple(args)
        self.calls.append((command, cwd))
        if self.before_run is not None:
            self.before_run(command, cwd)
        for prefix, stderr in self.failures.items():
            words = tuple(prefix.split())
            if command[: len(words)] == words:
                return CompletedCommand(
                    args=command, returncode=128, stdout="", stderr=stderr
                )
        key = " ".join(command[:2])
        if command[:3] in (("git", "worktree", "add"), ("gh", "repo", "clone")):
            # Under a real folder only; tests of Git alone use made-up paths.
            with contextlib.suppress(OSError):
                Path(command[-1 if command[0] == "gh" else -2]).mkdir(
                    parents=True, exist_ok=True
                )
        output = self.outputs.get(key, "")
        stdout = output if isinstance(output, str) else output(command, cwd)
        return CompletedCommand(args=command, returncode=0, stdout=stdout, stderr="")

    def stream(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        on_stdout: OnLine,
        on_stderr: OnLine,
        timeout: float | None = None,
    ) -> int:
        self.streamed.append((tuple(args), cwd))
        self.timeouts.append(timeout)
        if self.while_streaming is not None:
            self.while_streaming()
        for line in self.stdout_lines:
            on_stdout(line)
        for line in self.stderr_lines:
            on_stderr(line)
        if self.runs_past_timeout and timeout is not None:
            raise CommandTimeoutError("timed out", timeout=timeout, returncode=-9)
        return self.returncode

    def commands(self, *first_words: str) -> list[Call]:
        """The recorded calls starting with `first_words`."""
        return [
            call for call in self.calls if call[0][: len(first_words)] == first_words
        ]


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()
