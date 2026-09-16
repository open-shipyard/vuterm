# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""`SubprocessRunner`, on real processes: Python itself."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from vuterm import CommandTimeoutError, CompletedCommand, SubprocessRunner


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_run_captures_output_and_status(tmp_path: Path) -> None:
    command = python(
        "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    )

    completed = SubprocessRunner().run(command, cwd=tmp_path)

    assert completed == CompletedCommand(
        args=tuple(command), returncode=3, stdout="out\n", stderr="err\n"
    )


def test_run_decodes_utf8_whatever_the_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    command = python("import sys; sys.stdout.buffer.write(b'caf\\xc3\\xa9 \\xff')")

    assert SubprocessRunner().run(command, cwd=tmp_path).stdout == "café �"


def test_run_tells_git_and_ssh_not_to_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIT_TERMINAL_PROMPT", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    command = python(
        "import os\n"
        "print(os.environ['GIT_TERMINAL_PROMPT'], os.environ['SSH_ASKPASS_REQUIRE'],"
        " os.environ.get('GIT_SSH_COMMAND', 'unset'))"
    )

    stdout = SubprocessRunner().run(command, cwd=tmp_path).stdout

    assert stdout == "0 force unset\n", "the caller's setting wins; ssh is git's"


def test_run_in_the_given_folder_with_no_stdin(tmp_path: Path) -> None:
    command = python("import os, sys; print(os.getcwd()); print(len(sys.stdin.read()))")

    completed = SubprocessRunner().run(command, cwd=tmp_path)

    assert completed.stdout.splitlines() == [str(tmp_path.resolve()), "0"]


def test_stream_passes_each_line_on_and_returns_the_status(tmp_path: Path) -> None:
    command = python(
        "import sys\n"
        "for i in range(3): print('out', i)\n"
        "for i in range(2): print('err', i, file=sys.stderr)\n"
        "sys.exit(2)"
    )
    out: list[str] = []
    err: list[str] = []

    status = SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=err.append
    )

    assert status == 2
    assert out == ["out 0", "out 1", "out 2"]
    assert err == ["err 0", "err 1"]


def test_stream_reads_both_pipes_however_much_is_written(tmp_path: Path) -> None:
    # More than a pipe buffer on each: a reader of one pipe at a time would hang.
    command = python(
        "import sys\n"
        "for i in range(20000): print('x' * 80)\n"
        "for i in range(20000): print('y' * 80, file=sys.stderr)\n"
    )
    out: list[str] = []
    err: list[str] = []

    status = SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=err.append
    )

    assert status == 0
    assert len(out) == 20000 and len(err) == 20000


def test_stream_replaces_undecodable_bytes(tmp_path: Path) -> None:
    command = python("import sys; sys.stdout.buffer.write(b'a\\xffb\\n')")
    out: list[str] = []

    SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=lambda _: None
    )

    assert out == ["a�b"]


def test_stream_kills_the_process_when_a_callback_raises(tmp_path: Path) -> None:
    command = python("import time; print('first', flush=True); time.sleep(30)")

    def refuse(line: str) -> None:
        raise RuntimeError(line)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="first"):
        SubprocessRunner().stream(
            command, cwd=tmp_path, on_stdout=refuse, on_stderr=lambda _: None
        )

    assert time.monotonic() - started < 10, "killed, not waited for"


def test_stream_kills_the_process_when_the_stderr_callback_raises(
    tmp_path: Path,
) -> None:
    command = python(
        "import sys, time; print('warn', file=sys.stderr, flush=True); time.sleep(30)"
    )

    def refuse(line: str) -> None:
        raise RuntimeError(line)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="warn"):
        SubprocessRunner().stream(
            command, cwd=tmp_path, on_stdout=lambda _: None, on_stderr=refuse
        )

    assert time.monotonic() - started < 10, "killed, not waited for"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_stream_returns_when_the_agent_exits_leaving_a_child_behind(
    tmp_path: Path,
) -> None:
    # The child inherits the pipes; if it lived on, they would never close.
    command = python(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print('started')"
    )
    out: list[str] = []

    started = time.monotonic()
    status = SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=lambda _: None
    )

    assert (status, out) == (0, ["started"])
    assert time.monotonic() - started < 10, "the child was stopped with the run"


