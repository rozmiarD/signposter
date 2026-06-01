from __future__ import annotations

from signposter.backend_status import (
    build_backend_status_report,
    format_backend_status_report,
)


class Preflight:
    ok = True
    reason = "OpenClaw ready"
    openclaw_path = "/usr/bin/openclaw"


def test_backend_status_reports_openclaw_and_codex_cli() -> None:
    report = build_backend_status_report(
        which_command=lambda name: "/usr/bin/codex" if name == "codex" else None,
        openclaw_check=lambda **kwargs: Preflight(),
    )

    assert report.default_backend == "openclaw"
    assert [backend.name for backend in report.backends] == ["openclaw", "codex-cli"]
    assert report.backends[0].status == "ready"
    assert report.backends[1].status == "ready"
    assert report.fallback_order == ("openclaw", "codex-cli")


def test_backend_status_reports_missing_codex_cli() -> None:
    report = build_backend_status_report(
        which_command=lambda _: None,
        openclaw_check=lambda **kwargs: Preflight(),
    )

    codex = report.backends[1]
    assert codex.name == "codex-cli"
    assert codex.status == "blocked"
    assert "not found" in codex.reason


def test_backend_status_format_is_bounded_and_read_only() -> None:
    report = build_backend_status_report(
        default_backend="codex-cli",
        which_command=lambda _: None,
        openclaw_check=lambda **kwargs: Preflight(),
    )
    out = format_backend_status_report(report)

    assert "Signposter Backend Status" in out
    assert "Default backend: codex-cli" in out
    assert "Fallback order: openclaw -> codex-cli" in out
    assert "No prompt was executed." in out
    assert "No GitHub mutation was performed." in out
    assert "signposter artifact record-bug" in out
