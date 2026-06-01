from __future__ import annotations

import subprocess

from signposter.codex_cli_backend import (
    check_codex_cli_preflight,
    execute_codex_cli_invocation,
    plan_codex_cli_invocation,
)


class Proc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_plan_codex_cli_invocation_builds_bounded_command(tmp_path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do work", encoding="utf-8")

    invocation = plan_codex_cli_invocation(
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path=prompt,
        timeout_seconds=33,
    )

    assert invocation.timeout_seconds == 33
    assert invocation.command == [
        "codex",
        "exec",
        "--agent",
        "worker_core",
        "--session-key",
        "signposter-test",
        "--model",
        "openai/gpt-5.4",
        "--reasoning",
        "medium",
        "--prompt-file",
        str(prompt),
    ]


def test_check_codex_cli_preflight_reports_missing_binary(tmp_path) -> None:
    invocation = plan_codex_cli_invocation(
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path=tmp_path / "prompt.md",
    )

    result = check_codex_cli_preflight(invocation, which_command=lambda _: None)

    assert result.ok is False
    assert result.status == "missing-binary"


def test_check_codex_cli_preflight_reports_missing_prompt(tmp_path) -> None:
    invocation = plan_codex_cli_invocation(
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path=tmp_path / "missing.md",
    )

    result = check_codex_cli_preflight(invocation, which_command=lambda _: "/usr/bin/codex")

    assert result.ok is False
    assert result.status == "missing-prompt"


def test_execute_codex_cli_invocation_captures_success_artifacts(tmp_path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do work", encoding="utf-8")
    invocation = plan_codex_cli_invocation(
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path=prompt,
    )

    result = execute_codex_cli_invocation(
        invocation,
        raw_path=tmp_path / "raw.txt",
        summary_path=tmp_path / "summary.md",
        run_command=lambda *args, **kwargs: Proc(0, stdout="done", stderr=""),
        which_command=lambda _: "/usr/bin/codex",
    )

    assert result.success is True
    assert result.status == "success"
    assert "[STDOUT]\ndone" in result.raw_path.read_text(encoding="utf-8")
    summary = result.summary_path.read_text(encoding="utf-8")
    assert "Status: success" in summary
    assert "Raw output remains local." in summary


def test_execute_codex_cli_invocation_writes_preflight_artifacts(tmp_path) -> None:
    invocation = plan_codex_cli_invocation(
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path=tmp_path / "prompt.md",
    )

    result = execute_codex_cli_invocation(
        invocation,
        raw_path=tmp_path / "raw.txt",
        summary_path=tmp_path / "summary.md",
        run_command=lambda *args, **kwargs: Proc(0),
        which_command=lambda _: None,
    )

    assert result.success is False
    assert result.status == "missing-binary"
    assert "[PREFLIGHT missing-binary]" in result.raw_path.read_text(encoding="utf-8")


def test_execute_codex_cli_invocation_captures_timeout(tmp_path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do work", encoding="utf-8")
    invocation = plan_codex_cli_invocation(
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path=prompt,
        timeout_seconds=1,
    )

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["codex"], timeout=1, output="partial")

    result = execute_codex_cli_invocation(
        invocation,
        raw_path=tmp_path / "raw.txt",
        summary_path=tmp_path / "summary.md",
        run_command=raise_timeout,
        which_command=lambda _: "/usr/bin/codex",
    )

    assert result.success is False
    assert result.exit_code == -1
    assert result.status == "timeout"
    assert "partial" in result.raw_path.read_text(encoding="utf-8")
