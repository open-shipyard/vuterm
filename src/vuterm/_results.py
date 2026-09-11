# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Outcome of an agent run."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentResult:
    """Whether the agent completed successfully or errored.

    An agent that errors is reported here rather than raised.
    """

    success: bool
