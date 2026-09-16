# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Workspaces: numbering, reservation, reset, and the real git commands."""

import logging
import os
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import FakeRunner, rev_parse

from vuterm import (
    CommandError,
    Git,
    NoWorkspaceAvailableError,
    SubprocessRunner,
    VutermError,
)
from vuterm._workspaces import (
    WorkspacePool,
    _same_repository,
    remove,
    repository_name,
)


def pool(root: Path, runner: FakeRunner, *, max_count: int = 10) -> WorkspacePool:
    for name in ("one", "two"):  # the clones, as far as the fake can tell
        (root / "repos" / name).mkdir(parents=True, exist_ok=True)
    return WorkspacePool(
        workspaces_dir=root / "workspaces",
        repos_dir=root / "repos",
        repositories=["Org/one", "Org/two"],
        max_count=max_count,
        git=Git(runner),
    )


def test_repository_name_is_the_last_segment() -> None:
    assert repository_name("Org/repo") == "repo"
    assert repository_name("https://github.com/Org/repo") == "repo"
    for bad in ("repo", "Org/", "/repo", "Org/.."):
        with pytest.raises(ValueError, match="wants owner/name"):
            repository_name(bad)


def test_creates_the_first_workspace_with_a_worktree_per_repository(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspace = pool(tmp_path, runner).create()

    assert workspace == tmp_path / "workspaces" / "ws0001"
    assert workspace.is_dir()
    repos = tmp_path / "repos"
    assert runner.calls == [
        (("git", "rev-parse", "--show-toplevel"), repos / "one"),
        (("git", "remote", "get-url", "origin"), repos / "one"),
        (("git", "fetch", "origin"), repos / "one"),
        (("git", "remote", "set-head", "origin", "--auto"), repos / "one"),
        (("git", "symbolic-ref", "refs/remotes/origin/HEAD"), repos / "one"),
        (("git", "worktree", "prune"), repos / "one"),
        (
            (
                "git",
                "worktree",
                "add",
                "--detach",
                str(workspace / "one"),
                "origin/main",
            ),
            repos / "one",
        ),
        (("git", "rev-parse", "--show-toplevel"), repos / "two"),
        (("git", "remote", "get-url", "origin"), repos / "two"),
        (("git", "fetch", "origin"), repos / "two"),
        (("git", "remote", "set-head", "origin", "--auto"), repos / "two"),
        (("git", "symbolic-ref", "refs/remotes/origin/HEAD"), repos / "two"),
        (("git", "worktree", "prune"), repos / "two"),
        (
            (
                "git",
                "worktree",
                "add",
                "--detach",
                str(workspace / "two"),
                "origin/main",
            ),
            repos / "two",
        ),
    ]


def test_numbers_after_the_highest_existing_workspace(
    tmp_path: Path, runner: FakeRunner
) -> None:
    for name in ("ws0001", "ws0003", "ws12345", "notes", "ws1"):
        (tmp_path / "workspaces" / name).mkdir(parents=True)
    (tmp_path / "workspaces" / "ws0007").write_text("a file, not a workspace")

    assert pool(tmp_path, runner).create().name == "ws12346"


def test_removes_and_prunes_a_workspace_it_could_not_populate(
    tmp_path: Path, runner: FakeRunner
) -> None:
    added = 0

    def fail_the_second_worktree(command: tuple[str, ...], cwd: Path) -> None:
        nonlocal added
        if command[:3] == ("git", "worktree", "add"):
            added += 1
            if added == 2:
                runner.failures["git worktree add"] = "fatal: not a commit"

    runner.before_run = fail_the_second_worktree

    with pytest.raises(CommandError, match="git worktree add"):
        pool(tmp_path, runner).create()

    assert not (tmp_path / "workspaces" / "ws0001").exists()
    pruned = [cwd for _, cwd in runner.commands("git", "worktree", "prune")]
    assert pruned[-2:] == [tmp_path / "repos" / "one", tmp_path / "repos" / "two"]


def test_a_workspace_being_created_is_reserved(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)
    taken: list[Path] = []
    reserving = False

    def reserve_meanwhile(command: tuple[str, ...], cwd: Path) -> None:
        nonlocal reserving
        # On a command outside the clone's lock, as another thread would be.
        if command[:2] == ("git", "symbolic-ref") and not reserving:
            reserving = True
            taken.append(workspaces.reserve())

    runner.before_run = reserve_meanwhile

    created = workspaces.create()

    assert created == tmp_path / "workspaces" / "ws0001"
    assert taken == [tmp_path / "workspaces" / "ws0002"]
    runner.before_run = None
    assert workspaces.reserve() == created, "released once complete"


def test_reserves_free_workspaces_before_creating_more(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)

    first = workspaces.reserve()
    second = workspaces.reserve()
    workspaces.release(first)
    third = workspaces.reserve()

    assert (first.name, second.name, third.name) == ("ws0001", "ws0002", "ws0001")


def test_resets_a_reused_workspace(tmp_path: Path, runner: FakeRunner) -> None:
    workspaces = pool(tmp_path, runner)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    runner.calls.clear()

    assert workspaces.reserve() == workspace
    repo, tree = tmp_path / "repos" / "one", workspace / "one"
    other = tmp_path / "repos" / "two"
    in_progress = [
        arg
        for name in ("rebase-merge", "rebase-apply", "sequencer", "BISECT_START")
        for arg in ("--git-path", name)
    ]
    assert runner.calls[:15] == [
        # Every clone once, before any workspace is touched.
        (("git", "rev-parse", "--show-toplevel"), repo),
        (("git", "remote", "get-url", "origin"), repo),
        (("git", "fetch", "origin"), repo),
        (("git", "remote", "set-head", "origin", "--auto"), repo),
        (("git", "rev-parse", "--show-toplevel"), other),
        (("git", "remote", "get-url", "origin"), other),
        (("git", "fetch", "origin"), other),
        (("git", "remote", "set-head", "origin", "--auto"), other),
        (("git", "rev-parse", "--show-toplevel"), tree),
        (("git", "rev-parse", "--git-common-dir"), tree),
        (("git", "rev-parse", *in_progress), tree),
        (("git", "symbolic-ref", "refs/remotes/origin/HEAD"), repo),
        (("git", "reset", "--hard"), tree),
        (("git", "clean", "-ffd"), tree),
        (("git", "checkout", "--detach", "origin/main"), tree),
    ]
    assert runner.calls[15][1] == workspace / "two"
    assert not runner.commands("git", "worktree")


def test_reset_removes_stray_entries_at_the_root(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    (workspace / "notes.txt").write_text("scratch")
    (workspace / "build").mkdir()

    workspaces.reserve()

    assert sorted(entry.name for entry in workspace.iterdir()) == ["one", "two"]


def test_adds_a_missing_worktree_back_on_reset(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    shutil.rmtree(workspace / "one")
    runner.calls.clear()

    assert workspaces.reserve() == workspace

    repo, tree = tmp_path / "repos" / "one", workspace / "one"
    assert runner.calls[8:11] == [  # after the fetch of both clones
        (("git", "symbolic-ref", "refs/remotes/origin/HEAD"), repo),
        (("git", "worktree", "prune"), repo),
        (("git", "worktree", "add", "--detach", str(tree), "origin/main"), repo),
    ]
    assert [cwd for _, cwd in runner.commands("git", "reset")] == [workspace / "two"]


def test_replaces_a_folder_that_is_not_a_worktree_instead_of_resetting_it(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    # The agent removed .git from one/, so git would find an enclosing repository.
    runner.outputs["git rev-parse"] = lambda command, cwd: (
        f"{tmp_path}\n" if cwd == workspace / "one" else rev_parse(command, cwd)
    )
    runner.calls.clear()

    workspaces.reserve()

    tree = workspace / "one"
    assert [cwd for _, cwd in runner.commands("git", "reset")] == [workspace / "two"]
    assert [cwd for _, cwd in runner.commands("git", "clean")] == [workspace / "two"]
    assert runner.commands("git", "worktree", "add")[0][0][-2] == str(tree)


def test_replaces_a_worktree_that_became_a_repository_of_its_own(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    tree = workspace / "one"
    # The agent cloned the repository there: no fetch of ours reaches it.
    runner.outputs["git rev-parse"] = lambda command, cwd: (
        f"{cwd / '.git'}\n"
        if "--git-common-dir" in command and cwd == tree
        else rev_parse(command, cwd)
    )
    runner.calls.clear()

    workspaces.reserve()

    assert [cwd for _, cwd in runner.commands("git", "reset")] == [workspace / "two"]
    assert runner.commands("git", "worktree", "add")[0][0][-2] == str(tree)


def test_replaces_a_worktree_with_a_rebase_in_progress(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    tree = workspace / "one"
    (tree / ".git" / "rebase-merge").mkdir(parents=True)  # where the fake says
    runner.calls.clear()

    workspaces.reserve()

    assert [cwd for _, cwd in runner.commands("git", "reset")] == [workspace / "two"]
    assert runner.commands("git", "worktree", "add")[0][0][-2] == str(tree)


def test_a_failing_fetch_is_the_clones_error_not_a_workspaces(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner, max_count=1)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    runner.failures["git fetch"] = "fatal: could not read from remote\n"

    with pytest.raises(CommandError, match="git fetch origin failed"):
        workspaces.reserve()

    assert not runner.commands("git", "reset"), "no workspace was touched"
    assert workspaces._reserved == set()


def test_refuses_when_every_workspace_is_reserved_at_the_limit(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner, max_count=2)
    first = workspaces.reserve()
    workspaces.reserve()

    with pytest.raises(NoWorkspaceAvailableError, match="max_workspace_count is 2"):
        workspaces.reserve()

    workspaces.release(first)
    assert workspaces.reserve() == first


def test_a_failed_creation_keeps_its_number_until_released(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)
    taken: list[Path] = []

    reserving = False

    def reserve_while_pruning(command: tuple[str, ...], cwd: Path) -> None:
        # The folder is already gone, but ws0001 is still this creation's.
        nonlocal reserving
        gone = not (tmp_path / "workspaces" / "ws0001").exists()
        # On the clone check before the cleanup prune, outside the clone's lock.
        if command[:2] == ("git", "rev-parse") and gone and not reserving:
            reserving = True
            runner.failures.clear()
            taken.append(workspaces.reserve())

    runner.failures["git worktree add"] = "fatal: 'origin/main' is not a commit\n"
    runner.before_run = reserve_while_pruning

    with pytest.raises(CommandError):
        workspaces.create()

    assert taken == [tmp_path / "workspaces" / "ws0002"]
    assert workspaces.reserve() == tmp_path / "workspaces" / "ws0003"


def test_replaces_a_worktree_that_refuses_to_be_reset(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    runner.failures["git reset"] = "fatal: Unable to create 'index.lock'\n"
    runner.calls.clear()

    assert workspaces.reserve() == workspace

    tree = workspace / "one"
    assert [cwd for _, cwd in runner.commands("git", "worktree", "prune")] == [
        tmp_path / "repos" / "one",
        tmp_path / "repos" / "two",
    ]
    assert runner.commands("git", "worktree", "add")[0][0][-2] == str(tree)
    assert not runner.commands("git", "clean")


def break_workspace(runner: FakeRunner, workspace: Path) -> None:
    """Make every git command about `workspace` fail from now on."""

    def refuse(command: tuple[str, ...], cwd: Path) -> None:
        about_it = cwd.is_relative_to(workspace) or str(workspace) in " ".join(command)
        runner.failures = {"git": "fatal: broken\n"} if about_it else {}

    runner.before_run = refuse


def test_skips_a_workspace_it_cannot_reset(
    tmp_path: Path, runner: FakeRunner, caplog: pytest.LogCaptureFixture
) -> None:
    workspaces = pool(tmp_path, runner, max_count=3)
    first = workspaces.reserve()
    second = workspaces.reserve()
    workspaces.release(first)
    workspaces.release(second)
    break_workspace(runner, first)

    with caplog.at_level(logging.WARNING, logger="vuterm._workspaces"):
        assert workspaces.reserve() == second, "the next free one"
        assert workspaces.reserve() == tmp_path / "workspaces" / "ws0003", "a new one"

    assert [r.message[:40] for r in caplog.records] == [
        f"Could not reset {first}, trying another"[:40]
    ] * 2
    with pytest.raises(NoWorkspaceAvailableError, match="ws0001 could not be reset"):
        workspaces.reserve()


def test_raises_when_creating_a_workspace_fails(
    tmp_path: Path, runner: FakeRunner
) -> None:
    runner.failures["git worktree add"] = "fatal: 'origin/main' is not a commit\n"

    with pytest.raises(CommandError, match="git worktree add"):
        pool(tmp_path, runner).reserve()

    assert not (tmp_path / "workspaces" / "ws0001").exists()


def test_a_symlink_is_not_a_worktree(tmp_path: Path, runner: FakeRunner) -> None:
    workspaces = pool(tmp_path, runner)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    tree = workspace / "one"
    shutil.rmtree(tree)
    tree.symlink_to(workspace / "two")
    runner.calls.clear()

    workspaces.reserve()

    assert [cwd for _, cwd in runner.commands("git", "reset")] == [workspace / "two"]
    assert runner.commands("git", "worktree", "add")[0][0][-2] == str(tree)
    assert tree.is_dir() and not tree.is_symlink()
    assert (workspace / "two").exists(), "the symlink's target was left alone"


def test_refuses_a_clone_folder_that_is_not_a_repository(
    tmp_path: Path, runner: FakeRunner
) -> None:
    # git run in it would act on whatever repository encloses it.
    runner.outputs["git rev-parse"] = lambda _, cwd: f"{cwd.parent}\n"

    with pytest.raises(VutermError, match="repos/one is not a clone of Org/one"):
        pool(tmp_path, runner).reserve()

    assert not runner.commands("git", "fetch")
    assert not runner.commands("git", "worktree")


@pytest.mark.parametrize(
    ("url", "repository", "same"),
    [
        ("https://github.com/Org/repo.git", "Org/repo", True),
        ("git@github.com:Org/repo.git", "org/REPO", True),
        ("ssh://git@github.com/Org/repo", "Org/repo", True),
        ("/srv/git/Org/repo/", "Org/repo", True),
        ("https://github.com/Org/repo", "https://github.com/Org/repo.git", True),
        ("https://github.com/Org/repo", "repo", False),
        ("https://github.com/Other/repo.git", "Org/repo", False),
        ("https://github.com/Org/repo2.git", "Org/repo", False),
        ("https://github.com/Org/repo.git", "MyOrg/repo", False),
    ],
)
def test_same_repository(url: str, repository: str, same: bool) -> None:
    assert _same_repository(url, repository) is same


def test_refuses_a_clone_of_another_repository(
    tmp_path: Path, runner: FakeRunner
) -> None:
    runner.outputs["git remote"] = lambda command, cwd: (
        "https://github.com/Other/one.git\n" if command[2] == "get-url" else ""
    )

    with pytest.raises(VutermError, match=r"its origin is https://github\.com/Other"):
        pool(tmp_path, runner).reserve()

    assert not runner.commands("git", "fetch")


def test_a_symlinked_workspace_folder_is_neither_used_nor_touched(
    tmp_path: Path, runner: FakeRunner
) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "one").mkdir(parents=True)
    (elsewhere / "two").mkdir()
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / "ws0001").symlink_to(elsewhere)

    workspace = pool(tmp_path, runner).reserve()

    assert workspace == tmp_path / "workspaces" / "ws0002"
    assert not [call for call in runner.calls if call[1].is_relative_to(elsewhere)]
    assert (tmp_path / "workspaces" / "ws0001").is_symlink()
    assert (elsewhere / "one").is_dir()


def test_remove_leaves_the_modes_of_files_alone(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.write_text("shared")
    original.chmod(0o644)
    folder = tmp_path / "folder"
    folder.mkdir()
    os.link(original, folder / "hard-link")

    remove(folder)

    assert not folder.exists()
    assert stat.S_IMODE(original.stat().st_mode) == 0o644


def test_remove_takes_read_only_content_and_symlinks(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    (folder / "locked").mkdir(parents=True)
    (folder / "locked" / "file").write_text("x")
    (folder / "locked" / "sealed").mkdir()
    (folder / "locked" / "sealed" / "file").write_text("x")
    (folder / "link").symlink_to(tmp_path)
    (folder / "locked" / "file").chmod(0o444)
    (folder / "locked" / "sealed").chmod(0o000)  # cannot even be listed
    (folder / "locked").chmod(0o555)

    remove(folder)
    remove(tmp_path / "gone")  # nothing there: fine

    assert not folder.exists() and tmp_path.exists()


def test_concurrent_reservations_never_share_or_exceed_the_limit(
    tmp_path: Path, runner: FakeRunner
) -> None:
    workspaces = pool(tmp_path, runner, max_count=5)
    reserved: list[Path] = []
    refused: list[NoWorkspaceAvailableError] = []
    lock = threading.Lock()
    start = threading.Barrier(20)

    def reserve() -> None:
        start.wait()
        try:
            workspace = workspaces.reserve()
        except NoWorkspaceAvailableError as error:
            with lock:
                refused.append(error)
        else:
            with lock:
                reserved.append(workspace)

    threads = [threading.Thread(target=reserve) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(reserved) == 5 and len(set(reserved)) == 5
    assert len(refused) == 15
    assert len(list((tmp_path / "workspaces").iterdir())) == 5


# The real thing: git on a local origin whose default branch is not main.


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository with one commit on ``trunk``, cloned into ``repos/repo``.

    It lives at ``<tmp>/Org/repo``, so its path names the repository
    ``Org/repo`` the way a GitHub URL would.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    origin = make_origin(tmp_path / "Org" / "repo")
    (tmp_path / "repos").mkdir()
    git("clone", str(origin), str(tmp_path / "repos" / "repo"), cwd=tmp_path)
    return origin


def make_origin(path: Path) -> Path:
    path.mkdir(parents=True)
    git("init", "--initial-branch=trunk", cwd=path)
    (path / "file.txt").write_text("one\n")
    git("add", "file.txt", cwd=path)
    git("commit", "-m", "one", cwd=path)
    return path


def real_pool(root: Path, *repositories: str) -> WorkspacePool:
    return WorkspacePool(
        workspaces_dir=root / "workspaces",
        repos_dir=root / "repos",
        repositories=list(repositories or ["Org/repo"]),
        max_count=2,
        git=Git(SubprocessRunner()),
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_creates_and_resets_real_worktrees(tmp_path: Path, origin: Path) -> None:
    workspaces = real_pool(tmp_path)

    tree = workspaces.reserve() / "repo"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=tree) == "HEAD"  # detached
    assert git("rev-parse", "HEAD", cwd=tree) == git("rev-parse", "trunk", cwd=origin)
    assert (tree / "file.txt").read_text() == "one\n"

    # The agent works, and origin moves on meanwhile.
    (tree / "file.txt").write_text("changed\n")
    (tree / "untracked.txt").write_text("left behind\n")
    (tree / "ignored.txt").write_text("kept\n")
    (tree / ".gitignore").write_text("ignored.txt\n")
    git("switch", "--create", "agent-branch", cwd=tree)
    git("init", str(tree / "nested"), cwd=tree)  # an untracked repository
    (origin / "file.txt").write_text("two\n")
    git("commit", "-am", "two", cwd=origin)
    workspaces.release(tree.parent)

    assert workspaces.reserve() == tree.parent
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=tree) == "HEAD"
    assert git("rev-parse", "HEAD", cwd=tree) == git("rev-parse", "trunk", cwd=origin)
    assert (tree / "file.txt").read_text() == "two\n"
    assert not (tree / "untracked.txt").exists()
    assert not (tree / "nested").exists(), "nested repositories go too"
    assert not (tree / ".gitignore").exists()
    assert (tree / "ignored.txt").read_text() == "kept\n", "ignored files stay"
    assert "agent-branch" in git("branch", "--list", cwd=tree)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_removes_what_the_agent_left_at_the_workspace_root(
    tmp_path: Path, origin: Path
) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    (workspace / "CLAUDE.md").write_text("instructions for the last task\n")
    (workspace / ".claude").mkdir()
    (workspace / ".claude" / "settings.json").write_text("{}")
    (workspace / "other-repo").symlink_to(tmp_path / "repos" / "repo")

    assert workspaces.reserve() == workspace

    assert sorted(entry.name for entry in workspace.iterdir()) == ["repo"]
    assert (tmp_path / "repos" / "repo" / "file.txt").exists(), "symlink target kept"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_creates_again_after_the_workspaces_were_deleted(
    tmp_path: Path, origin: Path
) -> None:
    workspaces = real_pool(tmp_path)
    first = workspaces.create()
    shutil.rmtree(tmp_path / "workspaces")  # by hand, to start over

    assert workspaces.create() == first
    assert (first / "repo" / "file.txt").read_text() == "one\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_refuses_a_clone_of_another_repository_with_the_same_name(
    tmp_path: Path, origin: Path
) -> None:
    other = make_origin(tmp_path / "Other" / "repo")
    shutil.rmtree(tmp_path / "repos" / "repo")
    git("clone", str(other), str(tmp_path / "repos" / "repo"), cwd=tmp_path)

    with pytest.raises(VutermError, match=r"not a clone of Org/repo \(its origin is"):
        real_pool(tmp_path).reserve()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_creates_again_after_a_failed_creation(tmp_path: Path, origin: Path) -> None:
    # The second repository's origin is gone: its fetch fails after the first
    # worktree was added and registered in the clone.
    orphan = make_origin(tmp_path / "Org" / "orphan")
    git("clone", str(orphan), str(tmp_path / "repos" / "orphan"), cwd=tmp_path)
    shutil.rmtree(orphan)
    with pytest.raises(CommandError, match="git fetch origin failed"):
        real_pool(tmp_path, "Org/repo", "Org/orphan").create()
    assert not (tmp_path / "workspaces" / "ws0001").exists()

    workspace = real_pool(tmp_path).create()

    assert workspace == tmp_path / "workspaces" / "ws0001"
    assert (workspace / "repo" / "file.txt").read_text() == "one\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_repairs_a_worktree_the_agent_turned_into_a_repository(
    tmp_path: Path, origin: Path
) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    tree = workspace / "repo"
    shutil.rmtree(tree / ".git", ignore_errors=True)
    (tree / ".git").unlink(missing_ok=True)
    git("init", cwd=tree)  # a repository of its own now, with no origin
    (tree / "file.txt").write_text("changed\n")

    assert workspaces.reserve() == workspace

    assert (tree / "file.txt").read_text() == "one\n"
    assert git("rev-parse", "HEAD", cwd=tree) == git("rev-parse", "trunk", cwd=origin)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_follows_a_renamed_default_branch(tmp_path: Path, origin: Path) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    git("branch", "--move", "trunk", "main", cwd=origin)
    (origin / "file.txt").write_text("renamed\n")
    git("commit", "-am", "on main", cwd=origin)

    assert workspaces.reserve() == workspace

    clone = tmp_path / "repos" / "repo"
    assert git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=clone).endswith("/main")
    assert (workspace / "repo" / "file.txt").read_text() == "renamed\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_replaces_a_repository_the_agent_put_in_a_worktrees_place(
    tmp_path: Path, origin: Path
) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    tree = workspace / "repo"
    shutil.rmtree(tree)
    git("clone", str(origin), str(tree), cwd=tmp_path)  # looks right, is not ours

    assert workspaces.reserve() == workspace

    common = Path(git("rev-parse", "--git-common-dir", cwd=tree))
    assert (tree / common).resolve() == (tmp_path / "repos" / "repo" / ".git").resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_replaces_a_worktree_left_in_the_middle_of_a_rebase(
    tmp_path: Path, origin: Path
) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    tree = workspace / "repo"
    base = git("rev-parse", "HEAD", cwd=tree)
    git("switch", "--create", "theirs", cwd=tree)
    (tree / "file.txt").write_text("theirs\n")
    git("commit", "-am", "theirs", cwd=tree)
    git("switch", "--create", "ours", base, cwd=tree)
    (tree / "file.txt").write_text("ours\n")
    git("commit", "-am", "ours", cwd=tree)
    conflict = subprocess.run(
        ["git", "rebase", "theirs"], cwd=tree, capture_output=True
    )
    assert conflict.returncode != 0, "a conflict, left as it is"

    assert workspaces.reserve() == workspace

    assert "rebase" not in git("status", cwd=tree)
    assert (tree / "file.txt").read_text() == "one\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_replaces_a_worktree_left_in_a_bisect(tmp_path: Path, origin: Path) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    tree = workspace / "repo"
    git("bisect", "start", cwd=tree)
    assert "bisect" in git("status", cwd=tree).lower()

    assert workspaces.reserve() == workspace

    assert "bisect" not in git("status", cwd=tree).lower()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_never_resets_through_a_symlink(tmp_path: Path, origin: Path) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    clone = tmp_path / "repos" / "repo"
    (clone / "precious.txt").write_text("uncommitted work in the clone\n")
    shutil.rmtree(workspace / "repo")
    (workspace / "repo").symlink_to(clone)

    assert workspaces.reserve() == workspace

    assert (clone / "precious.txt").exists(), "clean never ran in the clone"
    assert not (workspace / "repo").is_symlink()
    assert (workspace / "repo" / "file.txt").read_text() == "one\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_replaces_a_worktree_with_read_only_content(
    tmp_path: Path, origin: Path
) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    locked = workspace / "repo" / "locked"
    locked.mkdir()
    (locked / "file").write_text("x")
    locked.chmod(0o555)  # git clean cannot unlink the file inside

    assert workspaces.reserve() == workspace

    assert not locked.exists()
    assert (workspace / "repo" / "file.txt").read_text() == "one\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths in git output")
def test_repairs_a_worktree_that_was_deleted(tmp_path: Path, origin: Path) -> None:
    workspaces = real_pool(tmp_path)
    workspace = workspaces.reserve()
    workspaces.release(workspace)
    shutil.rmtree(workspace / "repo")

    assert workspaces.reserve() == workspace

    assert (workspace / "repo" / "file.txt").read_text() == "one\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=workspace / "repo") == "HEAD"
