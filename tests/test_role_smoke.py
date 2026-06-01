from __future__ import annotations

from subprocess import TimeoutExpired
from unittest.mock import patch

from signposter.role_smoke import (
    build_role_smoke_matrix,
    build_role_smoke_plan,
    classify_role_smoke_result,
    execute_role_smoke,
    execute_role_smoke_matrix,
    format_role_smoke_matrix,
    format_role_smoke_plan,
)


def test_build_role_smoke_plan_uses_explicit_model_and_reasoning():
    plan = build_role_smoke_plan("REVIEWER_CORE")

    assert plan.policy.model == "openai/gpt-5.4"
    assert plan.policy.reasoning_effort == "medium"
    assert "--model openai/gpt-5.4" in plan.command_shape
    assert "--thinking medium" in plan.command_shape


def test_format_role_smoke_plan_mentions_no_execution():
    output = format_role_smoke_plan(build_role_smoke_plan("WORKER_CORE"))

    assert "No OpenClaw execution was performed." in output


def test_execute_role_smoke_passes_model_and_thinking(tmp_path):
    with patch("signposter.role_smoke.check_openclaw_preflight") as mock_preflight, \
         patch("signposter.role_smoke.gather_openclaw_runtime_diagnostics") as mock_diag, \
         patch("signposter.role_smoke.subprocess.run") as mock_run:
        mock_preflight.return_value = type("pf", (), {"ok": True})()
        mock_diag.return_value = type("diag", (), {"warnings": ()})()
        mock_run.return_value = type("proc", (), {"stdout": "ok", "stderr": "", "returncode": 0})()

        result = execute_role_smoke("WORKER_CORE", runs_dir=tmp_path)

    cmd = mock_run.call_args.args[0]
    assert "--model" in cmd
    assert "openai/gpt-5.4" in cmd
    assert "--thinking" in cmd
    assert "medium" in cmd
    assert result["success"] is False
    assert result["diagnosis"].status == "runtime-error"
    assert tmp_path.joinpath("role-smoke-worker_core.summary.md").exists()


def test_classify_role_smoke_success_uses_expected_bounded_token():
    diagnosis = classify_role_smoke_result(
        role_name="WORKER_CORE",
        exit_code=0,
        combined_output="WORKER_CORE_SMOKE_OK",
        timed_out=False,
    )

    assert diagnosis.status == "success"


def test_execute_role_smoke_timeout_writes_summary_and_diagnostics(tmp_path):
    with patch("signposter.role_smoke.check_openclaw_preflight") as mock_preflight, \
         patch("signposter.role_smoke.gather_openclaw_runtime_diagnostics") as mock_diag, \
         patch("signposter.role_smoke.subprocess.run") as mock_run:
        mock_preflight.return_value = type("pf", (), {"ok": True})()
        mock_diag.return_value = type("diag", (), {"warnings": ("fallback drift",)})()
        mock_run.side_effect = TimeoutExpired(cmd=["openclaw"], timeout=30)

        result = execute_role_smoke("REVIEWER_CORE", runs_dir=tmp_path)

    assert result["success"] is False
    assert result["diagnosis"].status == "timeout"
    assert "fallback drift" in tmp_path.joinpath("role-smoke-reviewer_core.summary.md").read_text(
        encoding="utf-8"
    )


def test_build_role_smoke_matrix_defaults_to_all_roles():
    with patch("signposter.role_smoke.gather_openclaw_runtime_diagnostics") as mock_diag:
        mock_diag.return_value = type("diag", (), {"warnings": ("runtime drift",)})()

        matrix = build_role_smoke_matrix()

    assert matrix.mode == "plan"
    assert len(matrix.entries) >= 1
    assert matrix.diagnostics_warnings == ("runtime drift",)
    assert all(entry.policy_status in ("pass", "fail") for entry in matrix.entries)


def test_format_role_smoke_matrix_includes_result_paths():
    matrix = type(
        "Matrix",
        (),
        {
            "mode": "execute",
            "diagnostics_warnings": (),
            "entries": (
                type(
                    "Entry",
                    (),
                    {
                        "role_name": "WORKER_CORE",
                        "agent": "worker",
                        "model": "openai/gpt-5.4",
                        "reasoning_effort": "medium",
                        "policy_status": "pass",
                        "policy_errors": (),
                        "command_shape": "openclaw agent ...",
                        "result_status": "timeout",
                        "result_reason": "timed out",
                        "raw_path": "artifacts/runs/raw.txt",
                        "summary_path": "artifacts/runs/summary.md",
                    },
                )(),
            ),
        },
    )()

    output = format_role_smoke_matrix(matrix)

    assert "result: timeout" in output
    assert "summary: artifacts/runs/summary.md" in output


def test_execute_role_smoke_matrix_passes_shared_diagnostics(tmp_path):
    diagnostics = type("diag", (), {"warnings": ("drift",)})()
    fake_diagnosis = type("diag_result", (), {"status": "timeout", "reason": "timed out"})()
    with patch(
        "signposter.role_smoke.gather_openclaw_runtime_diagnostics",
        return_value=diagnostics,
    ), patch("signposter.role_smoke.execute_role_smoke") as mock_execute:
        mock_execute.return_value = {
            "raw_path": str(tmp_path / "raw.txt"),
            "summary_path": str(tmp_path / "summary.md"),
            "diagnosis": fake_diagnosis,
        }

        matrix = execute_role_smoke_matrix(("WORKER_CORE",), runs_dir=tmp_path)

    assert matrix.mode == "execute"
    assert len(matrix.entries) == 1
    assert matrix.entries[0].result_status == "timeout"
    assert matrix.diagnostics_warnings == ("drift",)
    assert mock_execute.call_args.kwargs["diagnostics"] is diagnostics
