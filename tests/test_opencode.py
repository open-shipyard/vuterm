# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""OpenCode harness, replayed from recorded ``opencode run`` sessions.

``fixtures/opencode/<scenario>.jsonl`` holds the ``--format json`` output of a
real run.
"""

import json
from pathlib import Path

import pytest

from vuterm import AgentResult
from vuterm.harnesses import AgentSession
from vuterm.harnesses.opencode import OpenCodeHarness

FIXTURES = Path(__file__).parent / "fixtures" / "opencode"
TASK = "update the README"


def replay(scenario: str) -> tuple[AgentSession, list[str]]:
    """Feed a recorded session line by line and return the log messages."""
    session = OpenCodeHarness().start(TASK)
    lines = (FIXTURES / f"{scenario}.jsonl").read_text().splitlines()
    messages = [message for line in lines if (message := session.feed(line))]
    return session, messages


def test_runs_opencode_headless_on_the_task() -> None:
    assert OpenCodeHarness().start(TASK).command == [
        "opencode",
        "run",
        "--format",
        "json",
        "--auto",
        "--",
        TASK,
    ]


def test_can_leave_permissions_to_the_opencode_config() -> None:
    command = OpenCodeHarness(auto_approve=False).start(TASK).command

    assert "--auto" not in command


@pytest.mark.parametrize(
    ("scenario", "returncode", "expected"),
    [
        ("answer", 0, AgentResult(success=True, response="ok")),
        (
            "tool_use",
            0,
            AgentResult(success=True, response="a.txt, err.txt, out.jsonl"),
        ),
        ("tool_error", 0, AgentResult(success=True, response="done")),
        ("error", 1, AgentResult(success=False)),
    ],
)
def test_reads_the_outcome(
    scenario: str, returncode: int, expected: AgentResult
) -> None:
    session, _ = replay(scenario)

    assert session.finish(returncode) == expected


def test_fails_when_opencode_exits_non_zero() -> None:
    session, _ = replay("answer")

    assert session.finish(1) == AgentResult(success=False, response="ok")


def test_the_response_is_the_last_steps_text_not_what_came_before_a_tool() -> None:
    session, messages = replay("text_then_tool")

    response = session.finish(0).response

    assert messages[1] == "Let me look."
    assert response is not None
    assert response.startswith("The directory contains three files:")
    assert "Let me look." not in response


def test_has_no_response_when_the_run_fails_after_a_tool_call() -> None:
    session = OpenCodeHarness().start(TASK)
    lines = (FIXTURES / "text_then_tool.jsonl").read_text().splitlines()
    error = {"type": "error", "error": {"name": "APIError", "data": {"message": "x"}}}
    for line in [*lines[:4], json.dumps(error)]:  # up to the tool-calls step_finish
        session.feed(line)

    assert session.finish(1) == AgentResult(success=False)


def test_joins_the_text_parts_of_the_last_step() -> None:
    session = OpenCodeHarness().start(TASK)
    step = {"type": "step_start", "sessionID": "ses_1"}
    for line in [step, text_event("one"), text_event(""), text_event("two")]:
        session.feed(json.dumps(line))

    assert session.finish(0) == AgentResult(success=True, response="one\ntwo")


def test_has_no_response_when_the_last_step_has_no_text() -> None:
    session = OpenCodeHarness().start(TASK)
    step = {"type": "step_start", "sessionID": "ses_1"}
    for line in [step, text_event("Let me look."), step]:
        session.feed(json.dumps(line))

    assert session.finish(0) == AgentResult(success=True)


def text_event(text: str) -> dict[str, object]:
    return {"type": "text", "part": {"type": "text", "text": text}}


def test_fails_when_opencode_reports_an_error_but_exits_zero() -> None:
    session, _ = replay("error")

    assert session.finish(0) == AgentResult(success=False)


def test_logs_the_session_answers_and_tool_calls() -> None:
    _, messages = replay("tool_use")

    assert messages == [
        "Session ses_f6de93b26ffeuTI1a5RLrupjjG started",
        'Tool call bash: {"command": "ls"}\nTool result: a.txt\nerr.txt\nout.jsonl',
        "a.txt, err.txt, out.jsonl",
    ]


def test_logs_failed_tool_calls() -> None:
    _, messages = replay("tool_error")

    assert messages[1] == (
        'Tool call read: {"filePath": "/workspace/missing.txt"}\n'
        "Tool error: File not found: /workspace/missing.txt"
    )


def test_logs_the_error() -> None:
    _, messages = replay("error")

    assert messages == [
        "Agent errored: UnknownError: "
        "Unexpected server error. Check server logs for details."
    ]


@pytest.mark.parametrize("line", ["", "  ", '{"type": "step_finish"}'])
def test_skips_lines_without_a_message(line: str) -> None:
    assert OpenCodeHarness().start(TASK).feed(line) is None


@pytest.mark.parametrize("line", ["Warning: not json", '{"type": "text"}'])
def test_logs_unreadable_lines_as_written(line: str) -> None:
    assert OpenCodeHarness().start(TASK).feed(line) == line


def test_truncates_long_tool_output() -> None:
    state = {
        "status": "completed",
        "input": {"command": "cat big"},
        "output": "x" * 1000,
    }
    event = {
        "type": "tool_use",
        "part": {"type": "tool", "tool": "bash", "state": state},
    }

    message = OpenCodeHarness().start(TASK).feed(json.dumps(event))

    assert message == 'Tool call bash: {"command": "cat big"}\nTool result: ' + (
        "x" * 300 + "…"
    )
