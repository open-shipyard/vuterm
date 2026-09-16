# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""The client: launching agents, in the current folder or in a workspace."""

import logging
from pathlib import Path

import pytest
from conftest import FakeRunner

from vuterm import AgentResult, Client, NoWorkspaceAvailableError, VutermError
from vuterm.harnesses.claude import ClaudeHarness

FIXTURES = Path(__file__).parent / "fixtures"
TASK = "update the README"


def client(
    root: Path,
    runner: FakeRunner,
    *,
    max_workspace_count: int = 10,
    cloned: bool = True,
) -> Client:
    if cloned:  # the clones, as far as the fake can tell
        for name in ("one", "two"):
            (root / "repos" / name).mkdir(parents=True, exist_ok=True)
    return Client(
        working_dir=root,
        repositories=["Org/one", "Org/two"],
        runner=runner,
        max_workspace_count=max_workspace_count,
    )


def replay(runner: FakeRunner, scenario: str) -> None:
    runner.stdout_lines = (FIXTURES / scenario).read_text().splitlines()


def test_launches_the_agent_in_the_current_folder(
    tmp_path: Path, runner: FakeRunner, caplog: pytest.LogCaptureFixture
) -> None:
    replay(runner, "claude/tool_use.jsonl")
    runner.stderr_lines = ["a warning"]

    with caplog.at_level(logging.INFO, logger="vuterm.agent"):
        result = client(tmp_path, runner).launch_agent("claude", TASK)

    assert result == AgentResult(success=True, response="a.txt\nerr.txt\nout.jsonl")
    assert runner.streamed == [(tuple(ClaudeHarness().start(TASK).command), Path.cwd())]
    assert runner.calls == [], "no git or gh outside a workspace"
    messages = [(record.levelname, record.message) for record in caplog.records]
    assert messages[0] == ("INFO", f"Running claude in {Path.cwd()}")
    assert ("INFO", "Agent completed (turns: 2)") in messages
    assert messages[-1] == ("WARNING", "a warning")


def test_reports_an_agent_error_as_a_result(tmp_path: Path, runner: FakeRunner) -> None:
    replay(runner, "claude/api_error.jsonl")
    runner.returncode = 1

    result = client(tmp_path, runner).launch_agent("claude", TASK)

    assert not result.success
    assert result.response is not None
    assert result.response.startswith("There's an issue with the selected model")


def test_selects_the_harness_by_name(tmp_path: Path, runner: FakeRunner) -> None:
    replay(runner, "opencode/answer.jsonl")

    client(tmp_path, runner).launch_agent("opencode", TASK)

    assert runner.streamed[0][0][:2] == ("opencode", "run")


def test_refuses_an_unknown_harness(tmp_path: Path, runner: FakeRunner) -> None:
    with pytest.raises(ValueError, match="no harness is named 'codex'; available: "):
        client(tmp_path, runner).launch_agent("codex", TASK)
    with pytest.raises(ValueError, match="claude, opencode"):
        client(tmp_path, runner).launch_agent_in_workspace("codex", TASK)

    assert runner.streamed == [] and not (tmp_path / "workspaces").exists()


def test_launches_in_a_reserved_workspace_and_releases_it(
    tmp_path: Path, runner: FakeRunner
) -> None:
    replay(runner, "claude/answer.jsonl")
    vt = client(tmp_path, runner)
    workspace = tmp_path / "workspaces" / "ws0001"
    seen_during_run: list[Path] = []
    # Another thread's agent, while this one runs: the workspace is taken.
    runner.while_streaming = lambda: seen_during_run.append(vt._workspaces.reserve())

    assert vt.launch_agent_in_workspace("claude", TASK) == AgentResult(
        success=True, response="ok"
    )

    assert runner.streamed[0][1] == workspace
    assert seen_during_run == [tmp_path / "workspaces" / "ws0002"]
    runner.while_streaming = None
    vt.launch_agent_in_workspace("claude", TASK)
    assert runner.streamed[1][1] == workspace, "released, so reused"


