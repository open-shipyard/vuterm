# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``git`` wrapper: which commands it runs, and how failures surface."""

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import FakeRunner

from vuterm import CommandError, Git

REPO = Path("/work/repos/repo")
TREE = Path("/work/workspaces/ws0001/repo")


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (
            lambda git: git.add_worktree(REPO, TREE, "origin/main"),
            (("git", "worktree", "add", "--detach", str(TREE), "origin/main"), REPO),
        ),
        (
            lambda git: git.checkout_detached(TREE, "origin/main"),
            (("git", "checkout", "--detach", "origin/main"), TREE),
        ),
        (lambda git: git.reset_hard(TREE), (("git", "reset", "--hard"), TREE)),
        (lambda git: git.clean(TREE), (("git", "clean", "-ffd"), TREE)),
        (
            lambda git: git.create_branch(TREE, "feature/x"),
            (("git", "switch", "--create", "feature/x"), TREE),
        ),
        (
            lambda git: git.push(TREE, "feature/x"),
            (
                (
                    "git",
                    "push",
                    "--set-upstream",
                    "origin",
                    "refs/heads/feature/x:refs/heads/feature/x",
                ),
                TREE,
            ),
        ),
        (
            lambda git: git.delete_branch(TREE, "feature/x"),
            (("git", "branch", "--delete", "--force", "feature/x"), TREE),
        ),
        (lambda git: git.prune_worktrees(REPO), (("git", "worktree", "prune"), REPO)),
    ],
    ids=[
        "add_worktree",
        "checkout_detached",
        "reset_hard",
        "clean",
        "create_branch",
        "push",
        "delete_branch",
        "prune_worktrees",
    ],
)
def test_runs_the_git_command(
    runner: FakeRunner,
    call: Callable[[Git], None],
    expected: tuple[tuple[str, ...], Path],
) -> None:
    call(Git(runner))

    assert runner.calls == [expected]


def test_fetch_follows_the_default_branch(runner: FakeRunner) -> None:
    Git(runner).fetch(REPO)

    assert runner.calls == [
        (("git", "fetch", "origin"), REPO),
        (("git", "remote", "set-head", "origin", "--auto"), REPO),
    ]


def test_reads_the_common_dir_relative_to_the_folder(runner: FakeRunner) -> None:
    runner.outputs["git rev-parse"] = ".git\n"

    assert Git(runner).common_dir(REPO) == (REPO / ".git").resolve()
    assert runner.calls == [(("git", "rev-parse", "--git-common-dir"), REPO)]


def test_reads_git_paths(runner: FakeRunner) -> None:
    runner.outputs["git rev-parse"] = ".git/rebase-merge\n/abs/rebase-apply\n"

    paths = Git(runner).git_paths(TREE, "rebase-merge", "rebase-apply")

    assert paths == [(TREE / ".git/rebase-merge").resolve(), Path("/abs/rebase-apply")]
    assert runner.calls[0][0] == (
        "git",
        "rev-parse",
        "--git-path",
        "rebase-merge",
        "--git-path",
        "rebase-apply",
    )


def test_reads_the_default_branch_from_origin_head(runner: FakeRunner) -> None:
    runner.outputs["git symbolic-ref"] = "refs/remotes/origin/trunk\n"

    assert Git(runner).default_branch(REPO) == "trunk"
    assert runner.calls == [(("git", "symbolic-ref", "refs/remotes/origin/HEAD"), REPO)]


@pytest.mark.parametrize(
    "name",
    [
        "",
        "-x",
        "--receive-pack=sh",
        "a b",
        "a\n",
        "+main",
        ":main",
        "a:b",
        "*",
        "feature/*",
        "a?",
        "a[0]",
        "a^",
        "a~1",
        "a..b",
        "a@{1}",
        "/a",
        "a/",
        "a//b",
        ".a",
        "a/.b",
        "a.lock",
        "a\\b",
        "é",
    ],
)
def test_refuses_a_branch_name_git_could_read_as_an_option(
    runner: FakeRunner, name: str
) -> None:
    git = Git(runner)
    for call in (git.create_branch, git.push, git.delete_branch):
        with pytest.raises(ValueError, match="not a branch name"):
            call(TREE, name)

    assert runner.calls == []


@pytest.mark.parametrize("name", ["main", "feature/x", "v1.2-rc.1", "a_b/c-d.e"])
def test_accepts_plain_branch_names(runner: FakeRunner, name: str) -> None:
    Git(runner).create_branch(TREE, name)

    assert runner.calls == [(("git", "switch", "--create", name), TREE)]


def test_reads_the_toplevel_of_a_worktree(runner: FakeRunner) -> None:
    runner.outputs["git rev-parse"] = "/work/workspaces/ws0001/repo\n"

    assert Git(runner).toplevel(TREE / "src") == TREE
    assert runner.calls == [(("git", "rev-parse", "--show-toplevel"), TREE / "src")]


def test_refuses_an_origin_head_outside_origin(runner: FakeRunner) -> None:
    runner.outputs["git symbolic-ref"] = "refs/heads/main\n"

    with pytest.raises(CommandError, match="not a branch of origin"):
        Git(runner).default_branch(REPO)


def test_a_failing_command_raises_with_its_status_and_stderr(
    runner: FakeRunner,
) -> None:
    runner.failures["git fetch"] = "fatal: could not read from remote repository\n"

    with pytest.raises(CommandError, match="git fetch origin failed") as raised:
        Git(runner).fetch(REPO)

    assert raised.value.returncode == 128
    assert raised.value.stderr.startswith("fatal: could not read")
    assert "could not read from remote" in str(raised.value)
