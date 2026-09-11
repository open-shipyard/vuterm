# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Wrapper around the ``gh`` CLI.

Every method raises `CommandError` if ``gh`` exits with a non-zero status.
"""

from pathlib import Path

from vuterm._runner import CommandRunner


class GitHub:
    """Runs ``gh`` commands, authenticated as the ``gh`` user."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def clone(self, repository: str, destination: Path) -> None:
        """Clone `repository`, e.g. ``"Org/repo"``, into `destination`."""
        raise NotImplementedError

    def create_pull_request(
        self,
        path: Path,
        *,
        title: str,
        body: str,
        base: str | None = None,
    ) -> str:
        """Open a pull request for the branch checked out at `path`.

        Args:
            base: Branch to merge into; defaults to the repository's default branch.

        Returns:
            The pull request URL.
        """
        raise NotImplementedError
