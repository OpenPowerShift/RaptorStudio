"""The modal guard in `tests/conftest.py` covers a GUI module's module-scoped
fixtures, not only its tests: a blocking dialog opened while such a fixture
is set up fails at once instead of waiting for a click forever."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tests.conftest import UnansweredModal  # noqa: E402


@pytest.fixture(scope="module")
def blocked_in_setup():
    QApplication.instance() or QApplication([])
    try:
        QMessageBox.warning(None, "From a module fixture", "nobody will answer")
    except UnansweredModal as exc:
        return exc
    return None


def test_a_blocking_dialog_in_module_setup_is_refused(blocked_in_setup):
    assert isinstance(blocked_in_setup, UnansweredModal)
    assert "From a module fixture" in str(blocked_in_setup)