def test_stream_within_its_timeout_returns_the_status(tmp_path: Path) -> None:
    out: list[str] = []

    status = SubprocessRunner().stream(
        python("print('done'); raise SystemExit(3)"),
        cwd=tmp_path,
        on_stdout=out.append,
        on_stderr=lambda _: None,
        timeout=30,
    )

    assert (status, out) == (3, ["done"])


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_stream_stops_a_command_past_its_timeout_with_its_group(
    tmp_path: Path,
) -> None:
    pidfile = tmp_path / "pid"
    command = python(
        "import subprocess, sys, time\n"
        "sleeper = [sys.executable, '-c', 'import time; time.sleep(30)']\n"
        "child = subprocess.Popen(sleeper)\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "print('working', flush=True)\n"
        "time.sleep(30)"
    )
    out: list[str] = []

    started = time.monotonic()
    with pytest.raises(CommandTimeoutError, match=r"after 0\.5 s") as raised:
        SubprocessRunner().stream(
            command,
            cwd=tmp_path,
            on_stdout=out.append,
            on_stderr=lambda _: None,
            timeout=0.5,
        )

    assert time.monotonic() - started < 10
    assert out == ["working"], "what it wrote is delivered"
    assert (raised.value.timeout, raised.value.returncode) == (0.5, -9)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)  # the child went with it


def test_a_callback_error_wins_over_the_timeout(tmp_path: Path) -> None:
    def fail(line: str) -> None:
        time.sleep(1)  # the timeout passes meanwhile
        raise RuntimeError(line)

    with pytest.raises(RuntimeError, match="boom"):
        SubprocessRunner().stream(
            python("import time; print('boom', flush=True); time.sleep(30)"),
            cwd=tmp_path,
            on_stdout=fail,
            on_stderr=lambda _: None,
            timeout=0.3,
        )


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_stream_abandons_a_pipe_held_by_a_process_in_another_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vuterm._runner.PIPE_CLOSE_TIMEOUT", 0.5)
    command = python(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],"
        " start_new_session=True)\n"
        "print('started')"
    )
    out: list[str] = []

    started = time.monotonic()
    status = SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=lambda _: None
    )

    assert (status, out) == (0, ["started"])
    assert time.monotonic() - started < 5, "gave up on the pipe"


def test_stream_stops_the_process_when_reading_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: object, timeout: float) -> bool:
        raise ValueError("filedescriptor out of range in select()")

    monkeypatch.setattr("vuterm._runner._Poller._readable", broken)
    command = python("import time; print('x' * 100000); time.sleep(30)")

    started = time.monotonic()
    with pytest.raises(ValueError, match="out of range"):
        SubprocessRunner().stream(
            command, cwd=tmp_path, on_stdout=lambda _: None, on_stderr=lambda _: None
        )

    assert time.monotonic() - started < 10, "killed rather than left blocked"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_run_returns_when_a_helper_holds_the_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vuterm._runner.PIPE_CLOSE_TIMEOUT", 0.5)
    command = python(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],"
        " start_new_session=True)\n"
        "print('done')"
    )

    started = time.monotonic()
    completed = SubprocessRunner().run(command, cwd=tmp_path)

    assert (completed.returncode, completed.stdout) == (0, "done\n")
    assert time.monotonic() - started < 5, "gave up on the pipes"


def test_stream_delivers_everything_to_a_slow_sink_before_returning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vuterm._runner.PIPE_CLOSE_TIMEOUT", 0.3)
    command = python("print('a'); print('b'); print('c')")
    delivered: list[str] = []

    def slow(line: str) -> None:
        delivered.append(line)
        time.sleep(0.5)  # a sink that lags behind the agent, past the timeout

    SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=slow, on_stderr=lambda _: None
    )
    after_return = list(delivered)
    time.sleep(0.6)

    assert after_return == ["a", "b", "c"], "the last line, too"
    assert delivered == after_return, "nothing delivered after the return"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_an_interruption_while_waiting_kills_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "pid"
    waited = 0
    original = os.waitid

    def interrupted(*args: object, **kwargs: object) -> object:
        nonlocal waited
        waited += 1
        if waited == 1:
            while not pidfile.exists():  # once the agent is well under way
                time.sleep(0.05)
            raise KeyboardInterrupt  # Ctrl-C lands in the wait, as it does
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("os.waitid", interrupted)
    command = python(
        "import os, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(30)"
    )

    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        SubprocessRunner().stream(
            command, cwd=tmp_path, on_stdout=lambda _: None, on_stderr=lambda _: None
        )

    assert time.monotonic() - started < 10
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)  # gone, not orphaned


