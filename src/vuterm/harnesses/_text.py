# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""Helpers shared by harnesses to write log messages."""

# Tool inputs and results can hold whole files; log only their start.
MAX_TOOL_LOG_CHARS = 300


def truncate(text: str) -> str:
    """Shorten `text` to `MAX_TOOL_LOG_CHARS`, marking the cut with an ellipsis."""
    if len(text) <= MAX_TOOL_LOG_CHARS:
        return text
    return text[:MAX_TOOL_LOG_CHARS] + "…"
