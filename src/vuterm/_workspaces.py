# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Workspaces: one worktree per repository, reserved by one agent at a time.

See ``docs/specs/workspaces.md``.
"""

import contextlib
import logging
import os
import re
import shutil
import stat
import threading
from collections.abc import Sequence
from pathlib import Path

from vuterm._errors import CommandError, NoWorkspaceAvailableError, VutermError
from vuterm._git import Git

WORKSPACE_NAME = re.compile(r"ws(\d{4,})")

log = logging.getLogger(__name__)


def repository_name(repository: str) -> str:
    """The folder a repository is cloned into: ``"Org/repo"`` gives ``"repo"``.

    A repository is named with its owner, ``"Org/repo"`` or a URL: a bare
    name could be any owner's, and a clone of the wrong one would pass.
    """
    owner, _, name = repository.rstrip("/").rpartition("/")
    if not owner or not name or name in (".", ".."):
        raise ValueError(f"not a repository, wants owner/name: {repository!r}")
    return name


class WorkspacePool:
    """Creates, reserves, resets and releases workspaces.

    Reservations are tracked in memory and are thread safe. A workspace being
    created counts as reserved until it is complete, and its number is not
    given out again while it is, even if its folder is gone.
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
        # One git command at a time in each clone: two fetches fight over its
        # ref locks, and a prune can take a worktree another thread is adding.
        self._clone_locks = {r: threading.Lock() for r in self._repositories}

    def create(self) -> Path:
        """Create the next ``wsNNNN`` workspace and return its path.

        Each repository is fetched, then gets a worktree with a detached HEAD at
        ``origin/<default>``. The workspace is reserved only while it is being
        made: once returned it is free, like any workspace no agent is in, so
        a later `reserve` may take it and reset it.
        """
        with self._lock:
            workspace = self._allocate()
            self._reserved.add(workspace)
        try:
            self._populate(workspace)
        finally:
            self.release(workspace)
        return workspace

    def reserve(self) -> Path:
        """Reserve a free workspace, creating one if every workspace is reserved.

        The workspace is reset before it is returned. A free workspace that
        cannot be reset is left alone and the next one is tried, or a new one
        created.

        Raises:
            NoWorkspaceAvailableError: Every workspace is reserved or could
                not be reset, and ``max_count`` workspaces already exist.
        """
        # Once, up front: a fetch that fails is the clone's problem, the same
        # for every workspace, and is raised as such.
        for repository in self._repositories:
            self._fetch(repository)
        broken: dict[Path, Exception] = {}
        while True:
            with self._lock:
                existing = self._existing()
                free = [
                    ws
                    for ws in existing
                    if ws not in self._reserved and ws not in broken
                ]
                created = not free
                if free:
                    workspace = free[0]
                elif len(existing) < self._max_count:
                    workspace = self._allocate()
                else:
                    raise self._none_available(existing, broken)
                self._reserved.add(workspace)
            try:
                if created:
                    self._populate(workspace, fetch=False)
                else:
                    self.reset(workspace, fetch=False)
            except BaseException as error:
                self.release(workspace)
                if created or not isinstance(error, CommandError | OSError):
                    raise
                log.warning("Could not reset %s, trying another: %s", workspace, error)
                broken[workspace] = error
                continue
            return workspace

    def release(self, workspace: Path) -> None:
        """Release the reservation on `workspace`."""
        with self._lock:
            self._reserved.discard(workspace)

    def reset(self, workspace: Path, *, fetch: bool = True) -> None:
        """Restore every worktree in `workspace` to the condition of a new one.

        Fetches, unless told the clones were fetched already, then runs
        ``git reset --hard``, ``git clean -ffd`` and a detached checkout of
        ``origin/<default>``. A worktree that is missing, that is no longer a
        worktree, that was left in the middle of something, or that refuses
        to be restored is removed and added again instead. Whatever else is
        in the workspace, which agents run in, is removed: a new one holds
        the worktrees and nothing more.
        """
        for repository in self._repositories:
            repo = self._repo(repository)
            worktree = workspace / repository_name(repository)
            if fetch:
                self._fetch(repository)
            if not self._is_worktree(worktree, repo) or self._half_done(worktree):
                self._replace_worktree(repository, workspace)
                continue
            # The clone's business, not the worktree's: a failure here is
            # not something a fresh worktree would cure.
            ref = self._origin_default(repo)
            try:
                self._git.reset_hard(worktree)
                self._git.clean(worktree)
                self._git.checkout_detached(worktree, ref)
            except CommandError:
                # Whatever the agent left in there, a fresh worktree is as good.
                self._replace_worktree(repository, workspace)
        worktrees = {repository_name(r) for r in self._repositories}
        for entry in workspace.iterdir():
            if entry.name not in worktrees:
                remove(entry)

    def _none_available(
        self, existing: list[Path], broken: dict[Path, Exception]
    ) -> NoWorkspaceAvailableError:
        message = (
            f"every one of the {len(existing)} workspaces in "
            f"{self._workspaces_dir} is reserved, and max_workspace_count is "
            f"{self._max_count}"
        )
        if broken:
            names = ", ".join(ws.name for ws in broken)
            message = f"{message}; {names} could not be reset"
        error = NoWorkspaceAvailableError(message)
        error.__cause__ = next(reversed(broken.values()), None)
        return error

    def _existing(self) -> list[Path]:
        """Every workspace folder, lowest number first.

        A symlink is not a workspace, whatever its name: resetting through
        it would act on wherever it points.
        """
        return [
            path for path in self._numbered() if path.is_dir() and not path.is_symlink()
        ]

    def _numbered(self) -> list[Path]:
        """Everything named like a workspace, lowest number first."""
        if not self._workspaces_dir.is_dir():
            return []
        found = [
            path
            for path in self._workspaces_dir.iterdir()
            if WORKSPACE_NAME.fullmatch(path.name)
        ]
        return sorted(found, key=_number)

    def _allocate(self) -> Path:
        """Make the folder of the next workspace, so it counts from now on.

        Called with the lock held. A reserved workspace keeps its number even
        while its folder is gone, as during a failed creation; anything else
        named like a workspace keeps its number too.
        """
        taken = [_number(path) for path in [*self._numbered(), *self._reserved]]
        workspace = self._workspaces_dir / f"ws{max(taken, default=0) + 1:04d}"
        workspace.mkdir(parents=True)
        return workspace

    def _populate(self, workspace: Path, *, fetch: bool = True) -> None:
        """Add the worktrees to a new workspace; remove it if that fails.

        The clones keep a record of every worktree added, and a number comes
        round again once its folder is deleted, so the records of folders
        that are gone are pruned before adding, and again after a failure.
        """
        try:
            for repository in self._repositories:
                if fetch:
                    self._fetch(repository)
                self._add_worktree(repository, workspace)
        except BaseException:
            # Best effort: the error being raised is the one to report.
            with contextlib.suppress(OSError):
                remove(workspace)
            for repository in self._repositories:
                repo = self._repo(repository)
                if is_clone(self._git, repo):  # never git in a stray folder
                    with (
                        contextlib.suppress(CommandError, OSError),
                        self._clone_locks[repository],
                    ):
                        self._git.prune_worktrees(repo)
            raise

    def _is_worktree(self, worktree: Path, repo: Path) -> bool:
        """Whether `worktree` is the top of a worktree of the clone at `repo`,
        and not something else: a plain folder inside an enclosing repository,
        or a symlink to another worktree, where ``reset --hard`` and ``clean``
        would act on that repository; or a repository of its own, which no
        fetch of the clone would ever bring up to date.
        """
        if not is_clone(self._git, worktree):
            return False
        return self._git.common_dir(worktree) == (repo / ".git").resolve()

    def _half_done(self, worktree: Path) -> bool:
        """Whether a rebase or an ``am`` was left in progress: ``reset --hard``
        does not clear those, and the next agent would be in the middle of one.
        """
        paths = self._git.git_paths(
            worktree, "rebase-merge", "rebase-apply", "sequencer", "BISECT_START"
        )
        return any(path.exists() for path in paths)

    def _replace_worktree(self, repository: str, workspace: Path) -> None:
        remove(workspace / repository_name(repository))
        self._add_worktree(repository, workspace)

    def _add_worktree(self, repository: str, workspace: Path) -> None:
        """Add the worktree of `repository` to `workspace`, after pruning the
        clone's records of worktrees whose folders are gone, which would
        otherwise refuse the same path.
        """
        repo = self._repo(repository)
        ref = self._origin_default(repo)
        with self._clone_locks[repository]:
            self._git.prune_worktrees(repo)
            self._git.add_worktree(repo, workspace / repository_name(repository), ref)

    def _fetch(self, repository: str) -> None:
        """Fetch the clone of `repository`, after checking it is one: git run
        in a folder that is not a repository acts on any enclosing one.

        The first git command about a repository, so the check covers the
        others.
        """
        repo = self._repo(repository)
        problem = clone_problem(self._git, repo, repository)
        if problem is not None:
            raise VutermError(
                f"{repo} is not a clone of {repository} ({problem}): run "
                "Client.init(), after removing whatever is there"
            )
        with self._clone_locks[repository]:
            self._git.fetch(repo)

    def _repo(self, repository: str) -> Path:
        return self._repos_dir / repository_name(repository)

    def _origin_default(self, repo: Path) -> str:
        return f"origin/{self._git.default_branch(repo)}"


