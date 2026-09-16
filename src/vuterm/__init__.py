# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""A common interface to run terminal-based code agents."""

from importlib.metadata import PackageNotFoundError, version

from vuterm._client import Client
from vuterm._errors import (
    CommandError,
    CommandTimeoutError,
    NoWorkspaceAvailableError,
    VutermError,
)
from vuterm._git import Git
from vuterm._github import GitHub
from vuterm._results import AgentResult
from vuterm._runner import CommandRunner, CompletedCommand, SubprocessRunner

try:
    __version__ = version("vuterm")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = [
    "AgentResult",
    "Client",
    "CommandError",
    "CommandRunner",
    "CommandTimeoutError",
    "CompletedCommand",
    "Git",
    "GitHub",
    "NoWorkspaceAvailableError",
    "SubprocessRunner",
    "VutermError",
    "__version__",
]
