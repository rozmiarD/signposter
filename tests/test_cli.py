from __future__ import annotations

import sys

import pytest

from signposter.cli import main


def test_plain_signposter_remains_help_only_with_status_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["signposter"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    output = capsys.readouterr().out

    assert exc_info.value.code == 0
    assert "usage: signposter" in output
    assert "Operator status:" in output
    assert "signposter control-plane status --repo OWNER/REPO" in output
    assert "Bare `signposter` remains help-only" in output
