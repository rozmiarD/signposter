"""Execution backend policy for Signposter runner/reviewer stages."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_EXECUTION_BACKEND = "codex-cli"
EXECUTION_BACKEND_ENV = "SIGNPOSTER_EXECUTION_BACKEND"
ALLOWED_EXECUTION_BACKENDS = frozenset({"openclaw", "codex-cli"})


@dataclass(frozen=True)
class ExecutionBackendPlan:
    """Resolved execution backend metadata for dry-run and artifact output."""

    backend: str
    reason: str
    execution_supported: bool
    notes: tuple[str, ...] = ()


def resolve_execution_backend(
    backend: str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> ExecutionBackendPlan:
    """Resolve and validate the requested execution backend."""
    source = env if env is not None else os.environ
    explicit_backend = backend is not None
    env_backend = source.get(EXECUTION_BACKEND_ENV)
    requested = (backend or env_backend or DEFAULT_EXECUTION_BACKEND).strip()
    normalized = requested.lower()
    if normalized not in ALLOWED_EXECUTION_BACKENDS:
        allowed = ", ".join(sorted(ALLOWED_EXECUTION_BACKENDS))
        raise ValueError(f"unsupported execution backend '{requested}' (allowed: {allowed})")

    if normalized == "openclaw":
        return ExecutionBackendPlan(
            backend="openclaw",
            reason=(
                "explicit OpenClaw legacy backend selected"
                if explicit_backend or env_backend
                else "OpenClaw legacy backend selected"
            ),
            execution_supported=True,
            notes=("OpenClaw is legacy compatibility and is not the default backend.",),
        )

    return ExecutionBackendPlan(
        backend="codex-cli",
        reason=(
            "explicit Codex CLI backend selected for planning"
            if explicit_backend or env_backend
            else "default Codex CLI execution backend"
        ),
        execution_supported=True,
        notes=(
            "Codex CLI adapter is available for explicit --execute runs.",
            "Local preflight still blocks when the codex binary or prompt artifact is missing.",
        ),
    )


def build_backend_command_shape(
    *,
    backend: str,
    agent: str,
    session_key: str,
    model: str,
    reasoning_effort: str,
    prompt_path: str,
) -> str:
    """Return an operator-visible command shape for a resolved backend."""
    if backend == "openclaw":
        return (
            f"openclaw agent --agent {agent} "
            f"--session-key {session_key} "
            f"--model {model} "
            f"--thinking {reasoning_effort} "
            f'--message "$(cat {prompt_path})" --local'
        )
    if backend == "codex-cli":
        return (
            f"codex exec --model {model} - < {prompt_path} "
            f"(Signposter metadata: agent={agent}, session_key={session_key}, "
            f"reasoning={reasoning_effort})"
        )
    raise ValueError(f"unsupported execution backend '{backend}'")
