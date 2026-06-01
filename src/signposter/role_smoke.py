"""Local smoke planning and execution for role-aware OpenClaw roles."""

from __future__ import annotations

import datetime
import subprocess
from dataclasses import dataclass
from pathlib import Path

from signposter.artifact_safety import find_stale_or_failover_signal
from signposter.openclaw_diagnostics import (
    OpenClawRuntimeDiagnostics,
    gather_openclaw_runtime_diagnostics,
)
from signposter.openclaw_preflight import (
    check_openclaw_preflight,
    format_openclaw_preflight_block,
)
from signposter.role_policy import (
    ACTIVE_ROLE_POLICIES,
    RolePolicy,
    get_role_policy,
    validate_role_policy,
)


@dataclass(frozen=True)
class RoleSmokePlan:
    role_name: str
    policy: RolePolicy
    session_key: str
    message: str
    command_shape: str


@dataclass(frozen=True)
class RoleSmokeDiagnosis:
    status: str
    reason: str
    remediation: tuple[str, ...]
    signal: str | None = None


@dataclass(frozen=True)
class RoleSmokeMatrixEntry:
    role_name: str
    agent: str
    model: str
    reasoning_effort: str
    policy_status: str
    policy_errors: tuple[str, ...]
    command_shape: str
    result_status: str | None = None
    result_reason: str | None = None
    raw_path: str | None = None
    summary_path: str | None = None


@dataclass(frozen=True)
class RoleSmokeMatrix:
    mode: str
    entries: tuple[RoleSmokeMatrixEntry, ...]
    diagnostics_warnings: tuple[str, ...]


DEFAULT_ROLE_SMOKE_TIMEOUT_SECONDS = 20
DEFAULT_ROLE_SMOKE_SUBPROCESS_TIMEOUT_SECONDS = 30


def build_role_smoke_plan(role_name: str) -> RoleSmokePlan:
    """Build a deterministic local smoke plan for a role policy."""
    policy = get_role_policy(role_name)
    session_key = f"signposter-smoke-{role_name.lower()}"
    message = f"Reply with exactly {role_name}_SMOKE_OK and nothing else."
    command_shape = (
        f"openclaw agent --agent {policy.openclaw_agent} "
        f"--session-key {session_key} "
        f"--model {policy.model} "
        f"--thinking {policy.reasoning_effort} "
        f'--message "{message}" --local --json --timeout {DEFAULT_ROLE_SMOKE_TIMEOUT_SECONDS}'
    )
    return RoleSmokePlan(
        role_name=role_name,
        policy=policy,
        session_key=session_key,
        message=message,
        command_shape=command_shape,
    )


def format_role_smoke_plan(plan: RoleSmokePlan) -> str:
    lines = [f"Signposter Role Smoke — {plan.role_name}", ""]
    lines.append(f"Agent: {plan.policy.openclaw_agent}")
    lines.append(f"Model: {plan.policy.model}")
    lines.append(f"Reasoning: {plan.policy.reasoning_effort}")
    lines.append(f"Session key: {plan.session_key}")
    lines.append(f"Command shape: {plan.command_shape}")
    lines.append(f"Runtime timeout: {DEFAULT_ROLE_SMOKE_TIMEOUT_SECONDS}s")
    lines.append("")
    lines.append("Notes:")
    lines.append("  No GitHub mutation was performed.")
    lines.append("  No OpenClaw execution was performed.")
    return "\n".join(lines)


def classify_role_smoke_result(
    *,
    role_name: str,
    exit_code: int | None,
    combined_output: str,
    timed_out: bool,
    diagnostics_warnings: tuple[str, ...] = (),
) -> RoleSmokeDiagnosis:
    """Classify a smoke execution result into operator-facing categories."""
    if timed_out:
        remediation = [
            "Inspect the local raw artifact for the exact stall point.",
            "Run `openclaw models status` and remove stale fallback/auth drift before retry.",
        ]
        if diagnostics_warnings:
            remediation.append("Resolve the reported OpenClaw runtime hygiene warnings first.")
        return RoleSmokeDiagnosis(
            status="timeout",
            reason="OpenClaw role smoke timed out before a bounded response was returned.",
            remediation=tuple(remediation),
        )

    if exit_code == 0 and f"{role_name}_SMOKE_OK" in combined_output:
        return RoleSmokeDiagnosis(
            status="success",
            reason="OpenClaw returned the expected bounded smoke response.",
            remediation=(),
        )

    lowered = combined_output.lower()
    if "auth refresh request timed out" in lowered or "authentication failed" in lowered:
        return RoleSmokeDiagnosis(
            status="auth-runtime-failure",
            reason="OpenClaw hit an authentication/runtime refresh problem.",
            remediation=(
                "Refresh provider auth for the active OpenClaw agent.",
                "Re-run `openclaw doctor` and `openclaw models status` before retry.",
            ),
        )

    stale_signal = find_stale_or_failover_signal(combined_output)
    if stale_signal:
        return RoleSmokeDiagnosis(
            status="failover-or-stale-runtime",
            reason=f"OpenClaw output reported a stale/failover provider signal: {stale_signal}.",
            remediation=(
                "Clear stale sessions or remove unhealthy fallback providers before retry.",
                "Keep raw output local and regenerate only a bounded summary artifact.",
            ),
            signal=stale_signal,
        )

    if diagnostics_warnings:
        return RoleSmokeDiagnosis(
            status="config-drift",
            reason="OpenClaw runtime/config drift was detected during smoke execution.",
            remediation=(
                "Align OpenClaw defaults, fallbacks, and aliases "
                "with the active Signposter policy.",
                "Re-run `signposter doctor --automation` after fixing runtime drift.",
            ),
        )

    return RoleSmokeDiagnosis(
        status="runtime-error",
        reason=f"OpenClaw exited with code {exit_code} without a bounded success signal.",
        remediation=(
            "Inspect the local raw artifact and retry only after "
            "identifying the concrete runtime failure.",
        ),
    )


