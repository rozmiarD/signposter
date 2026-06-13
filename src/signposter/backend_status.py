"""Read-only execution backend status and fallback visibility."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from signposter.execution_backend import (
    DEFAULT_EXECUTION_BACKEND,
    resolve_execution_backend,
)
from signposter.openclaw_preflight import OpenClawPreflightResult, check_openclaw_preflight

WhichCommand = Callable[[str], str | None]
OpenClawCheck = Callable[..., OpenClawPreflightResult]
RUNTIME_BLOCKER_STATUSES = frozenset(
    {
        "unsupported-model",
        "missing-binary",
        "missing-prompt",
        "timeout",
        "runtime-stall",
        "runtime-error",
        "malformed-output",
    }
)


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
class RuntimeAvailabilityDiagnostic:
    """Recent local runtime evidence for model/backend availability."""

    artifact_path: str
    backend: str
    agent: str
    model: str
    status: str
    reason: str


@dataclass(frozen=True)
class BackendStatusReport:
    """Read-only backend status report."""

    default_backend: str
    backends: tuple[BackendHealth, ...]
    fallback_order: tuple[str, ...]
    runtime_diagnostics: tuple[RuntimeAvailabilityDiagnostic, ...]
    runtime_diagnostics_status: str
    source_modules: tuple[str, ...]
    command_surfaces: tuple[str, ...]
    notes: tuple[str, ...]


def build_backend_status_report(
    *,
    default_backend: str | None = None,
    which_command: WhichCommand = which,
    openclaw_check: OpenClawCheck = check_openclaw_preflight,
    runs_dir: str | Path = "artifacts/runs",
    diagnostic_limit: int = 5,
) -> BackendStatusReport:
    """Build a bounded read-only backend status report."""
    selected = resolve_execution_backend(default_backend).backend
    openclaw = _openclaw_health(selected, openclaw_check=openclaw_check)
    codex = _codex_cli_health(selected, which_command=which_command)
    runtime_diagnostics = _load_runtime_availability_diagnostics(
        runs_dir=Path(runs_dir),
        limit=diagnostic_limit,
    )
    return BackendStatusReport(
        default_backend=selected or DEFAULT_EXECUTION_BACKEND,
        backends=(openclaw, codex),
        fallback_order=(),
        runtime_diagnostics=runtime_diagnostics,
        runtime_diagnostics_status=(
            "warnings" if runtime_diagnostics else "no local runtime blockers found"
        ),
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
            "Automatic backend fallback is disabled; persistent failures require pilot takeover.",
        ),
    )


def _summary_field(text: str, field: str) -> str | None:
    pattern = re.compile(rf"^\*\*{re.escape(field)}:\*\*\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    pattern = re.compile(rf"^{re.escape(field)}:\s*(.+?)\s*$", re.MULTILINE)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return None


def _load_runtime_availability_diagnostics(
    *,
    runs_dir: Path,
    limit: int,
) -> tuple[RuntimeAvailabilityDiagnostic, ...]:
    if limit <= 0 or not runs_dir.exists():
        return ()
    candidates = sorted(
        runs_dir.glob("*.summary.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    diagnostics: list[RuntimeAvailabilityDiagnostic] = []
    seen_artifact_keys: set[str] = set()
    for path in candidates:
        artifact_key = path.name.replace(".codex-runtime.summary.md", ".summary.md")
        if artifact_key in seen_artifact_keys:
            continue
        seen_artifact_keys.add(artifact_key)
        try:
            text = path.read_text(encoding="utf-8")[:12000]
        except OSError:
            continue
        status = (_summary_field(text, "Status") or "").strip()
        if status not in RUNTIME_BLOCKER_STATUSES:
            continue
        diagnostics.append(
            RuntimeAvailabilityDiagnostic(
                artifact_path=str(path),
                backend=_summary_field(text, "Backend") or "unknown",
                agent=_summary_field(text, "Agent") or "unknown",
                model=_summary_field(text, "Model") or "unknown",
                status=status,
                reason=_summary_field(text, "Reason") or "not reported",
            )
        )
        if len(diagnostics) >= limit:
            break
    return tuple(diagnostics)


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
        reason=f"explicit legacy backend: {preflight.reason}",
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
        "Fallback order: "
        + (" -> ".join(report.fallback_order) if report.fallback_order else "disabled"),
        "",
        "Compact summary:",
        f"  default: {report.default_backend}",
        "  codex-cli: " + _backend_status_by_name(report.backends, "codex-cli"),
        "  openclaw: " + _backend_status_by_name(report.backends, "openclaw"),
        f"  runtime availability: {report.runtime_diagnostics_status}",
        f"  runtime diagnostics shown: {len(report.runtime_diagnostics)}",
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
            "  runtime availability: " + report.runtime_diagnostics_status,
            "",
            "Runtime availability diagnostics:",
        ]
    )
    if report.runtime_diagnostics:
        for diagnostic in report.runtime_diagnostics:
            lines.extend(
                [
                    f"- {diagnostic.status}",
                    f"  backend: {diagnostic.backend}",
                    f"  agent: {diagnostic.agent}",
                    f"  model: {diagnostic.model}",
                    f"  artifact: {diagnostic.artifact_path}",
                    f"  reason: {diagnostic.reason}",
                ]
            )
    else:
        lines.append("  none")
    lines.extend(
        [
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
