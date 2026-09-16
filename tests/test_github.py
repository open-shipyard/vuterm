# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``gh`` wrapper: which commands it runs, and how failures surface."""

from pathlib import Path

import pytest
from conftest import FakeRunner

from vuterm import CommandError, GitHub

REPOS = Path("/work/repos")
TREE = Path("/work/workspaces/ws0001/repo")


def test_clones_into_the_destination(runner: FakeRunner) -> None:
    GitHub(runner).clone("Org/repo", REPOS / "repo")

    assert runner.calls == [
        (("gh", "repo", "clone", "Org/repo", str(REPOS / "repo")), REPOS)
    ]


def test_creates_a_pull_request_and_returns_its_url(runner: FakeRunner) -> None:
    runner.outputs["gh pr"] = (
        "Creating pull request...\nhttps://github.com/o/r/pull/7\n"
    )

    url = GitHub(runner).create_pull_request(TREE, title="Title", body="Body")

    assert url == "https://github.com/o/r/pull/7"
    assert runner.calls == [
        (("gh", "pr", "create", "--title", "Title", "--body", "Body"), TREE)
    ]


def test_creates_a_pull_request_against_a_base(runner: FakeRunner) -> None:
    GitHub(runner).create_pull_request(TREE, title="T", body="B", base="release")

    assert runner.calls[0][0][-2:] == ("--base", "release")


def test_a_pull_request_without_a_url_raises(runner: FakeRunner) -> None:
    runner.outputs["gh pr"] = "\n"

    with pytest.raises(CommandError, match="printed no pull request URL"):
        GitHub(runner).create_pull_request(TREE, title="T", body="B")


def test_a_failing_command_raises_with_its_status_and_stderr(
    runner: FakeRunner,
) -> None:
    runner.failures["gh pr"] = "pull request create failed: not logged in\n"

    with pytest.raises(CommandError, match="gh pr create failed") as raised:
        GitHub(runner).create_pull_request(TREE, title="T", body="B")

    assert raised.value.returncode == 128
    assert "not logged in" in raised.value.stderr