def _format_summary_artifact(
    *,
    plan: RoleSmokePlan,
    diagnosis: RoleSmokeDiagnosis,
    result: dict,
    diagnostics_warnings: tuple[str, ...],
) -> str:
    lines = [
        "# Signposter Role Smoke Summary",
        "",
        f"**Role:** {plan.role_name}",
        f"**Agent:** {plan.policy.openclaw_agent}",
        f"**Model:** {plan.policy.model}",
        f"**Reasoning:** {plan.policy.reasoning_effort}",
        f"**Status:** {diagnosis.status}",
        f"**Reason:** {diagnosis.reason}",
        f"**Exit Code:** {result.get('exit_code', 'timeout')}",
        f"**Raw output:** {result.get('raw_path')}",
    ]
    if diagnostics_warnings:
        lines.extend(["", "## Runtime hygiene warnings", ""])
        for warning in diagnostics_warnings:
            lines.append(f"- {warning}")
    if diagnosis.remediation:
        lines.extend(["", "## Remediation", ""])
        for item in diagnosis.remediation:
            lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- No GitHub mutation was performed.",
            "- Raw OpenClaw output remains local.",
            "- This artifact is bounded and operator-facing.",
        ]
    )
    return "\n".join(lines)


def _default_role_names() -> tuple[str, ...]:
    return tuple(sorted(ACTIVE_ROLE_POLICIES))


def _resolve_role_names(role_names: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if role_names is None:
        return _default_role_names()
    return tuple(role_names)


def build_role_smoke_matrix(role_names: tuple[str, ...] | None = None) -> RoleSmokeMatrix:
    """Build a dry-run matrix for all active or selected role smoke plans."""
    entries: list[RoleSmokeMatrixEntry] = []
    diagnostics = gather_openclaw_runtime_diagnostics()
    for role_name in _resolve_role_names(role_names):
        plan = build_role_smoke_plan(role_name)
        policy_errors = tuple(validate_role_policy(plan.policy))
        entries.append(
            RoleSmokeMatrixEntry(
                role_name=role_name,
                agent=plan.policy.openclaw_agent,
                model=plan.policy.model,
                reasoning_effort=plan.policy.reasoning_effort,
                policy_status="pass" if not policy_errors else "fail",
                policy_errors=policy_errors,
                command_shape=plan.command_shape,
            )
        )
    return RoleSmokeMatrix(
        mode="plan",
        entries=tuple(entries),
        diagnostics_warnings=diagnostics.warnings,
    )


def format_role_smoke_matrix(matrix: RoleSmokeMatrix) -> str:
    """Render a bounded per-role smoke matrix for operator inspection."""
    lines = ["Signposter Role Smoke Matrix", ""]
    lines.append(f"Mode: {matrix.mode}")
    lines.append(f"Roles: {len(matrix.entries)}")
    if matrix.diagnostics_warnings:
        lines.append("")
        lines.append("Runtime hygiene warnings:")
        for warning in matrix.diagnostics_warnings:
            lines.append(f"  - {warning}")
    lines.append("")
    lines.append("Entries:")
    for entry in matrix.entries:
        lines.append(f"- {entry.role_name}")
        lines.append(f"  agent: {entry.agent}")
        lines.append(f"  model: {entry.model}")
        lines.append(f"  reasoning: {entry.reasoning_effort}")
        lines.append(f"  policy: {entry.policy_status}")
        if entry.policy_errors:
            for error in entry.policy_errors:
                lines.append(f"    error: {error}")
        if matrix.mode == "plan":
            lines.append(f"  command: {entry.command_shape}")
        else:
            lines.append(f"  result: {entry.result_status or 'unknown'}")
            if entry.result_reason:
                lines.append(f"  reason: {entry.result_reason}")
            lines.append(f"  raw: {entry.raw_path or 'none'}")
            lines.append(f"  summary: {entry.summary_path or 'none'}")
    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            (
                "  No OpenClaw execution was performed."
                if matrix.mode == "plan"
                else "  Raw OpenClaw outputs remain local."
            ),
        ]
    )
    return "\n".join(lines)


