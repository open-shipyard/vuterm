# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Synchronous client."""

import dataclasses
import logging
import math
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from vuterm._errors import CommandTimeoutError, VutermError
from vuterm._git import Git
from vuterm._github import GitHub
from vuterm._results import AgentResult
from vuterm._runner import CommandRunner, SubprocessRunner
from vuterm._workspaces import WorkspacePool, clone_problem, repository_name
from vuterm.harnesses import Harness
from vuterm.harnesses.claude import ClaudeHarness
from vuterm.harnesses.opencode import OpenCodeHarness

DEFAULT_MAX_WORKSPACE_COUNT = 10
# Seconds an agent may run unless told otherwise: long enough for real work,
# short enough that a hung agent does not hold its workspace for good.
DEFAULT_TIMEOUT = 3 * 60 * 60

# What agents write, as their sessions describe it: one INFO record per
# message, and one WARNING record per line of stderr.
agent_log = logging.getLogger("vuterm.agent")


class Client:
    """Runs code agents, optionally in workspaces spanning several repositories.

    The client is synchronous and single-threaded. Run agents concurrently by
    calling one client from several threads: workspace reservations are thread
    safe.

    Args:
        working_dir: Root folder. Repositories are cloned into ``repos`` and
            workspaces are created in ``workspaces``.
        repositories: GitHub repositories as ``"Org/repo"`` or ``"User/repo"``.
            Two repositories may not share a name.
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
        # Absolute, so git and gh, which run in other folders, find it.
        root = Path(working_dir).resolve()
        self._runner = runner or SubprocessRunner()
        self.git = git or Git(self._runner)
        self.github = github or GitHub(self._runner)
        default_harnesses = [ClaudeHarness(), OpenCodeHarness()]
        self._harnesses = {h.name: h for h in (harnesses or default_harnesses)}
        self._repositories = list(repositories)
        names = [repository_name(r) for r in self._repositories]
        if len(set(names)) != len(names):
            raise ValueError(f"two repositories share a name: {self._repositories}")
        self._repos_dir = root / "repos"
        self._workspaces = WorkspacePool(
            workspaces_dir=root / "workspaces",
            repos_dir=self._repos_dir,
            repositories=self._repositories,
            max_count=max_workspace_count,
            git=self.git,
        )

    def init(self) -> None:
        """Clone every repository into ``working_dir/repos``.

        A clone already there is fetched instead; an empty folder in its
        place, as an interrupted clone leaves, is cloned into.

        Raises:
            VutermError: Something else is in a clone's place.
        """
        self._repos_dir.mkdir(parents=True, exist_ok=True)
        for repository in self._repositories:
            repo = self._repos_dir / repository_name(repository)
            problem = clone_problem(self.git, repo, repository)
            if problem is None:
                self.git.fetch(repo)
                continue
            if repo.is_dir() and not repo.is_symlink() and not any(repo.iterdir()):
                repo.rmdir()
            if repo.exists() or repo.is_symlink():
                raise VutermError(
                    f"{repo} is not a clone of {repository} ({problem}): "
                    "remove it first"
                )
            self.github.clone(repository, repo)

    def prepare_new_workspace(self) -> Path:
        """Create the next ``wsNNNN`` workspace and return its path.

        The workspace is free once returned: like any workspace no agent is
        running in, a later `launch_agent_in_workspace` may take it and reset
        it, discarding whatever was done there. Work in it before that, or
        not at all while agents are being launched in workspaces.
        """
        return self._workspaces.create()

    def launch_agent(
        self, harness: str, task: str, *, timeout: float | None = DEFAULT_TIMEOUT
    ) -> AgentResult:
        """Run the `harness` agent on `task` in the current folder until it exits.

        Args:
            timeout: Seconds the agent may run. One still running then is
                killed with its process group, and its result has
                ``timed_out`` set. Three hours by default; None lets it run
                for as long as it takes.

        Raises:
            ValueError: No harness is named `harness`, or `timeout` is not a
                positive number.
        """
        selected = self._harness(harness)
        _check_timeout(timeout)
        return self._run(selected, task, cwd=Path.cwd(), timeout=timeout)

    def launch_agent_in_workspace(
        self, harness: str, task: str, *, timeout: float | None = DEFAULT_TIMEOUT
    ) -> AgentResult:
        """Run the `harness` agent on `task` in a reserved workspace until it exits.

        The workspace is reset to the condition of a new one before the agent
        starts, and its reservation is released when the agent exits.

        Args:
            timeout: Seconds the agent may run, as for `launch_agent`. The
                time taken to reserve and reset the workspace does not count.

        Raises:
            ValueError: No harness is named `harness`, or `timeout` is not a
                positive number.
            NoWorkspaceAvailableError: Every workspace is reserved and
                ``max_workspace_count`` workspaces already exist.
        """
        selected = self._harness(harness)
        _check_timeout(timeout)
        workspace = self._workspaces.reserve()
        try:
            return self._run(selected, task, cwd=workspace, timeout=timeout)
        finally:
            self._workspaces.release(workspace)

    def _harness(self, name: str) -> Harness:
        try:
            return self._harnesses[name]
        except KeyError:
            available = ", ".join(sorted(self._harnesses)) or "none"
            raise ValueError(
                f"no harness is named {name!r}; available: {available}"
            ) from None

    def _run(
        self, harness: Harness, task: str, *, cwd: Path, timeout: float | None
    ) -> AgentResult:
        session = harness.start(task)

        def on_stdout(line: str) -> None:
            message = session.feed(line)
            if message:
                agent_log.info("%s", message)

        def on_stderr(line: str) -> None:
            agent_log.warning("%s", line)

        agent_log.info("Running %s in %s", harness.name, cwd)
        try:
            returncode = self._runner.stream(
                session.command,
                cwd=cwd,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
                timeout=timeout,
            )
        except CommandTimeoutError as error:
            agent_log.warning("Agent stopped after its timeout of %g s", error.timeout)
            # Whatever the session read, such as a response, still stands.
            result = session.finish(error.returncode)
            return dataclasses.replace(result, success=False, timed_out=True)
        return session.finish(returncode)


def _check_timeout(timeout: float | None) -> None:
    if timeout is not None and not (timeout > 0 and math.isfinite(timeout)):
        raise ValueError(f"timeout must be a positive number of seconds: {timeout!r}")
