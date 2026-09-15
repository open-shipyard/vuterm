# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Synchronous client."""

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from vuterm._git import Git
from vuterm._github import GitHub
from vuterm._results import AgentResult
from vuterm._runner import CommandRunner, SubprocessRunner
from vuterm._workspaces import WorkspacePool
from vuterm.harnesses import Harness
from vuterm.harnesses.claude import ClaudeHarness
from vuterm.harnesses.opencode import OpenCodeHarness

DEFAULT_MAX_WORKSPACE_COUNT = 10


class Client:
    """Runs code agents, optionally in workspaces spanning several repositories.

    The client is synchronous and single-threaded. Run agents concurrently by
    calling one client from several threads: workspace reservations are thread
    safe.

    Args:
        working_dir: Root folder. Repositories are cloned into ``repos`` and
            workspaces are created in ``workspaces``.
        repositories: GitHub repositories as ``"Org/repo"`` or ``"User/repo"``.
        max_workspace_count: Most workspaces `launch_agent_in_workspace` creates.
        runner: Runs every ``git``, ``gh`` and agent command.
        git: ``git`` wrapper; defaults to one using `runner`.
        github: ``gh`` wrapper; defaults to one using `runner`.
        harnesses: Agent CLIs available by name; defaults to Claude Code and
            OpenCode.
    """

    def __init__(
        self,
        *,
        working_dir: str | os.PathLike[str],
        repositories: Sequence[str],
        max_workspace_count: int = DEFAULT_MAX_WORKSPACE_COUNT,
        runner: CommandRunner | None = None,
        git: Git | None = None,
        github: GitHub | None = None,
        harnesses: Iterable[Harness] | None = None,
    ) -> None:
        root = Path(working_dir)
        self._runner = runner or SubprocessRunner()
        self.git = git or Git(self._runner)
        self.github = github or GitHub(self._runner)
        default_harnesses = [ClaudeHarness(), OpenCodeHarness()]
        self._harnesses = {h.name: h for h in (harnesses or default_harnesses)}
        self._repositories = list(repositories)
        self._repos_dir = root / "repos"
        self._workspaces = WorkspacePool(
            workspaces_dir=root / "workspaces",
            repos_dir=self._repos_dir,
            repositories=self._repositories,
            max_count=max_workspace_count,
            git=self.git,
        )

    def init(self) -> None:
        """Clone every repository into ``working_dir/repos``."""
        raise NotImplementedError

    def prepare_new_workspace(self) -> Path:
        """Create the next ``wsNNNN`` workspace and return its path."""
        raise NotImplementedError

    def launch_agent(self, harness: str, task: str) -> AgentResult:
        """Run the `harness` agent on `task` in the current folder until it exits.

        Raises:
            ValueError: No harness is named `harness`.
        """
        raise NotImplementedError

    def launch_agent_in_workspace(self, harness: str, task: str) -> AgentResult:
        """Run the `harness` agent on `task` in a reserved workspace until it exits.

        The workspace is reset to the condition of a new one before the agent
        starts, and its reservation is released when the agent exits.

        Raises:
            ValueError: No harness is named `harness`.
            NoWorkspaceAvailableError: Every workspace is reserved and
                ``max_workspace_count`` workspaces already exist.
        """
        raise NotImplementedError
