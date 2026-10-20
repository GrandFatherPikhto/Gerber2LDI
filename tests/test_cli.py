"""The CLI entry point starts, prints help and its version."""
import pytest

from gerber2ldi.cli import main


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: gerber2ldi" in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("gerber2ldi ")
