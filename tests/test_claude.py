# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Claude Code harness, replayed from recorded ``claude -p`` sessions.

``fixtures/claude/<scenario>.jsonl`` holds the stream-json output of a real run,
trimmed to the fields the harness reads.
"""

import json
from pathlib import Path

import pytest

from vuterm import AgentResult
from vuterm.harnesses import AgentSession
from vuterm.harnesses.claude import ClaudeHarness

FIXTURES = Path(__file__).parent / "fixtures" / "claude"
TASK = "update the README"
SESSION_ID = "00000000-0000-4000-8000-000000000000"


def replay(scenario: str) -> tuple[AgentSession, list[str]]:
    """Feed a recorded session line by line and return the log messages."""
    session = ClaudeHarness().start(TASK)
    lines = (FIXTURES / f"{scenario}.jsonl").read_text().splitlines()
    messages = [message for line in lines if (message := session.feed(line))]
    return session, messages


def test_runs_claude_headless_on_the_task() -> None:
    assert ClaudeHarness().start(TASK).command == [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
        "--",
        TASK,
    ]


def test_passes_the_permission_mode() -> None:
    command = ClaudeHarness(permission_mode="acceptEdits").start(TASK).command

    assert command[command.index("--permission-mode") + 1] == "acceptEdits"


@pytest.mark.parametrize(
    ("scenario", "returncode", "success"),
    [("answer", 0, True), ("tool_use", 0, True), ("api_error", 1, False)],
)
def test_reads_the_outcome(scenario: str, returncode: int, success: bool) -> None:
    session, _ = replay(scenario)

    assert session.finish(returncode) == AgentResult(success=success)


def test_fails_when_claude_exits_non_zero_after_a_successful_result() -> None:
    session, _ = replay("answer")

    assert session.finish(1) == AgentResult(success=False)


def test_fails_without_a_result_event() -> None:
    assert ClaudeHarness().start(TASK).finish(0) == AgentResult(success=False)


def test_logs_answers_tool_calls_and_the_outcome() -> None:
    _, messages = replay("tool_use")

    assert messages == [
        f"Session {SESSION_ID} started with model claude-opus-5[1m]",
        'Tool call Bash: {"command": "ls", '
        '"description": "List files in current directory"}',
        "Tool result: a.txt\nerr.txt\nout.jsonl",
        "a.txt\nerr.txt\nout.jsonl",
        "Agent completed (turns: 2)",
    ]


def test_logs_the_error() -> None:
    _, messages = replay("api_error")

    assert messages[-1].startswith(
        "Agent errored (turns: 1): There's an issue with the selected model"
    )


@pytest.mark.parametrize("line", ["", "  ", '{"type": "rate_limit_event"}'])
def test_skips_lines_without_a_message(line: str) -> None:
    assert ClaudeHarness().start(TASK).feed(line) is None


@pytest.mark.parametrize("line", ["Warning: not json", '{"type": "assistant"}'])
def test_logs_unreadable_lines_as_written(line: str) -> None:
    assert ClaudeHarness().start(TASK).feed(line) == line


def test_truncates_long_tool_results() -> None:
    block = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "x" * 1000}
    event = {"type": "user", "message": {"role": "user", "content": [block]}}

    message = ClaudeHarness().start(TASK).feed(json.dumps(event))

    assert message == "Tool result: " + "x" * 300 + "…"
