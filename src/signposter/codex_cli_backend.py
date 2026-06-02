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
class CodexCliExecutionContract:
    """Stable Signposter-side contract for Codex CLI execution."""

    backend: str
    prompt_transport: str
    model_flag: str
    working_dir_flag: str
    output_last_message_flag: str
    metadata_only_fields: tuple[str, ...]
    raw_artifact: str
    summary_artifact: str
    timeout_status: str
    github_mutation: str
    unsupported_flags: tuple[str, ...]


@dataclass(frozen=True)
class CodexCliInvocation:
    """Planned Codex CLI invocation metadata."""

    agent: str
    session_key: str
    model: str
    reasoning_effort: str
    prompt_path: Path
    working_dir: Path | None = None
    output_last_message_path: Path | None = None
    timeout_seconds: int = 120

    @property
    def command(self) -> list[str]:
        command = [
            "codex",
            "exec",
            "--model",
            self.model,
        ]
        if self.working_dir is not None:
            command.extend(["--cd", str(self.working_dir)])
        if self.output_last_message_path is not None:
            command.extend(["--output-last-message", str(self.output_last_message_path)])
        command.append("-")
        return command


def build_codex_cli_execution_contract() -> CodexCliExecutionContract:
    """Return the bounded Codex CLI execution contract Signposter implements."""
    return CodexCliExecutionContract(
        backend="codex-cli",
        prompt_transport="prompt artifact is read locally and passed to codex exec via stdin",
        model_flag="--model",
        working_dir_flag="--cd",
        output_last_message_flag="--output-last-message",
        metadata_only_fields=("agent", "session_key", "reasoning_effort"),
        raw_artifact="local raw stdout/stderr artifact under artifacts/runs/",
        summary_artifact="bounded local summary artifact under artifacts/runs/",
        timeout_status="timeout with exit_code -1",
        github_mutation="none; execution backend never mutates GitHub",
        unsupported_flags=("--agent", "--session-key", "--reasoning", "--prompt-file"),
    )


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
    working_dir: str | Path | None = None,
    output_last_message_path: str | Path | None = None,
    timeout_seconds: int = 120,
) -> CodexCliInvocation:
    """Build the intended Codex CLI invocation without executing it."""
    return CodexCliInvocation(
        agent=agent,
        session_key=session_key,
        model=model,
        reasoning_effort=reasoning_effort,
        prompt_path=Path(prompt_path),
        working_dir=Path(working_dir) if working_dir is not None else None,
        output_last_message_path=(
            Path(output_last_message_path) if output_last_message_path is not None else None
        ),
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
        prompt_text = invocation.prompt_path.read_text(encoding="utf-8")
        proc = run_command(
            invocation.command,
            input=prompt_text,
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
                f"[PROMPT] {invocation.prompt_path} passed via stdin",
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
            "Reasoning Transport: Signposter metadata only",
            f"Session Key: {invocation.session_key}",
            f"Prompt Artifact: {invocation.prompt_path}",
            "Prompt Transport: stdin",
            f"Working Directory: {invocation.working_dir or '(current process cwd)'}",
            f"Last Message Artifact: {invocation.output_last_message_path or '(not requested)'}",
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
            "- Agent, session key, and reasoning effort are recorded as Signposter metadata.",
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
