# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Wrapper around the ``git`` CLI.

Every method raises `CommandError` if ``git`` exits with a non-zero status.
"""

from pathlib import Path

from vuterm._runner import CommandRunner


class Git:
    """Runs ``git`` commands in a clone or worktree."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def fetch(self, repo: Path) -> None:
        """Fetch ``origin`` into the clone at `repo`."""
        raise NotImplementedError

    def default_branch(self, repo: Path) -> str:
        """Return the branch ``origin/HEAD`` points to, e.g. ``"main"``."""
        raise NotImplementedError

    def add_worktree(self, repo: Path, path: Path, ref: str) -> None:
        """Add a worktree of `repo` at `path` with a detached HEAD at `ref`."""
        raise NotImplementedError

    def checkout_detached(self, path: Path, ref: str) -> None:
        """Check out `ref` with a detached HEAD."""
        raise NotImplementedError

    def reset_hard(self, path: Path) -> None:
        """Discard changes to tracked files with ``git reset --hard``."""
        raise NotImplementedError

    def clean(self, path: Path) -> None:
        """Remove untracked files and directories, keeping ignored ones."""
        raise NotImplementedError

    def create_branch(self, path: Path, branch: str) -> None:
        """Create a local `branch` at HEAD and switch to it."""
        raise NotImplementedError

    def push(self, path: Path, branch: str) -> None:
        """Push `branch` to ``origin``."""
        raise NotImplementedError

    def delete_branch(self, path: Path, branch: str) -> None:
        """Delete the local `branch`."""
        raise NotImplementedError
