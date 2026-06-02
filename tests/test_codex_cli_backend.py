from __future__ import annotations

import subprocess

from signposter.codex_cli_backend import (
    build_codex_cli_execution_contract,
    check_codex_cli_preflight,
    classify_codex_cli_failure,
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
        working_dir=tmp_path,
        output_last_message_path=tmp_path / "last-message.txt",
        timeout_seconds=33,
    )

    assert invocation.timeout_seconds == 33
    assert invocation.command == [
        "codex",
        "exec",
        "--model",
        "openai/gpt-5.4",
        "--cd",
        str(tmp_path),
        "--output-last-message",
        str(tmp_path / "last-message.txt"),
        "-",
    ]


def test_codex_cli_execution_contract_documents_supported_shape() -> None:
    contract = build_codex_cli_execution_contract()

    assert contract.backend == "codex-cli"
    assert contract.model_flag == "--model"
    assert contract.working_dir_flag == "--cd"
    assert contract.output_last_message_flag == "--output-last-message"
    assert "reasoning_effort" in contract.metadata_only_fields
    assert contract.timeout_status == "timeout with exit_code -1"
    assert contract.github_mutation == "none; execution backend never mutates GitHub"
    assert "--agent" in contract.unsupported_flags
    assert "--prompt-file" in contract.unsupported_flags


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
    captured: dict[str, object] = {}
    invocation = plan_codex_cli_invocation(
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path=prompt,
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        return Proc(0, stdout="done", stderr="")

    result = execute_codex_cli_invocation(
        invocation,
        raw_path=tmp_path / "raw.txt",
        summary_path=tmp_path / "summary.md",
        run_command=fake_run,
        which_command=lambda _: "/usr/bin/codex",
    )

    assert result.success is True
    assert result.status == "success"
    assert captured["command"] == ["codex", "exec", "--model", "openai/gpt-5.4", "-"]
    assert captured["input"] == "do work"
    assert "[STDOUT]\ndone" in result.raw_path.read_text(encoding="utf-8")
    assert "[PROMPT]" in result.raw_path.read_text(encoding="utf-8")
    summary = result.summary_path.read_text(encoding="utf-8")
    assert "**Backend:** codex-cli" in summary
    assert "**Agent:** worker_core" in summary
    assert "**Model:** openai/gpt-5.4" in summary
    assert "**Reasoning:** medium" in summary
    assert "**Exit Code:** 0" in summary
    assert "**Status:** success" in summary
    assert "**Task execution complete:** yes" in summary
    assert "**Acceptance:** pass" in summary
    assert "Status: success" in summary
    assert "Prompt Transport: stdin" in summary
    assert "Reasoning Transport: Signposter metadata only" in summary
    assert "Raw output: local only" in summary
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
    summary = result.summary_path.read_text(encoding="utf-8")
    assert "**Exit Code:** 1" in summary
    assert "**Status:** missing-binary" in summary
    assert "**Task execution complete:** no" in summary
    assert "**Acceptance:** needs-work" in summary


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
    summary = result.summary_path.read_text(encoding="utf-8")
    assert "**Exit Code:** -1" in summary
    assert "**Status:** timeout" in summary
    assert "**Task execution complete:** no" in summary
    assert "**Acceptance:** needs-work" in summary


def test_classify_codex_cli_failure_detects_unsupported_model() -> None:
    status = classify_codex_cli_failure(
        exit_code=1,
        stdout="",
        stderr="The 'openai/gpt-5.4' model is not supported for this account.",
    )

    assert status == "unsupported-model"


def test_classify_codex_cli_failure_detects_runtime_stall() -> None:
    status = classify_codex_cli_failure(
        exit_code=1,
        stdout="",
        stderr="runtime idle timed out waiting for progress",
    )

    assert status == "runtime-stall"


def test_classify_codex_cli_failure_detects_malformed_output() -> None:
    status = classify_codex_cli_failure(
        exit_code=1,
        stdout="",
        stderr="could not parse output: malformed output",
    )

    assert status == "malformed-output"


def test_classify_codex_cli_failure_uses_runtime_error_fallback() -> None:
    status = classify_codex_cli_failure(
        exit_code=1,
        stdout="",
        stderr="tool exited with an unclassified provider error",
    )

    assert status == "runtime-error"


def test_execute_codex_cli_invocation_captures_unsupported_model_status(tmp_path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do work", encoding="utf-8")
    invocation = plan_codex_cli_invocation(
        agent="worker_core",
        session_key="signposter-test",
        model="openai/gpt-5.4",
        reasoning_effort="medium",
        prompt_path=prompt,
    )

    def fake_run(command, **kwargs):
        return Proc(
            1,
            stdout="",
            stderr="The 'openai/gpt-5.4' model is not supported for this account.",
        )

    result = execute_codex_cli_invocation(
        invocation,
        raw_path=tmp_path / "raw.txt",
        summary_path=tmp_path / "summary.md",
        run_command=fake_run,
        which_command=lambda _: "/usr/bin/codex",
    )

    assert result.success is False
    assert result.exit_code == 1
    assert result.status == "unsupported-model"
    assert "classified as unsupported-model" in result.reason
    summary = result.summary_path.read_text(encoding="utf-8")
    assert "**Exit Code:** 1" in summary
    assert "**Status:** unsupported-model" in summary
    assert "**Task execution complete:** no" in summary
    assert "**Acceptance:** needs-work" in summary