def is_clone(git: Git, path: Path) -> bool:
    """Whether `path` is the top of a git repository or worktree, and not a
    symlink to one or a plain folder inside one.
    """
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        top = git.toplevel(path)
    except CommandError:
        return False
    return top.resolve() == path.resolve()


def clone_problem(git: Git, path: Path, repository: str) -> str | None:
    """Why `path` is not a clone of `repository`, or None when it is one: a
    repository of its own whose ``origin`` names `repository`, however the
    URL spells it. A clone of another repository with the same name must
    not pass, or agents would work on the wrong code.
    """
    if not is_clone(git, path):
        return "not a repository"
    try:
        url = git.origin_url(path)
    except CommandError:
        return "no origin"
    if not _same_repository(url, repository):
        return f"its origin is {url}"
    return None


def _same_repository(url: str, repository: str) -> bool:
    """Whether a remote URL and a repository, ``"Org/repo"`` or a URL of its
    own, name the same repository: the same owner and name, whatever the
    host, the scheme, the case and a ``.git`` ending.
    """
    return _repository_key(url) == _repository_key(repository)


def _repository_key(text: str) -> str:
    text = text.strip().lower().rstrip("/").removesuffix(".git")
    parts = [part for part in re.split(r"[/:]", text) if part]
    return "/".join(parts[-2:])


def remove(path: Path) -> None:
    """Remove `path`, whatever an agent left there: a symlink, which is not
    followed, a file, or a folder, read-only content included.

    Raises `OSError` if something in it cannot be removed.
    """
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    # Deleting an entry needs write permission on its folder, and listing a
    # folder needs read permission on it: give the owner both, on every folder,
    # each one before it is descended into. Files are left as they are: a hard
    # link shares its mode with the file it links to.
    os.chmod(path, stat.S_IRWXU)
    for parent, folders, _files in os.walk(path):
        for name in folders:
            folder = Path(parent, name)
            if not folder.is_symlink():
                os.chmod(folder, stat.S_IRWXU)
    shutil.rmtree(path)


def _number(workspace: Path) -> int:
    match = WORKSPACE_NAME.fullmatch(workspace.name)
    assert match is not None, workspace
    return int(match.group(1))
