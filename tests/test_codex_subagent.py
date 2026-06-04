from __future__ import annotations

import pytest

from signposter.codex_subagent import (
    format_codex_subagent_dispatch_contract,
    format_codex_subagent_output_normalization,
    normalize_codex_subagent_output,
    plan_codex_subagent_dispatch,
)
from signposter.dispatch import classify_candidate
from signposter.role_routing import resolve_role_execution, select_role_for_issue
from signposter.scan import LabeledItem


def _role_execution(backend: str = "codex-cli"):
    item = LabeledItem(
        number=184,
        title="Add subagent dispatch contract",
        html_url="https://github.com/example/repo/issues/184",
        labels=[
            "state:ready",
            "phase:build",
            "role:worker",
            "risk:medium",
            "area:dispatcher",
        ],
        item_type="issue",
    )
    selection = select_role_for_issue(item, classify_candidate(item))
    return resolve_role_execution(selection, backend=backend)


def test_plan_codex_subagent_dispatch_builds_bounded_contract(tmp_path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("do bounded work", encoding="utf-8")
    role_execution = _role_execution()

    contract = plan_codex_subagent_dispatch(
        task_scope="implement a small dispatcher helper",
        prompt_artifact=prompt,
        working_dir=tmp_path / "work",
        raw_artifact=tmp_path / "raw.txt",
        summary_artifact=tmp_path / "summary.md",
        last_message_artifact=tmp_path / "last-message.txt",
        role_execution=role_execution,
        session_key="signposter-test-subagent",
        timeout_seconds=45,
    )

    assert contract.backend == "codex-cli"
    assert contract.role_name == "WORKER_CODE"
    assert contract.execution_agent == "codex_worker_code"
    assert contract.model == "openai/gpt-5.3-codex"
    assert contract.reasoning_effort == "low"
    assert contract.timeout_seconds == 45
    assert contract.session_key == "signposter-test-subagent"
    assert contract.command_preview.startswith("codex exec --model openai/gpt-5.3-codex")
    assert "--cd" in contract.command_preview
    assert "--output-last-message" in contract.command_preview
    assert "malformed output" in contract.takeover_condition
    assert "no GitHub mutation" in contract.forbidden_actions
    assert contract.invocation.agent == "codex_worker_code"
    assert contract.invocation.model == "openai/gpt-5.3-codex"
    assert contract.invocation.prompt_path == prompt


def test_plan_codex_subagent_dispatch_rejects_non_codex_backend(tmp_path) -> None:
    with pytest.raises(ValueError, match="backend='codex-cli'"):
        plan_codex_subagent_dispatch(
            task_scope="do work",
            prompt_artifact=tmp_path / "prompt.md",
            working_dir=tmp_path,
            raw_artifact=tmp_path / "raw.txt",
            summary_artifact=tmp_path / "summary.md",
            last_message_artifact=tmp_path / "last-message.txt",
            role_execution=_role_execution("openclaw"),
            session_key="signposter-test-subagent",
        )


def test_plan_codex_subagent_dispatch_rejects_artifact_path_collision(tmp_path) -> None:
    with pytest.raises(ValueError, match="artifact paths must be distinct"):
        plan_codex_subagent_dispatch(
            task_scope="do work",
            prompt_artifact=tmp_path / "prompt.md",
            working_dir=tmp_path,
            raw_artifact=tmp_path / "artifact.txt",
            summary_artifact=tmp_path / "artifact.txt",
            last_message_artifact=tmp_path / "last-message.txt",
            role_execution=_role_execution(),
            session_key="signposter-test-subagent",
        )


def test_format_codex_subagent_dispatch_contract_is_read_only(tmp_path) -> None:
    contract = plan_codex_subagent_dispatch(
        task_scope="bounded task",
        prompt_artifact=tmp_path / "prompt.md",
        working_dir=tmp_path,
        raw_artifact=tmp_path / "raw.txt",
        summary_artifact=tmp_path / "summary.md",
        last_message_artifact=tmp_path / "last-message.txt",
        role_execution=_role_execution(),
        session_key="signposter-test-subagent",
    )

    output = format_codex_subagent_dispatch_contract(contract)

    assert "Signposter Codex Subagent Dispatch Contract" in output
    assert "Status:\n  ready" in output
    assert "agent: codex_worker_code" in output
    assert "model: openai/gpt-5.3-codex" in output
    assert "command_preview: codex exec --model openai/gpt-5.3-codex" in output
    assert "session_key: signposter-test-subagent (Signposter metadata only)" in output
    assert "prompt_transport: stdin" in output
    assert "--session-key" not in output
    assert "takeover:" in output
    assert "Output normalization:" in output
    assert "missing summary requires takeover before gate" in output
    assert "No GitHub mutation was performed." in output
    assert "No Codex CLI execution was performed." in output


def test_normalize_codex_subagent_output_marks_success_complete(tmp_path) -> None:
    contract = plan_codex_subagent_dispatch(
        task_scope="bounded task",
        prompt_artifact=tmp_path / "prompt.md",
        working_dir=tmp_path,
        raw_artifact=tmp_path / "raw.txt",
        summary_artifact=tmp_path / "summary.md",
        last_message_artifact=tmp_path / "last-message.txt",
        role_execution=_role_execution(),
        session_key="signposter-test-subagent",
    )

    result = normalize_codex_subagent_output(
        contract,
        execution_status="success",
        exit_code=0,
        raw_exists=True,
        summary_exists=True,
        last_message_exists=True,
    )

    assert result.task_execution_complete is True
    assert result.acceptance == "pass"
    assert result.takeover_required is False
    assert "ready for bounded summary use" in result.guidance[-1]

    output = format_codex_subagent_output_normalization(result)
    assert "Status:\n  complete" in output
    assert "task_execution_complete: yes" in output
    assert "Raw output remains local." in output
    assert "No GitHub mutation was performed." in output


def test_normalize_codex_subagent_output_blocks_missing_summary(tmp_path) -> None:
    contract = plan_codex_subagent_dispatch(
        task_scope="bounded task",
        prompt_artifact=tmp_path / "prompt.md",
        working_dir=tmp_path,
        raw_artifact=tmp_path / "raw.txt",
        summary_artifact=tmp_path / "summary.md",
        last_message_artifact=tmp_path / "last-message.txt",
        role_execution=_role_execution(),
        session_key="signposter-test-subagent",
    )

    result = normalize_codex_subagent_output(
        contract,
        execution_status="runtime-stall",
        exit_code=1,
        raw_exists=True,
        summary_exists=False,
        last_message_exists=False,
    )

    assert result.task_execution_complete is False
    assert result.acceptance == "needs-work"
    assert result.takeover_required is True
    assert "subagent output requires takeover before gate evaluation" in result.guidance
    assert "summary artifact is missing" in result.guidance

    output = format_codex_subagent_output_normalization(result)
    assert "Status:\n  blocked" in output
    assert "takeover_required: yes" in output
    assert "summary.md (exists: no)" in output
    assert "No Codex CLI execution was performed by this formatter." in output


def test_cli_subagent_plan_codex_renders_read_only_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    from signposter.cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "signposter",
            "subagent",
            "plan-codex",
            "--role",
            "WORKER_CODE",
            "--scope",
            "bounded dry-run",
            "--prompt",
            str(tmp_path / "prompt.md"),
            "--working-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "Signposter Codex Subagent Dispatch Contract" in output
    assert "agent: codex_worker_code" in output
    assert "command_preview: codex exec --model openai/gpt-5.3-codex" in output
    assert "session_key: signposter-subagent-dry-run (Signposter metadata only)" in output
    assert "No Codex CLI execution was performed." in output


def test_cli_subagent_plan_codex_blocks_unknown_role(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    from signposter.cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "signposter",
            "subagent",
            "plan-codex",
            "--role",
            "MISSING",
            "--scope",
            "bounded dry-run",
            "--prompt",
            str(tmp_path / "prompt.md"),
            "--working-dir",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        main()

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "Unknown Signposter role: MISSING" in captured.err
