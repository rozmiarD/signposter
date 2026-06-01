from __future__ import annotations

import pytest

from signposter.execution_backend import (
    build_backend_command_shape,
    resolve_execution_backend,
)


def test_resolve_execution_backend_defaults_to_openclaw() -> None:
    plan = resolve_execution_backend(env={})

    assert plan.backend == "openclaw"
    assert plan.execution_supported is True
    assert plan.reason == "default Signposter execution backend"


def test_resolve_execution_backend_accepts_explicit_codex_cli() -> None:
    plan = resolve_execution_backend("codex-cli", env={})

    assert plan.backend == "codex-cli"
    assert plan.execution_supported is True
    assert "adapter is available" in " ".join(plan.notes)


def test_resolve_execution_backend_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unsupported execution backend"):
        resolve_execution_backend("unknown", env={})


def test_resolve_execution_backend_reads_environment_default() -> None:
    plan = resolve_execution_backend(env={"SIGNPOSTER_EXECUTION_BACKEND": "codex-cli"})

    assert plan.backend == "codex-cli"


def test_build_backend_command_shape_for_codex_cli_is_plan_only() -> None:
    shape = build_backend_command_shape(
        backend="codex-cli",
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path="artifacts/prompts/issue-1.md",
    )

    assert shape.startswith("codex exec ")
    assert "--model openai/gpt-5.4" in shape
    assert "--reasoning medium" in shape
    assert "--prompt-file artifacts/prompts/issue-1.md" in shape
