"""Read-only execution backend status and fallback visibility."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from shutil import which

from signposter.execution_backend import (
    DEFAULT_EXECUTION_BACKEND,
    resolve_execution_backend,
)
from signposter.openclaw_preflight import OpenClawPreflightResult, check_openclaw_preflight

WhichCommand = Callable[[str], str | None]
OpenClawCheck = Callable[..., OpenClawPreflightResult]


@dataclass(frozen=True)
class BackendHealth:
    """One backend's read-only health signal."""

    name: str
    selected_default: bool
    execution_supported: bool
    status: str
    reason: str
    command_path: str | None = None


@dataclass(frozen=True)
class BackendStatusReport:
    """Read-only backend status report."""

    default_backend: str
    backends: tuple[BackendHealth, ...]
    fallback_order: tuple[str, ...]
    source_modules: tuple[str, ...]
    command_surfaces: tuple[str, ...]
    notes: tuple[str, ...]


def build_backend_status_report(
    *,
    default_backend: str | None = None,
    which_command: WhichCommand = which,
    openclaw_check: OpenClawCheck = check_openclaw_preflight,
) -> BackendStatusReport:
    """Build a bounded read-only backend status report."""
    selected = resolve_execution_backend(default_backend).backend
    openclaw = _openclaw_health(selected, openclaw_check=openclaw_check)
    codex = _codex_cli_health(selected, which_command=which_command)
    return BackendStatusReport(
        default_backend=selected or DEFAULT_EXECUTION_BACKEND,
        backends=(openclaw, codex),
        fallback_order=("codex-cli", "openclaw"),
        source_modules=(
            "signposter.execution_backend: backend resolution and command shape",
            "signposter.codex_cli_backend: Codex CLI execution adapter",
            "signposter.openclaw_runtime: legacy OpenClaw execution adapter",
            "signposter.role_policy: role/model/reasoning registry",
            "signposter.role_routing: deterministic stage-to-role routing",
        ),
        command_surfaces=(
            "signposter run --backend {openclaw,codex-cli}",
            "signposter review execute --backend {openclaw,codex-cli}",
            "signposter roles status",
            "signposter roles validate",
            "signposter backend status",
        ),
        notes=(
            "Read-only backend status only.",
            "No prompt was executed.",
            "No model tokens were consumed.",
            "No GitHub mutation was performed.",
            "Fallback must be explicit and visible; no silent backend switch is allowed.",
        ),
    )


def _openclaw_health(
    selected_default: str,
    *,
    openclaw_check: OpenClawCheck,
) -> BackendHealth:
    try:
        preflight = openclaw_check(artifact_kind="backend-status", target=0)
    except Exception as exc:
        return BackendHealth(
            name="openclaw",
            selected_default=selected_default == "openclaw",
            execution_supported=True,
            status="blocked",
            reason=f"OpenClaw preflight raised: {exc}",
        )
    return BackendHealth(
        name="openclaw",
        selected_default=selected_default == "openclaw",
        execution_supported=True,
        status="ready" if preflight.ok else "blocked",
        reason=preflight.reason,
        command_path=preflight.openclaw_path,
    )


def _codex_cli_health(
    selected_default: str,
    *,
    which_command: WhichCommand,
) -> BackendHealth:
    command_path = which_command("codex")
    if command_path:
        return BackendHealth(
            name="codex-cli",
            selected_default=selected_default == "codex-cli",
            execution_supported=True,
            status="ready",
            reason="codex CLI binary found on PATH",
            command_path=command_path,
        )
    return BackendHealth(
        name="codex-cli",
        selected_default=selected_default == "codex-cli",
        execution_supported=True,
        status="blocked",
        reason="codex CLI binary was not found on PATH",
    )


def format_backend_status_report(report: BackendStatusReport) -> str:
    """Render compact operator-facing backend status."""
    lines = [
        "Signposter Backend Status",
        "",
        f"Default backend: {report.default_backend}",
        "Fallback order: " + " -> ".join(report.fallback_order),
        "",
        "Backends:",
    ]
    for backend in report.backends:
        lines.extend(
            [
                f"- {backend.name}",
                f"  default: {'yes' if backend.selected_default else 'no'}",
                f"  execution_supported: {'yes' if backend.execution_supported else 'no'}",
                f"  status: {backend.status}",
                f"  reason: {backend.reason}",
                f"  command_path: {backend.command_path or 'missing'}",
            ]
        )
    lines.extend(
        [
            "",
            "Audit:",
            f"  current default backend: {report.default_backend}",
            "  codex cli support: "
            + _backend_status_by_name(report.backends, "codex-cli"),
            "",
            "Source modules:",
        ]
    )
    lines.extend(f"  {module}" for module in report.source_modules)
    lines.extend(
        [
            "",
            "Command surfaces:",
        ]
    )
    lines.extend(f"  {surface}" for surface in report.command_surfaces)
    lines.extend(
        [
            "",
            "Fallback reporting:",
            "  signposter artifact record-bug --summary \"backend failure\" --status open --apply",
            "",
            "Notes:",
        ]
    )
    lines.extend(f"  {note}" for note in report.notes)
    return "\n".join(lines)


def _backend_status_by_name(backends: tuple[BackendHealth, ...], name: str) -> str:
    for backend in backends:
        if backend.name == name:
            return backend.status
    return "unknown"