def test_output_after_a_long_silence_is_not_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vuterm._runner.PIPE_CLOSE_TIMEOUT", 0.3)
    command = python("import time; time.sleep(0.8); print('late')")
    out: list[str] = []

    status = SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=lambda _: None
    )
    completed = SubprocessRunner().run(command, cwd=tmp_path)

    assert (status, out) == (0, ["late"])
    assert completed.stdout == "late\n"


def test_stream_fails_cleanly_when_a_reader_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = 0
    original = threading.Thread.start

    def start_one_only(self: threading.Thread) -> None:
        nonlocal starts
        starts += 1
        if starts == 2:
            raise RuntimeError("can't start new thread")
        original(self)

    monkeypatch.setattr("vuterm._runner._Reader.start", start_one_only)
    command = python("import sys, time; print('x' * 200000); time.sleep(30)")
    open_fds = len(os.listdir("/proc/self/fd")) if Path("/proc/self/fd").exists() else 0

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="can't start new thread"):
        SubprocessRunner().stream(
            command, cwd=tmp_path, on_stdout=lambda _: None, on_stderr=lambda _: None
        )

    assert time.monotonic() - started < 10, "the process was stopped, not waited for"
    if open_fds:
        assert len(os.listdir("/proc/self/fd")) == open_fds, "no descriptor leaked"


def test_the_group_is_never_signalled_after_the_process_was_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vuterm._runner import _kill_group

    sent: list[int] = []
    monkeypatch.setattr("os.killpg", lambda pid, sig: sent.append(pid))
    process = subprocess.Popen(python("pass"), start_new_session=True)
    process.wait()

    _kill_group(process)  # as a lingering reader thread would

    assert sent == []


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_stream_leaves_a_pipe_a_stranger_keeps_writing_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vuterm._runner.PIPE_CLOSE_TIMEOUT", 0.5)
    chatter = (
        "import time\n"
        "for i in range(300): print('chatter', flush=True); time.sleep(0.1)"
    )
    command = python(
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {chatter!r}],"
        " start_new_session=True)\n"
        "print('started')"
    )
    out: list[str] = []

    started = time.monotonic()
    status = SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=lambda _: None
    )

    assert (status, out[0]) == (0, "started")
    assert time.monotonic() - started < 5, "not kept alive by the chatter"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
def test_stream_leaves_a_pipe_a_stranger_never_stops_filling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vuterm._runner.PIPE_CLOSE_TIMEOUT", 0.5)
    flood = "while True: print('x' * 80, flush=True)"  # dies of EPIPE when left
    command = python(
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {flood!r}],"
        " start_new_session=True, stderr=subprocess.DEVNULL)\n"
        "print('started')"
    )
    out: list[str] = []

    started = time.monotonic()
    status = SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=lambda _: None
    )

    assert (status, out[0]) == (0, "started")
    assert time.monotonic() - started < 5, "not kept alive by the flood"


def test_stream_delivers_a_last_line_without_a_newline(tmp_path: Path) -> None:
    command = python("import sys; sys.stdout.write('a\\r\\nb'); sys.stderr.write('c')")
    out: list[str] = []
    err: list[str] = []

    SubprocessRunner().stream(
        command, cwd=tmp_path, on_stdout=out.append, on_stderr=err.append
    )

    assert (out, err) == (["a", "b"], ["c"])


def test_a_missing_executable_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SubprocessRunner().run(["vuterm-no-such-command"], cwd=tmp_path)
    with pytest.raises(FileNotFoundError):
        SubprocessRunner().stream(
            ["vuterm-no-such-command"],
            cwd=tmp_path,
            on_stdout=lambda _: None,
            on_stderr=lambda _: None,
        )