def test_releases_the_workspace_when_the_run_raises(
    tmp_path: Path, runner: FakeRunner
) -> None:
    vt = client(tmp_path, runner, max_workspace_count=1)

    def boom() -> None:
        raise FileNotFoundError("claude")

    runner.while_streaming = boom
    with pytest.raises(FileNotFoundError):
        vt.launch_agent_in_workspace("claude", TASK)

    runner.while_streaming = None
    vt.launch_agent_in_workspace("claude", TASK)  # no NoWorkspaceAvailableError
    assert len(runner.streamed) == 2


def test_passes_the_workspace_limit_on(tmp_path: Path, runner: FakeRunner) -> None:
    vt = client(tmp_path, runner, max_workspace_count=1)
    vt._workspaces.reserve()

    with pytest.raises(NoWorkspaceAvailableError):
        vt.launch_agent_in_workspace("claude", TASK)


def test_init_clones_missing_repositories_and_fetches_the_rest(
    tmp_path: Path, runner: FakeRunner
) -> None:
    repos = tmp_path / "repos"
    (repos / "two").mkdir(parents=True)

    client(tmp_path, runner, cloned=False).init()

    assert [call for call in runner.calls if call[0][1] != "rev-parse"] == [
        (("gh", "repo", "clone", "Org/one", str(repos / "one")), repos),
        (("git", "remote", "get-url", "origin"), repos / "two"),
        (("git", "fetch", "origin"), repos / "two"),
        (("git", "remote", "set-head", "origin", "--auto"), repos / "two"),
    ]


def test_init_clones_into_an_empty_folder_and_refuses_anything_else(
    tmp_path: Path, runner: FakeRunner
) -> None:
    repos = tmp_path / "repos"
    (repos / "one").mkdir(parents=True)  # an interrupted clone
    (repos / "two").mkdir()
    (repos / "two" / "notes.txt").write_text("not a clone")
    runner.failures["git rev-parse"] = "fatal: not a git repository\n"

    with pytest.raises(VutermError, match="repos/two is not a clone of Org/two"):
        client(tmp_path, runner, cloned=False).init()

    assert runner.commands("gh") == [
        (("gh", "repo", "clone", "Org/one", str(repos / "one")), repos)
    ]
    assert not runner.commands("git", "fetch")


def test_init_refuses_a_clone_of_another_repository(
    tmp_path: Path, runner: FakeRunner
) -> None:
    runner.outputs["git remote"] = lambda command, cwd: (
        f"git@github.com:Other/{cwd.name}.git\n" if command[2] == "get-url" else ""
    )
    vt = client(tmp_path, runner)
    (tmp_path / "repos" / "one" / "file.txt").write_text("their code")

    with pytest.raises(VutermError, match=r"its origin is git@github\.com:Other/one"):
        vt.init()

    assert not runner.commands("git", "fetch") and not runner.commands("gh")


def test_prepares_a_new_workspace(tmp_path: Path, runner: FakeRunner) -> None:
    vt = client(tmp_path, runner)

    assert vt.prepare_new_workspace() == tmp_path / "workspaces" / "ws0001"
    assert vt.prepare_new_workspace() == tmp_path / "workspaces" / "ws0002"
    assert len(runner.commands("git", "worktree", "add")) == 4


def test_a_relative_working_dir_is_made_absolute(
    tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    vt = Client(working_dir="work", repositories=["Org/repo"], runner=runner)

    vt.init()
    workspace = vt.prepare_new_workspace()

    repos = tmp_path.resolve() / "work" / "repos"
    assert runner.calls[0] == (
        ("gh", "repo", "clone", "Org/repo", str(repos / "repo")),
        repos,
    )
    assert workspace == tmp_path.resolve() / "work" / "workspaces" / "ws0001"
    assert runner.commands("git", "worktree", "add")[0][0][-2] == str(
        workspace / "repo"
    )


def test_refuses_repositories_sharing_a_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="share a name"):
        Client(working_dir=tmp_path, repositories=["A/repo", "B/repo"])


def test_refuses_a_repository_without_an_owner(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="wants owner/name"):
        Client(working_dir=tmp_path, repositories=["repo"])
