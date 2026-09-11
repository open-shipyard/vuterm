# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Workspaces: one worktree per repository, reserved by one agent at a time.

See ``docs/specs/workspaces.md``.
"""

import threading
from collections.abc import Sequence
from pathlib import Path

from vuterm._git import Git


class WorkspacePool:
    """Creates, reserves, resets and releases workspaces.

    Reservations are tracked in memory and are thread safe.
    """

    def __init__(
        self,
        *,
        workspaces_dir: Path,
        repos_dir: Path,
        repositories: Sequence[str],
        max_count: int,
        git: Git,
    ) -> None:
        self._workspaces_dir = workspaces_dir
        self._repos_dir = repos_dir
        self._repositories = list(repositories)
        self._max_count = max_count
        self._git = git
        self._lock = threading.Lock()
        self._reserved: set[Path] = set()

    def create(self) -> Path:
        """Create the next ``wsNNNN`` workspace and return its path.

        Each repository is fetched, then gets a worktree with a detached HEAD at
        ``origin/<default>``.
        """
        raise NotImplementedError

    def reserve(self) -> Path:
        """Reserve a free workspace, creating one if every workspace is reserved.

        The workspace is reset before it is returned.

        Raises:
            NoWorkspaceAvailableError: Every workspace is reserved and
                ``max_count`` workspaces already exist.
        """
        raise NotImplementedError

    def release(self, workspace: Path) -> None:
        """Release the reservation on `workspace`."""
        raise NotImplementedError

    def reset(self, workspace: Path) -> None:
        """Restore every worktree in `workspace` to the condition of a new one.

        Fetches, then runs ``git reset --hard``, ``git clean -fd`` and a
        detached checkout of ``origin/<default>``.
        """
        raise NotImplementedError
