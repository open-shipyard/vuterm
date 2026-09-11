# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Errors raised by vuterm."""


class VutermError(Exception):
    """Base class for all errors raised by this library."""


class CommandError(VutermError):
    """A ``git`` or ``gh`` command exited with a non-zero status."""

    def __init__(self, message: str, *, returncode: int, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


class NoWorkspaceAvailableError(VutermError):
    """Every workspace is reserved and no more can be created."""