def execute_role_smoke(
    role_name: str,
    *,
    runs_dir: Path | str = "artifacts/runs",
    diagnostics: OpenClawRuntimeDiagnostics | None = None,
) -> dict:
    """Run a local OpenClaw smoke turn for one role policy."""
    plan = build_role_smoke_plan(role_name)
    preflight = check_openclaw_preflight(artifact_kind="worker", target=0)
    if not preflight.ok:
        print(format_openclaw_preflight_block(preflight))
        return {"success": False, "error": preflight.reason, "raw_path": None}

    runs_path = Path(runs_dir)
    runs_path.mkdir(parents=True, exist_ok=True)
    raw_path = runs_path / f"role-smoke-{role_name.lower()}.raw.txt"
    summary_path = runs_path / f"role-smoke-{role_name.lower()}.summary.md"
    diagnostics = diagnostics or gather_openclaw_runtime_diagnostics()

    exec_cmd = [
        "openclaw",
        "agent",
        "--agent",
        plan.policy.openclaw_agent,
        "--session-key",
        plan.session_key,
        "--model",
        plan.policy.model,
        "--thinking",
        plan.policy.reasoning_effort,
        "--message",
        plan.message,
        "--local",
        "--json",
        "--timeout",
        str(DEFAULT_ROLE_SMOKE_TIMEOUT_SECONDS),
    ]

    try:
        proc = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=DEFAULT_ROLE_SMOKE_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raw_path.write_text(f"[TIMEOUT]\n{exc}", encoding="utf-8")
        diagnosis = classify_role_smoke_result(
            role_name=role_name,
            exit_code=None,
            combined_output=str(exc),
            timed_out=True,
            diagnostics_warnings=diagnostics.warnings,
        )
        result = {
            "success": False,
            "error": diagnosis.status,
            "exit_code": None,
            "raw_path": str(raw_path),
            "summary_path": str(summary_path),
            "diagnosis": diagnosis,
            "started_utc": datetime.datetime.now(datetime.UTC).isoformat(),
            "command_shape": plan.command_shape,
            "diagnostics_warnings": diagnostics.warnings,
        }
        summary_path.write_text(
            _format_summary_artifact(
                plan=plan,
                diagnosis=diagnosis,
                result=result,
                diagnostics_warnings=diagnostics.warnings,
            ),
            encoding="utf-8",
        )
        return result

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout
    if stderr:
        combined += "\n\n=== STDERR ===\n" + stderr

    raw_path.write_text(combined, encoding="utf-8")
    diagnosis = classify_role_smoke_result(
        role_name=role_name,
        exit_code=proc.returncode,
        combined_output=combined,
        timed_out=False,
        diagnostics_warnings=diagnostics.warnings,
    )
    result = {
        "success": diagnosis.status == "success",
        "exit_code": proc.returncode,
        "raw_path": str(raw_path),
        "summary_path": str(summary_path),
        "started_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "command_shape": plan.command_shape,
        "diagnosis": diagnosis,
        "diagnostics_warnings": diagnostics.warnings,
    }
    summary_path.write_text(
        _format_summary_artifact(
            plan=plan,
            diagnosis=diagnosis,
            result=result,
            diagnostics_warnings=diagnostics.warnings,
        ),
        encoding="utf-8",
    )

    return result


def execute_role_smoke_matrix(
    role_names: tuple[str, ...] | None = None,
    *,
    runs_dir: Path | str = "artifacts/runs",
) -> RoleSmokeMatrix:
    """Execute local smoke turns for all active or selected roles."""
    selected_names = _resolve_role_names(role_names)
    diagnostics = gather_openclaw_runtime_diagnostics()
    entries: list[RoleSmokeMatrixEntry] = []

    for role_name in selected_names:
        plan = build_role_smoke_plan(role_name)
        policy_errors = tuple(validate_role_policy(plan.policy))
        result = execute_role_smoke(role_name, runs_dir=runs_dir, diagnostics=diagnostics)
        diagnosis = result.get("diagnosis")
        entries.append(
            RoleSmokeMatrixEntry(
                role_name=role_name,
                agent=plan.policy.openclaw_agent,
                model=plan.policy.model,
                reasoning_effort=plan.policy.reasoning_effort,
                policy_status="pass" if not policy_errors else "fail",
                policy_errors=policy_errors,
                command_shape=plan.command_shape,
                result_status=(
                    diagnosis.status if diagnosis is not None else result.get("error", "unknown")
                ),
                result_reason=diagnosis.reason if diagnosis is not None else result.get("error"),
                raw_path=result.get("raw_path"),
                summary_path=result.get("summary_path"),
            )
        )

    return RoleSmokeMatrix(
        mode="execute",
        entries=tuple(entries),
        diagnostics_warnings=diagnostics.warnings,
    )
