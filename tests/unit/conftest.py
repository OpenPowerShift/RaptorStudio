"""Shared fixtures for the unit tier.

The characterisation suites all expose a module-level ``observe()`` and
need the same module-scoped, offscreen snapshot. Declaring it once here
keeps eight copies of the fixture out of the suites themselves.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def observed(request):
    """The module's own ``observe()`` result, with Qt headless."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return request.module.observe()
