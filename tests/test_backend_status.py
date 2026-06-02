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

    assert report.default_backend == "codex-cli"
    assert [backend.name for backend in report.backends] == ["openclaw", "codex-cli"]
    assert report.backends[0].status == "ready"
    assert report.backends[0].reason.startswith("legacy fallback:")
    assert report.backends[1].status == "ready"
    assert report.fallback_order == ("codex-cli", "openclaw")
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


def test_backend_status_format_is_bounded_and_read_only() -> None:
    report = build_backend_status_report(
        default_backend="codex-cli",
        which_command=lambda _: None,
        openclaw_check=lambda **kwargs: Preflight(),
    )
    out = format_backend_status_report(report)

    assert "Signposter Backend Status" in out
    assert "Default backend: codex-cli" in out
    assert "Fallback order: codex-cli -> openclaw" in out
    assert "reason: legacy fallback:" in out
    assert "Audit:" in out
    assert "current default backend: codex-cli" in out
    assert "codex cli support: blocked" in out
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
