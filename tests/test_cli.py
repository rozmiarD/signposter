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


def test_backend_status_cli_renders_compact_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from signposter.backend_status import (
        BackendHealth,
        BackendStatusReport,
        RuntimeAvailabilityDiagnostic,
    )

    def fake_report(**kwargs):
        assert kwargs["default_backend"] == "codex-cli"
        assert kwargs["diagnostic_limit"] == 2
        return BackendStatusReport(
            default_backend="codex-cli",
            backends=(
                BackendHealth(
                    name="openclaw",
                    selected_default=False,
                    execution_supported=True,
                    status="blocked",
                    reason="legacy fallback not ready",
                ),
                BackendHealth(
                    name="codex-cli",
                    selected_default=True,
                    execution_supported=True,
                    status="ready",
                    reason="codex CLI binary found on PATH",
                    command_path="/usr/bin/codex",
                ),
            ),
            fallback_order=("codex-cli", "openclaw"),
            runtime_diagnostics=(
                RuntimeAvailabilityDiagnostic(
                    artifact_path="artifacts/runs/issue-1-worker.summary.md",
                    backend="codex-cli",
                    agent="codex_worker_core",
                    model="openai/gpt-5.4",
                    status="unsupported-model",
                    reason="runtime blocker",
                ),
            ),
            runtime_diagnostics_status="warnings",
            source_modules=("signposter.execution_backend",),
            command_surfaces=("signposter backend status",),
            notes=("No GitHub mutation was performed.",),
        )

    monkeypatch.setattr("signposter.cli.build_backend_status_report", fake_report)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "signposter",
            "backend",
            "status",
            "--default",
            "codex-cli",
            "--diagnostic-limit",
            "2",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()
    output = capsys.readouterr().out

    assert exc_info.value.code == 0
    assert "Compact summary:" in output
    assert "default: codex-cli" in output
    assert "codex-cli: ready" in output
    assert "openclaw: blocked" in output
    assert "runtime diagnostics shown: 1" in output
    assert "No GitHub mutation was performed." in output


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
