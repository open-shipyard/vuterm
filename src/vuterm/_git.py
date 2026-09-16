# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Wrapper around the ``git`` CLI.

Every method raises `CommandError` if ``git`` exits with a non-zero status.
"""

import re
from pathlib import Path

from vuterm._errors import CommandError
from vuterm._runner import CommandRunner, CompletedCommand

ORIGIN_HEAD = "refs/remotes/origin/HEAD"
ORIGIN_PREFIX = "refs/remotes/origin/"


BRANCH_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*")


def _branch(name: str) -> str:
    """`name`, if it is plainly a branch name: letters, digits, ``.``, ``_``
    and ``-``, in components separated by ``/``, none starting with ``.``,
    ``-`` or ``+``, and no ``..`` or ``.lock`` ending.

    Anything else could mean something to git where the name goes: an
    option after the positional arguments, such as ``--receive-pack=<cmd>``;
    a refspec's ``+`` forcing or ``:`` separating; a ``*`` pattern taking in
    every matching branch; or a revision expression with ``~``, ``^``,
    ``..`` or ``@{``.
    """
    if (
        not BRANCH_NAME.fullmatch(name)
        or ".." in name
        or any(part.endswith(".lock") for part in name.split("/"))
    ):
        raise ValueError(f"not a branch name: {name!r}")
    return name


class Git:
    """Runs ``git`` commands in a clone or worktree."""

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def fetch(self, repo: Path) -> None:
        """Fetch ``origin`` into the clone at `repo`, and follow its default
        branch, which git sets when cloning and never moves on its own.
        """
        self._git(repo, "fetch", "origin")
        self._git(repo, "remote", "set-head", "origin", "--auto")

    def origin_url(self, repo: Path) -> str:
        """The URL of ``origin`` in the clone at `repo`."""
        return self._git(repo, "remote", "get-url", "origin").stdout.strip()

    def default_branch(self, repo: Path) -> str:
        """Return the branch ``origin/HEAD`` points to, e.g. ``"main"``."""
        ref = self._git(repo, "symbolic-ref", ORIGIN_HEAD).stdout.strip()
        if not ref.startswith(ORIGIN_PREFIX):
            raise CommandError(
                f"{ORIGIN_HEAD} in {repo} points to {ref!r}, not a branch of origin",
                returncode=0,
                stderr="",
            )
        return ref.removeprefix(ORIGIN_PREFIX)

    def add_worktree(self, repo: Path, path: Path, ref: str) -> None:
        """Add a worktree of `repo` at `path` with a detached HEAD at `ref`."""
        # git runs in `repo`, so `path` must not be relative to here.
        self._git(repo, "worktree", "add", "--detach", str(path.absolute()), ref)

    def prune_worktrees(self, repo: Path) -> None:
        """Forget the worktrees of `repo` whose folders are gone."""
        self._git(repo, "worktree", "prune")

    def common_dir(self, path: Path) -> Path:
        """The ``.git`` folder of the repository `path` belongs to: a clone's
        own, or the clone's for one of its worktrees.
        """
        out = self._git(path, "rev-parse", "--git-common-dir").stdout.strip()
        return (path / out).resolve()

    def git_paths(self, path: Path, *names: str) -> list[Path]:
        """Where git keeps `names` for the worktree at `path`, such as the
        state of a rebase in progress, whether or not they exist now.
        """
        args = [arg for name in names for arg in ("--git-path", name)]
        lines = self._git(path, "rev-parse", *args).stdout.splitlines()
        return [(path / line.strip()).resolve() for line in lines]

    def toplevel(self, path: Path) -> Path:
        """The root of the worktree `path` is in."""
        return Path(self._git(path, "rev-parse", "--show-toplevel").stdout.strip())

    def checkout_detached(self, path: Path, ref: str) -> None:
        """Check out `ref` with a detached HEAD."""
        self._git(path, "checkout", "--detach", ref)

    def reset_hard(self, path: Path) -> None:
        """Discard changes to tracked files with ``git reset --hard``."""
        self._git(path, "reset", "--hard")

    def clean(self, path: Path) -> None:
        """Remove untracked files and directories, keeping ignored ones.

        The second ``-f`` takes untracked nested repositories too.
        """
        self._git(path, "clean", "-ffd")

    def create_branch(self, path: Path, branch: str) -> None:
        """Create a local `branch` at HEAD and switch to it."""
        self._git(path, "switch", "--create", _branch(branch))

    def push(self, path: Path, branch: str) -> None:
        """Push `branch` to ``origin``, setting it as the upstream."""
        # A full refspec: a bare name could be read as one, "+" forcing
        # and ":" deleting.
        refspec = f"refs/heads/{_branch(branch)}:refs/heads/{_branch(branch)}"
        self._git(path, "push", "--set-upstream", "origin", refspec)

    def delete_branch(self, path: Path, branch: str) -> None:
        """Delete the local `branch`, merged or not."""
        self._git(path, "branch", "--delete", "--force", _branch(branch))

    def _git(self, cwd: Path, *args: str) -> CompletedCommand:
        """Run git in `cwd`; `CommandError` if it fails."""
        completed = self._runner.run(["git", *args], cwd=cwd)
        if completed.returncode != 0:
            raise CommandError(
                f"git {' '.join(args)} failed in {cwd} "
                f"with status {completed.returncode}: {completed.stderr.strip()}",
                returncode=completed.returncode,
                stderr=completed.stderr,
            )
        return completed
