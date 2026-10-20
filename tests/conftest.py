"""Suite-wide fixtures."""
import pytest

import gerber2ldi.i18n as i18n


@pytest.fixture(autouse=True)
def _restore_translation(monkeypatch):
    """setup_i18n() (called by the CLI's main() too) swaps the process-wide
    translation; put it back after every test so a language never leaks into
    the next test of the same worker under `-n auto`."""
    monkeypatch.setattr(i18n, "_translation", i18n._translation)
