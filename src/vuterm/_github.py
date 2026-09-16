# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Wrapper around the ``gh`` CLI.

Every method raises `CommandError` if ``gh`` exits with a non-zero status.
"""

from pathlib import Path

from vuterm._errors import CommandError
from vuterm._runner import CommandRunner, CompletedCommand


class GitHub:
    """Runs ``gh`` commands, authenticated as the ``gh`` user."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def clone(self, repository: str, destination: Path) -> None:
        """Clone `repository`, e.g. ``"Org/repo"``, into `destination`.

        The parent of `destination` must exist.
        """
        destination = destination.absolute()
        self._gh(destination.parent, "repo", "clone", repository, str(destination))

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
        args = ["pr", "create", "--title", title, "--body", body]
        if base is not None:
            args += ["--base", base]
        # gh prints the URL of the new pull request as the last line of stdout.
        completed = self._gh(path, *args)
        lines = completed.stdout.strip().splitlines()
        if not lines:
            raise CommandError(
                f"gh pr create in {path} printed no pull request URL",
                returncode=completed.returncode,
                stderr=completed.stderr,
            )
        return lines[-1]

    def _gh(self, cwd: Path, *args: str) -> CompletedCommand:
        completed = self._runner.run(["gh", *args], cwd=cwd)
        if completed.returncode != 0:
            raise CommandError(
                f"gh {' '.join(args[:2])} failed in {cwd} "
                f"with status {completed.returncode}: {completed.stderr.strip()}",
                returncode=completed.returncode,
                stderr=completed.stderr,
            )
        return completed
