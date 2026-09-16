# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Outcome of an agent run."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentResult:
    """Whether the agent completed successfully or errored, and its response.

    An agent that errors is reported here rather than raised.
    """

    success: bool
    response: str | None = None
    """The agent's final message, as its CLI reports it, or None if it gave
    none. An agent that errored may still have one, such as the error it met.
    """
    timed_out: bool = False
    """Whether the agent was stopped for running past its timeout; it did not
    succeed then.
    """
