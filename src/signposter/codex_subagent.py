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
