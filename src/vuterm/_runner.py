# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Runs terminal commands; the only place vuterm starts processes."""

import codecs
import contextlib
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

OnLine = Callable[[str], None]

# How long to wait for the output pipes to close after the agent and its
# process group are gone; a descendant in a session of its own could hold them.
PIPE_CLOSE_TIMEOUT = 5.0
# How often a reader checks whether it was told to stop.
POLL_INTERVAL = 0.1
READ_SIZE = 64 * 1024
# The most a stopped reader still takes from its pipe: what the process
# could have left there, which is at most the pipe's capacity.
DRAIN_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class CompletedCommand:
    """A command that ran to completion."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(ABC):
    """Runs non-interactive commands: nothing is written to their stdin.

    Inject a fake implementation to test without running ``git``, ``gh`` or
    agent CLIs.
    """

    @abstractmethod
    def run(self, args: Sequence[str], *, cwd: Path) -> CompletedCommand:
        """Run `args` in `cwd` and wait for it to exit, capturing its output.

        For short commands such as ``git`` and ``gh``. A non-zero exit status is
        returned, not raised.
        """

    @abstractmethod
    def stream(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        on_stdout: OnLine,
        on_stderr: OnLine,
    ) -> int:
        """Run `args` in `cwd`, passing each output line on as soon as it is written.

        For long-running agents. Returns the exit status; a non-zero one is not
        raised.
        """


class SubprocessRunner(CommandRunner):
    """Runs commands with `subprocess`.

    Output is decoded as UTF-8, with undecodable bytes replaced. A command
    that is not installed raises `FileNotFoundError`, as `subprocess` does.

    Nothing here has a terminal to answer on, so `run` tells git and ssh not
    to ask, and runs the command in a session with no terminal: a missing
    credential fails the command instead of hanging it. The caller's own
    settings of those variables win.

    A streamed command runs in a process group of its own. The run is over
    when the command exits: whatever it left running is stopped then, so
    nothing outlives a run. Everything the command wrote is delivered, at
    the pace of the callbacks; an output pipe that a process outside the
    group holds open is read for `PIPE_CLOSE_TIMEOUT` more, then left to
    it. The group is also stopped when a callback raises or the caller is
    interrupted, and the exception is raised to the caller. No callback is
    called once `stream` has returned.

    Windows has no process groups or pipe polling of this kind: there, only
    the command itself is stopped, and a helper it leaves holding a pipe
    keeps a reader thread alive, unheard, until it exits.
    """

    def run(self, args: Sequence[str], *, cwd: Path) -> CompletedCommand:
        # The same machinery as `stream`, collecting instead of passing on:
        # a helper git or ssh leaves behind cannot hold the pipes open either.
        with _start(args, cwd=cwd, env=_no_prompts()) as process:
            stdout, stderr = _readers(process)
            returncode = _finish(process, [stdout, stderr])
        return CompletedCommand(
            args=tuple(args),
            returncode=returncode,
            stdout=stdout.text,
            stderr=stderr.text,
        )

    def stream(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        on_stdout: OnLine,
        on_stderr: OnLine,
    ) -> int:
        with _start(args, cwd=cwd) as process:
            readers = _readers(process, on_stdout, on_stderr)
            return _finish(process, list(readers))


def _start(
    args: Sequence[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )


def _readers(
    process: subprocess.Popen[bytes],
    on_stdout: OnLine | None = None,
    on_stderr: OnLine | None = None,
) -> tuple["_Reader", "_Reader"]:
    """Start a reader on each pipe; if that fails part way, leave nothing
    behind: a pipe nobody reads would block the process for good.
    """
    assert process.stdout is not None and process.stderr is not None

    def stop() -> None:
        _kill_group(process)

    readers: list[_Reader] = []
    try:
        for pipe, on_line in ((process.stdout, on_stdout), (process.stderr, on_stderr)):
            reader = _Reader(pipe.fileno(), stop, on_line=on_line)
            readers.append(reader)
            reader.start()
    except BaseException:
        _kill_group(process)
        for reader in readers:
            reader.stop()
            if reader.ident is None:  # never started: nothing will close its fd
                reader.discard()
            else:
                reader.join(PIPE_CLOSE_TIMEOUT)
        raise
    return readers[0], readers[1]


def _finish(process: subprocess.Popen[bytes], readers: list["_Reader"]) -> int:
    """Wait for the process, stop its group, and collect the readers.

    Once the process has ended, its pipes are read for `PIPE_CLOSE_TIMEOUT`
    more, and what was read is delivered however slow the sink: the process
    wrote everything before it ended. A pipe a process outside the group
    holds open is abandoned after that, whether silent or still written to.
    An interruption while waiting kills the group and silences the readers
    at once; the output is lost, and the interruption is raised.
    """
    try:
        _wait_and_kill_group(process)
    except BaseException:
        _kill_group(process)
        for reader in readers:
            reader.abandon()
        process.wait()
        for reader in readers:
            reader.join(PIPE_CLOSE_TIMEOUT)
        raise
    deadline = time.monotonic() + PIPE_CLOSE_TIMEOUT
    for reader in readers:
        reader.mark_active()  # the wait for data starts now, not at launch
        while reader.is_alive():
            reader.join(POLL_INTERVAL)
            if not reader.is_alive():
                break
            if time.monotonic() > deadline:
                reader.stop()  # read no more; deliver what was read
            if reader.idle_for() > PIPE_CLOSE_TIMEOUT:
                # Nothing to deliver and no data: a held pipe. Bounded, as a
                # reader blocked in a read, which polling prevents on POSIX
                # only, would otherwise hold us here.
                reader.abandon()
                reader.join(PIPE_CLOSE_TIMEOUT)
                break
    for reader in readers:
        if reader.error is not None:
            raise reader.error
    return process.returncode


def _wait_and_kill_group(process: subprocess.Popen[bytes]) -> None:
    """Wait for the process to exit, kill what it left in its group, and
    only then reap it: once reaped, its id could belong to someone else.
    """
    if hasattr(os, "waitid"):
        # Wait without reaping: the id stays the exited process's own.
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        _kill_group(process)
    process.wait()


def _no_prompts() -> dict[str, str]:
    """The environment for git and gh, with prompts off unless the caller
    says otherwise. The ssh command itself is left to git's configuration.
    """
    return {"GIT_TERMINAL_PROMPT": "0", "SSH_ASKPASS_REQUIRE": "never", **os.environ}


class _Reader(threading.Thread):
    """Reads a pipe until it closes or `stop`: each line to a callback, or
    everything into `text` when there is no callback.

    Reads a duplicate of the raw descriptor, its own to close when it ends,
    checking between reads whether to stop, so a process that keeps the pipe
    open cannot keep this thread alive, and a thread that lives on never
    reads a descriptor number the runner has closed and something else
    reuses. Once stopped, it calls back no more. A callback that raises
    stops the process, and its exception is kept for the caller; the pipe is
    drained to the end either way, so the process is never blocked on a full
    pipe. A failure of the reading itself also stops the process, since
    nobody would drain the pipe any more.
    """

    def __init__(
        self,
        fd: int,
        stop_process: Callable[[], None],
        *,
        on_line: OnLine | None = None,
    ):
        super().__init__(daemon=True)
        self._fd = os.dup(fd)
        self._on_line = on_line
        self._stop_process = stop_process
        self._stopped = threading.Event()
        self._silenced = threading.Event()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending = ""
        self._chunks: list[str] = []
        self._last_read = time.monotonic()
        self._delivering = False
        self.error: BaseException | None = None

    @property
    def text(self) -> str:
        """Everything read, when there is no callback."""
        return "".join(self._chunks)

    def stop(self) -> None:
        """Read no more than is already there, `DRAIN_LIMIT` at most; deliver
        that, then end.
        """
        self._stopped.set()

    def abandon(self) -> None:
        """Read no more and call back no more."""
        self._silenced.set()
        self._stopped.set()

    def discard(self) -> None:
        """Give the descriptor back, for a reader that never ran."""
        os.close(self._fd)

    def mark_active(self) -> None:
        """Count idleness from now on."""
        self._last_read = time.monotonic()

    def idle_for(self) -> float:
        """Seconds since the pipe last gave data or the last delivery ended,
        or 0 while delivering.
        """
        if self._delivering:
            return 0.0
        return time.monotonic() - self._last_read

    def run(self) -> None:
        try:
            with _Poller(self._fd) as readable:
                while not self._stopped.is_set():
                    if readable(POLL_INTERVAL) and self._read() is None:
                        return
                # Stopped: what is in the pipe already, without waiting, and
                # no more than a pipe can hold: past that it is being fed.
                budget = DRAIN_LIMIT
                while not self._silenced.is_set() and budget > 0 and readable(0):
                    size = self._read()
                    if size is None:
                        return
                    budget -= size
        except BaseException as error:
            if self.error is None:
                self.error = error
            self._stop_process()
        finally:
            os.close(self._fd)

    def _read(self) -> int | None:
        """Read and deliver a chunk: its size, or None at the end of the pipe."""
        chunk = os.read(self._fd, READ_SIZE)
        self._last_read = time.monotonic()
        if not chunk:
            self._deliver(self._decoder.decode(b"", final=True), final=True)
            return None
        self._deliver(self._decoder.decode(chunk))
        return len(chunk)

    def _deliver(self, text: str, *, final: bool = False) -> None:
        if self._on_line is None:
            self._chunks.append(text)
            return
        self._pending += text
        *lines, self._pending = self._pending.split("\n")
        if final and self._pending:
            lines.append(self._pending)
            self._pending = ""
        self._delivering = True
        try:
            for line in lines:
                self._call(line.rstrip("\r"))
        finally:
            self._last_read = time.monotonic()
            self._delivering = False

    def _call(self, line: str) -> None:
        if self.error is not None or self._on_line is None or self._silenced.is_set():
            return
        try:
            self._on_line(line)
        except BaseException as error:
            self.error = error
            self._stop_process()


class _Poller(contextlib.AbstractContextManager["Callable[[float], bool]"]):
    """Waits for a descriptor to be readable, with no limit on its number.

    Windows can poll sockets only: there, every wait says yes, and the read
    blocks instead.
    """

    def __init__(self, fd: int) -> None:
        self._selector = (
            None if sys.platform == "win32" else selectors.DefaultSelector()
        )
        if self._selector is not None:
            self._selector.register(fd, selectors.EVENT_READ)

    def __enter__(self) -> Callable[[float], bool]:
        return self._readable

    def __exit__(self, *_: object) -> None:
        if self._selector is not None:
            self._selector.close()

    def _readable(self, timeout: float) -> bool:
        if self._selector is None:
            return True
        return bool(self._selector.select(timeout))


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the process and everything else in its process group.

    Never after the process was reaped: its id may have been given to
    another process by then, and the group is signalled by that id.
    """
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if hasattr(os, "killpg"):
            os.killpg(process.pid, signal.SIGKILL)
        else:  # Windows has no process groups of this kind.
            process.kill()
