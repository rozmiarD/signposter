"""Codex CLI execution backend adapter.

The adapter is deliberately small and injectable.  It builds one bounded
invocation, performs a local binary/prompt preflight, captures stdout/stderr to
local raw artifacts, and writes a compact summary.  It does not mutate GitHub.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Protocol


class CompletedProcessLike(Protocol):
    returncode: int
    stdout: str | None
    stderr: str | None


RunCommand = Callable[..., CompletedProcessLike]
WhichCommand = Callable[[str], str | None]


@dataclass(frozen=True)
class CodexCliInvocation:
    """Planned Codex CLI invocation metadata."""

    agent: str
    session_key: str
    model: str
    reasoning_effort: str
    prompt_path: Path
    timeout_seconds: int = 120

    @property
    def command(self) -> list[str]:
        return [
            "codex",
            "exec",
            "--agent",
            self.agent,
            "--session-key",
            self.session_key,
            "--model",
            self.model,
            "--reasoning",
            self.reasoning_effort,
            "--prompt-file",
            str(self.prompt_path),
        ]


@dataclass(frozen=True)
class CodexCliPreflight:
    """Local Codex CLI preflight result."""

    ok: bool
    status: str
    reason: str
    command_path: str | None = None


@dataclass(frozen=True)
class CodexCliExecutionResult:
    """Codex CLI execution result and artifact paths."""

    success: bool
    exit_code: int
    status: str
    reason: str
    raw_path: Path
    summary_path: Path


def plan_codex_cli_invocation(
    *,
    agent: str,
    session_key: str,
    model: str,
    reasoning_effort: str,
    prompt_path: str | Path,
    timeout_seconds: int = 120,
) -> CodexCliInvocation:
    """Build the intended Codex CLI invocation without executing it."""
    return CodexCliInvocation(
        agent=agent,
        session_key=session_key,
        model=model,
        reasoning_effort=reasoning_effort,
        prompt_path=Path(prompt_path),
        timeout_seconds=timeout_seconds,
    )


def check_codex_cli_preflight(
    invocation: CodexCliInvocation,
    *,
    which_command: WhichCommand = which,
) -> CodexCliPreflight:
    """Check local Codex CLI prerequisites without consuming model tokens."""
    command_path = which_command("codex")
    if not command_path:
        return CodexCliPreflight(
            ok=False,
            status="missing-binary",
            reason="codex CLI binary was not found on PATH",
        )
    if not invocation.prompt_path.exists():
        return CodexCliPreflight(
            ok=False,
            status="missing-prompt",
            reason=f"prompt artifact does not exist: {invocation.prompt_path}",
            command_path=command_path,
        )
    return CodexCliPreflight(
        ok=True,
        status="ready",
        reason="codex CLI binary and prompt artifact are available",
        command_path=command_path,
    )


def execute_codex_cli_invocation(
    invocation: CodexCliInvocation,
    *,
    raw_path: str | Path,
    summary_path: str | Path,
    run_command: RunCommand = subprocess.run,
    which_command: WhichCommand = which,
) -> CodexCliExecutionResult:
    """Execute Codex CLI once and capture local raw/summary artifacts."""
    raw = Path(raw_path)
    summary = Path(summary_path)
    raw.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)

    preflight = check_codex_cli_preflight(invocation, which_command=which_command)
    if not preflight.ok:
        raw_text = f"[PREFLIGHT {preflight.status}]\n{preflight.reason}\n"
        raw.write_text(raw_text, encoding="utf-8")
        summary.write_text(
            _format_codex_cli_summary(
                invocation,
                status=preflight.status,
                reason=preflight.reason,
                exit_code=1,
                started_at=started_at,
                stdout="",
                stderr="",
                raw_path=raw,
            ),
            encoding="utf-8",
        )
        return CodexCliExecutionResult(
            success=False,
            exit_code=1,
            status=preflight.status,
            reason=preflight.reason,
            raw_path=raw,
            summary_path=summary,
        )

    try:
        proc = run_command(
            invocation.command,
            capture_output=True,
            text=True,
            timeout=invocation.timeout_seconds,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode
        status = "success" if exit_code == 0 else "failed"
        reason = (
            "Codex CLI execution completed successfully."
            if exit_code == 0
            else f"Codex CLI exited with code {exit_code}."
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        exit_code = -1
        status = "timeout"
        reason = f"Codex CLI execution exceeded timeout ({invocation.timeout_seconds}s)."

    raw.write_text(
        "\n".join(
            [
                f"[COMMAND] {' '.join(invocation.command)}",
                "[STDOUT]",
                stdout,
                "[STDERR]",
                stderr,
            ]
        ),
        encoding="utf-8",
    )
    summary.write_text(
        _format_codex_cli_summary(
            invocation,
            status=status,
            reason=reason,
            exit_code=exit_code,
            started_at=started_at,
            stdout=stdout,
            stderr=stderr,
            raw_path=raw,
        ),
        encoding="utf-8",
    )
    return CodexCliExecutionResult(
        success=exit_code == 0,
        exit_code=exit_code,
        status=status,
        reason=reason,
        raw_path=raw,
        summary_path=summary,
    )


def _format_codex_cli_summary(
    invocation: CodexCliInvocation,
    *,
    status: str,
    reason: str,
    exit_code: int,
    started_at: datetime,
    stdout: str,
    stderr: str,
    raw_path: Path,
) -> str:
    output = "\n".join(part for part in (stdout, stderr) if part)
    excerpt = _bounded_excerpt(output)
    return "\n".join(
        [
            "# Signposter Codex CLI Execution Summary",
            "",
            "Backend: codex-cli",
            f"Agent: {invocation.agent}",
            f"Model: {invocation.model}",
            f"Reasoning: {invocation.reasoning_effort}",
            f"Session Key: {invocation.session_key}",
            f"Prompt Artifact: {invocation.prompt_path}",
            f"Started (UTC): {started_at.isoformat()}",
            f"Exit Code: {exit_code}",
            f"Status: {status}",
            f"Reason: {reason}",
            f"Raw Output: {raw_path}",
            "",
            "## First output lines",
            "",
            "```",
            excerpt,
            "```",
            "",
            "Notes:",
            "- Raw output remains local.",
            "- No GitHub mutation was performed.",
        ]
    )


def _bounded_excerpt(text: str, *, max_lines: int = 30, max_chars: int = 1800) -> str:
    if not text:
        return "(no output)"
    lines = text.splitlines()[:max_lines]
    excerpt = "\n".join(lines)
    if len(excerpt) > max_chars:
        return excerpt[:max_chars] + "\n...[truncated]"
    if len(text.splitlines()) > max_lines:
        return excerpt + "\n...[truncated]"
    return excerpt
