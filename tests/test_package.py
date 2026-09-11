# Copyright 2026 The vuterm Authors
# SPDX-License-Identifier: Apache-2.0

import importlib
import importlib.metadata
from importlib.metadata import PackageNotFoundError, version

import pytest

import vuterm


def test_version_matches_distribution_metadata() -> None:
    assert vuterm.__version__ == version("vuterm")


def test_imports_without_distribution_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_installed(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_installed)
    try:
        assert importlib.reload(vuterm).__version__ == "0+unknown"
    finally:
        monkeypatch.undo()
        importlib.reload(vuterm)
