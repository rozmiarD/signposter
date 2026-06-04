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
    normalized = " ".join(output.split())

    assert exc_info.value.code == 0
    assert "usage: signposter" in output
    assert "Operator status:" in output
    assert "signposter control-plane status --repo OWNER/REPO" in output
    assert "current task, next task, and stop reason" in normalized
    assert "Bare `signposter` remains help-only" in output


def test_handoff_snapshot_help_lists_manifest_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["signposter", "handoff", "snapshot", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        main()

    output = capsys.readouterr().out

    assert exc_info.value.code == 0
    assert "usage: signposter handoff snapshot" in output
    assert "--manifest" in output
    assert "--sync-github" in output


def test_handoff_snapshot_blocks_missing_manifest(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "handoff",
            "snapshot",
            "--repo",
            "ExatronOmega/signposter",
            "--manifest",
            str(missing),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()
    output = capsys.readouterr().out

    assert exc_info.value.code == 1
    assert "Signposter Handoff Snapshot" in output
    assert "Status:\n  blocked" in output
    assert f"manifest file not found: {missing}" in output
    assert "No GitHub mutation was performed." in output
    assert "No backend execution was performed." in output
