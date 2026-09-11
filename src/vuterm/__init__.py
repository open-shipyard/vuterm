# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

"""A common interface to run terminal-based code agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vuterm")
except PackageNotFoundError:  # a source checkout on sys.path, not installed
    __version__ = "0+unknown"

__all__ = ["__version__"]
