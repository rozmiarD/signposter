from __future__ import annotations

from signposter.backend_status import (
    build_backend_status_report,
    format_backend_status_report,
)


class Preflight:
    ok = True
    reason = "OpenClaw ready"
    openclaw_path = "/usr/bin/openclaw"


def test_backend_status_reports_openclaw_and_codex_cli(tmp_path) -> None:
    report = build_backend_status_report(
        which_command=lambda name: "/usr/bin/codex" if name == "codex" else None,
        openclaw_check=lambda **kwargs: Preflight(),
        runs_dir=tmp_path / "runs",
    )

    assert report.default_backend == "codex-cli"
    assert [backend.name for backend in report.backends] == ["openclaw", "codex-cli"]
    assert report.backends[0].status == "ready"
    assert report.backends[0].reason.startswith("legacy fallback:")
    assert report.backends[1].status == "ready"
    assert report.fallback_order == ("codex-cli", "openclaw")
    assert report.runtime_diagnostics == ()
    assert report.runtime_diagnostics_status == "no local runtime blockers found"
    assert "signposter.role_policy: role/model/reasoning registry" in report.source_modules
    assert "signposter.role_routing: deterministic stage-to-role routing" in report.source_modules
    assert "signposter run --backend {openclaw,codex-cli}" in report.command_surfaces


def test_backend_status_reports_missing_codex_cli() -> None:
    report = build_backend_status_report(
        which_command=lambda _: None,
        openclaw_check=lambda **kwargs: Preflight(),
    )

    codex = report.backends[1]
    assert codex.name == "codex-cli"
    assert codex.status == "blocked"
    assert "not found" in codex.reason


def test_backend_status_format_is_bounded_and_read_only(tmp_path) -> None:
    report = build_backend_status_report(
        default_backend="codex-cli",
        which_command=lambda _: None,
        openclaw_check=lambda **kwargs: Preflight(),
        runs_dir=tmp_path / "runs",
    )
    out = format_backend_status_report(report)

    assert "Signposter Backend Status" in out
    assert "Default backend: codex-cli" in out
    assert "Fallback order: codex-cli -> openclaw" in out
    assert "Compact summary:" in out
    assert "default: codex-cli" in out
    assert "runtime diagnostics shown: 0" in out
    assert "reason: legacy fallback:" in out
    assert "Audit:" in out
    assert "current default backend: codex-cli" in out
    assert "codex cli support: blocked" in out
    assert "runtime availability: no local runtime blockers found" in out
    assert "Runtime availability diagnostics:" in out
    assert "  none" in out
    assert "Source modules:" in out
    assert "signposter.role_policy: role/model/reasoning registry" in out
    assert "Command surfaces:" in out
    assert "signposter review execute --backend {openclaw,codex-cli}" in out
    assert "No prompt was executed." in out
    assert "No GitHub mutation was performed." in out
    assert "signposter artifact record-bug" in out


def test_backend_status_format_surfaces_current_codex_cli_default() -> None:
    report = build_backend_status_report(
        which_command=lambda name: "/usr/bin/codex" if name == "codex" else None,
        openclaw_check=lambda **kwargs: Preflight(),
    )
    out = format_backend_status_report(report)

    assert "Default backend: codex-cli" in out
    assert "current default backend: codex-cli" in out
    assert "codex cli support: ready" in out


def test_backend_status_reports_recent_runtime_availability_diagnostics(tmp_path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    summary = runs_dir / "issue-1-worker.summary.md"
    summary_text = (
        "\n".join(
            [
                "# Signposter Codex CLI Execution Summary",
                "",
                "**Backend:** codex-cli",
                "**Agent:** codex_worker_core",
                "**Model:** openai/gpt-5.4",
                "**Requested Reasoning:** medium",
                "**Exit Code:** 1",
                "**Status:** unsupported-model",
                "",
                "Reason: Codex CLI exited with code 1; classified as unsupported-model.",
            ]
        )
    )
    summary.write_text(
        summary_text,
        encoding="utf-8",
    )
    (runs_dir / "issue-1-worker.codex-runtime.summary.md").write_text(
        summary_text,
        encoding="utf-8",
    )

    report = build_backend_status_report(
        which_command=lambda name: "/usr/bin/codex" if name == "codex" else None,
        openclaw_check=lambda **kwargs: Preflight(),
        runs_dir=runs_dir,
    )
    out = format_backend_status_report(report)

    assert report.runtime_diagnostics_status == "warnings"
    assert len(report.runtime_diagnostics) == 1
    diagnostic = report.runtime_diagnostics[0]
    assert diagnostic.backend == "codex-cli"
    assert diagnostic.agent == "codex_worker_core"
    assert diagnostic.model == "openai/gpt-5.4"
    assert diagnostic.status == "unsupported-model"
    assert "runtime availability: warnings" in out
    assert "runtime diagnostics shown: 1" in out
    assert "- unsupported-model" in out
    assert "model: openai/gpt-5.4" in out
    assert diagnostic.artifact_path in {
        str(summary),
        str(runs_dir / "issue-1-worker.codex-runtime.summary.md"),
    }
    assert diagnostic.artifact_path in out
    assert "No prompt was executed." in out
    assert "No model tokens were consumed." in out
