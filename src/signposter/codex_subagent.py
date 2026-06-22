"""Local Codex CLI subagent dispatch contract.

This module defines the bounded contract for future subagent dispatch. It does
not execute Codex, spawn processes, mutate GitHub, or write artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from signposter.codex_cli_backend import CodexCliInvocation, plan_codex_cli_invocation
from signposter.role_routing import RoleExecutionSelection

DEFAULT_SUBAGENT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class CodexSubagentDispatchContract:
    """Auditable local contract for one bounded Codex CLI subagent task."""

    backend: str
    task_scope: str
    prompt_artifact: Path
    working_dir: Path
    raw_artifact: Path
    summary_artifact: Path
    last_message_artifact: Path
    role_name: str
    execution_agent: str
    model: str
    reasoning_effort: str
    timeout_seconds: int
    takeover_condition: str
    forbidden_actions: tuple[str, ...]
    invocation: CodexCliInvocation

    @property
    def command_preview(self) -> str:
        """Shell-shaped Codex CLI command preview without executing it."""
        return " ".join(self.invocation.command)

    @property
    def session_key(self) -> str:
        """Signposter-side session key metadata for audit and takeover."""
        return self.invocation.session_key


@dataclass(frozen=True)
class CodexSubagentOutputNormalization:
    """Normalized, gate-friendly view of one subagent output set."""

    backend: str
    role_name: str
    execution_agent: str
    model: str
    reasoning_effort: str
    execution_status: str
    exit_code: int
    task_execution_complete: bool
    acceptance: str
    takeover_required: bool
    raw_artifact: Path
    summary_artifact: Path
    last_message_artifact: Path
    raw_exists: bool
    summary_exists: bool
    last_message_exists: bool
    guidance: tuple[str, ...]


@dataclass(frozen=True)
class CodexSubagentTakeoverPlan:
    """Deterministic recovery plan for incomplete subagent work."""

    status: str
    reason: str
    takeover_required: bool
    artifact_repair_required: bool
    validation_required: bool
    required_actions: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    output: CodexSubagentOutputNormalization


def plan_codex_subagent_dispatch(
    *,
    task_scope: str,
    prompt_artifact: str | Path,
    working_dir: str | Path,
    raw_artifact: str | Path,
    summary_artifact: str | Path,
    last_message_artifact: str | Path,
    role_execution: RoleExecutionSelection,
    session_key: str,
    timeout_seconds: int = DEFAULT_SUBAGENT_TIMEOUT_SECONDS,
) -> CodexSubagentDispatchContract:
    """Build a read-only Codex CLI subagent contract."""
    if role_execution.backend != "codex-cli":
        raise ValueError("Codex subagent dispatch requires backend='codex-cli'")
    if not task_scope.strip():
        raise ValueError("task_scope must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    prompt = Path(prompt_artifact)
    workdir = Path(working_dir)
    raw = Path(raw_artifact)
    summary = Path(summary_artifact)
    last_message = Path(last_message_artifact)
    artifact_paths = (raw, summary, last_message)
    if len(set(artifact_paths)) != len(artifact_paths):
        raise ValueError("subagent artifact paths must be distinct")

    invocation = plan_codex_cli_invocation(
        agent=role_execution.execution_agent,
        session_key=session_key,
        model=role_execution.model,
        reasoning_effort=role_execution.reasoning_effort,
        prompt_path=prompt,
        working_dir=workdir,
        output_last_message_path=last_message,
        timeout_seconds=timeout_seconds,
    )

    return CodexSubagentDispatchContract(
        backend="codex-cli",
        task_scope=task_scope.strip(),
        prompt_artifact=prompt,
        working_dir=workdir,
        raw_artifact=raw,
        summary_artifact=summary,
        last_message_artifact=last_message,
        role_name=role_execution.role.policy.name,
        execution_agent=role_execution.execution_agent,
        model=role_execution.model,
        reasoning_effort=role_execution.reasoning_effort,
        timeout_seconds=timeout_seconds,
        takeover_condition=(
            "take over when Codex CLI returns a non-success status, times out, "
            "produces malformed output, or leaves the expected summary artifact unusable"
        ),
        forbidden_actions=(
            "no GitHub mutation",
            "no merge",
            "no issue close",
            "no branch deletion",
            "no package publish",
            "no unbounded raw output posting",
        ),
        invocation=invocation,
    )


def normalize_codex_subagent_output(
    contract: CodexSubagentDispatchContract,
    *,
    execution_status: str,
    exit_code: int,
    raw_exists: bool,
    summary_exists: bool,
    last_message_exists: bool,
) -> CodexSubagentOutputNormalization:
    """Normalize observed subagent artifacts into a compact completion state."""
    status = execution_status.strip().lower()
    complete = status == "success" and exit_code == 0 and raw_exists and summary_exists
    guidance: list[str] = []

    if status != "success" or exit_code != 0:
        guidance.append("subagent output requires takeover before gate evaluation")
    if not raw_exists:
        guidance.append("raw artifact is missing")
    if not summary_exists:
        guidance.append("summary artifact is missing")
    if not last_message_exists:
        guidance.append("last-message artifact is missing or was not produced")
    if complete:
        guidance.append("subagent output is normalized and ready for bounded summary use")

    return CodexSubagentOutputNormalization(
        backend=contract.backend,
        role_name=contract.role_name,
        execution_agent=contract.execution_agent,
        model=contract.model,
        reasoning_effort=contract.reasoning_effort,
        execution_status=status,
        exit_code=exit_code,
        task_execution_complete=complete,
        acceptance="pass" if complete else "needs-work",
        takeover_required=not complete,
        raw_artifact=contract.raw_artifact,
        summary_artifact=contract.summary_artifact,
        last_message_artifact=contract.last_message_artifact,
        raw_exists=raw_exists,
        summary_exists=summary_exists,
        last_message_exists=last_message_exists,
        guidance=tuple(guidance),
    )


def plan_codex_subagent_takeover(
    output: CodexSubagentOutputNormalization,
    *,
    validation_evidence_present: bool,
) -> CodexSubagentTakeoverPlan:
    """Build a safe takeover plan from normalized subagent output."""
    artifact_repair_required = not output.raw_exists or not output.summary_exists
    validation_required = not validation_evidence_present
    takeover_required = output.takeover_required or validation_required

    actions: tuple[str, ...]
    if not takeover_required:
        status = "not-required"
        reason = "subagent output is complete and validation evidence is present"
        actions = ("continue normal gate evaluation",)
    elif artifact_repair_required or output.execution_status in {
        "timeout",
        "runtime-stall",
        "malformed-output",
        "unsupported-model",
        "runtime-error",
    }:
        status = "ready"
        reason = "takeover can proceed with preserved local evidence and manual summary repair"
        actions = (
            "preserve any existing raw and summary artifacts under non-canonical names",
            "inspect the worktree and existing artifacts before editing",
            "repair or replace the canonical bounded summary artifact",
            "run targeted validation for the recovered work",
            "run full validation before report, PR, or merge",
            "continue through Signposter gate and complete surfaces only after evidence is ready",
        )
    else:
        status = "blocked"
        reason = "takeover state is unclear; inspect artifacts before continuing"
        actions = (
            "inspect local raw, summary, last-message, worktree, and issue comments",
            "do not report, complete, merge, or close until evidence is understood",
        )

    if validation_required and "run targeted validation for the recovered work" not in actions:
        actions = actions + (
            "run targeted validation for the recovered work",
            "run full validation before report, PR, or merge",
        )

    return CodexSubagentTakeoverPlan(
        status=status,
        reason=reason,
        takeover_required=takeover_required,
        artifact_repair_required=artifact_repair_required,
        validation_required=validation_required,
        required_actions=actions,
        forbidden_actions=(
            "no GitHub mutation until the corresponding Signposter plan is ready",
            "no issue close outside integration",
            "no merge before gate, CI, review, and approval requirements pass",
            "no raw backend output posting to GitHub",
        ),
        output=output,
    )


def format_codex_subagent_output_normalization(
    result: CodexSubagentOutputNormalization,
) -> str:
    """Render normalized subagent output state without raw log content."""
    lines = [
        "Signposter Codex Subagent Output Normalization",
        "",
        "Status:",
        "  complete" if result.task_execution_complete else "  blocked",
        "",
        "Role:",
        f"  role: {result.role_name}",
        f"  agent: {result.execution_agent}",
        f"  model: {result.model}",
        f"  reasoning: {result.reasoning_effort}",
        "",
        "Execution:",
        f"  status: {result.execution_status}",
        f"  exit_code: {result.exit_code}",
        f"  task_execution_complete: {'yes' if result.task_execution_complete else 'no'}",
        f"  acceptance: {result.acceptance}",
        f"  takeover_required: {'yes' if result.takeover_required else 'no'}",
        "",
        "Artifacts:",
        f"  raw: {result.raw_artifact} (exists: {'yes' if result.raw_exists else 'no'})",
        (
            f"  summary: {result.summary_artifact} "
            f"(exists: {'yes' if result.summary_exists else 'no'})"
        ),
        (
            f"  last_message: {result.last_message_artifact} "
            f"(exists: {'yes' if result.last_message_exists else 'no'})"
        ),
        "",
        "Guidance:",
    ]
    lines.extend(f"  - {item}" for item in result.guidance)
    lines.extend(
        [
            "",
            "Notes:",
            "  Raw output remains local.",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No Codex CLI execution was performed by this formatter.",
        ]
    )
    return "\n".join(lines)


def format_codex_subagent_takeover_plan(plan: CodexSubagentTakeoverPlan) -> str:
    """Render a compact stuck-subagent takeover plan."""
    lines = [
        "Signposter Codex Subagent Takeover Plan",
        "",
        "Status:",
        f"  {plan.status}",
        "",
        "Reason:",
        f"  {plan.reason}",
        "",
        "State:",
        f"  execution_status: {plan.output.execution_status}",
        f"  takeover_required: {'yes' if plan.takeover_required else 'no'}",
        f"  artifact_repair_required: {'yes' if plan.artifact_repair_required else 'no'}",
        f"  validation_required: {'yes' if plan.validation_required else 'no'}",
        "",
        "Required actions:",
    ]
    lines.extend(f"  - {action}" for action in plan.required_actions)
    lines.extend(["", "Forbidden actions:"])
    lines.extend(f"  - {action}" for action in plan.forbidden_actions)
    lines.extend(
        [
            "",
            "Notes:",
            "  Raw output remains local.",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No Codex CLI execution was performed by this planner.",
        ]
    )
    return "\n".join(lines)


def format_codex_subagent_dispatch_contract(
    contract: CodexSubagentDispatchContract,
) -> str:
    """Render a compact read-only contract for operator dry-runs."""
    lines = [
        "Signposter Codex Subagent Dispatch Contract",
        "",
        "Status:",
        "  ready",
        "",
        "Backend:",
        f"  {contract.backend}",
        "",
        "Role:",
        f"  role: {contract.role_name}",
        f"  agent: {contract.execution_agent}",
        f"  model: {contract.model}",
        f"  reasoning: {contract.reasoning_effort}",
        "",
        "Invocation:",
        f"  command_preview: {contract.command_preview}",
        f"  session_key: {contract.session_key} (Signposter metadata only)",
        "  prompt_transport: stdin",
        "",
        "Artifacts:",
        f"  prompt: {contract.prompt_artifact}",
        f"  raw: {contract.raw_artifact}",
        f"  summary: {contract.summary_artifact}",
        f"  last_message: {contract.last_message_artifact}",
        "",
        "Execution Bounds:",
        f"  working_dir: {contract.working_dir}",
        f"  timeout_seconds: {contract.timeout_seconds}",
        f"  takeover: {contract.takeover_condition}",
        "",
        "Output normalization:",
        "  success requires execution_status=success, exit_code=0, raw exists, and summary exists",
        "  non-success, missing raw, or missing summary requires takeover before gate",
        "  last-message artifact is useful for audit but raw/summary remain authoritative",
        "",
        "Forbidden actions:",
    ]
    lines.extend(f"  - {action}" for action in contract.forbidden_actions)
    lines.extend(
        [
            "",
            "Notes:",
            "  No GitHub mutation was performed.",
            "  No OpenClaw execution was performed.",
            "  No Codex CLI execution was performed.",
            "  This command only describes a local dispatch contract.",
        ]
    )
    return "\n".join(lines)
