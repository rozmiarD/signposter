from __future__ import annotations

from signposter.artifact import (
    format_worker_artifact_validation,
    plan_review_summary,
    plan_worker_summary,
    validate_worker_summary_artifact,
    write_manual_artifact,
)
from signposter.gate import evaluate_ci_gate
from signposter.review import evaluate_review_gate


def test_worker_summary_plan_is_gate_compatible():
    plan = plan_worker_summary(
        repo="test/repo",
        issue=32,
        changed_files=["src/signposter/artifact.py", "tests/test_artifact.py"],
        implemented_behavior=["Manual artifact command writes deterministic summaries."],
        targeted_validation=[
            "ruff check src/signposter/artifact.py tests/test_artifact.py",
            "python -m pytest tests/test_artifact.py -q",
        ],
        manual_smoke=["signposter artifact write-worker-summary --issue 32"],
    )

    decision = evaluate_ci_gate(0, plan.content)

    assert plan.path == "artifacts/runs/issue-32-worker.summary.md"
    assert decision.decision == "pass"
    assert "No GitHub mutation was performed" in plan.content
    assert "No unrelated files were changed" in plan.content


def test_worker_summary_dry_run_does_not_write(tmp_path):
    plan = plan_worker_summary(
        repo="test/repo",
        issue=32,
        changed_files=["src/signposter/artifact.py", "tests/test_artifact.py"],
        runs_dir=tmp_path,
    )

    wrote = write_manual_artifact(plan, apply=False)

    assert wrote is False
    assert not (tmp_path / "issue-32-worker.summary.md").exists()


def test_worker_summary_apply_writes_file(tmp_path):
    plan = plan_worker_summary(
        repo="test/repo",
        issue=32,
        changed_files=["src/signposter/artifact.py", "tests/test_artifact.py"],
        runs_dir=tmp_path,
    )

    wrote = write_manual_artifact(plan, apply=True)

    path = tmp_path / "issue-32-worker.summary.md"
    assert wrote is True
    assert path.read_text(encoding="utf-8") == plan.content


def test_validate_worker_summary_artifact_passes_formal_summary(tmp_path):
    plan = plan_worker_summary(
        repo="test/repo",
        issue=72,
        changed_files=["src/signposter/artifact.py", "tests/test_artifact.py"],
        targeted_validation=[
            "ruff check src/signposter/artifact.py tests/test_artifact.py",
            "python -m pytest tests/test_artifact.py -q",
        ],
        runs_dir=tmp_path,
    )
    write_manual_artifact(plan, apply=True)

    result = validate_worker_summary_artifact(72, runs_dir=tmp_path)
    out = format_worker_artifact_validation(result)

    assert result.status == "pass"
    assert result.missing == []
    assert result.stale_signal is None
    assert result.raw_exists is False
    assert "raw output artifact not found" in out
    assert "Status:\n  pass" in out


def test_validate_worker_summary_artifact_reports_missing_file(tmp_path):
    result = validate_worker_summary_artifact(72, runs_dir=tmp_path)

    assert result.status == "missing"
    assert result.exists is False
    assert result.missing == ["summary artifact"]


def test_validate_worker_summary_artifact_blocks_incomplete_summary(tmp_path):
    path = tmp_path / "issue-72-worker.summary.md"
    path.write_text("short summary\n**Exit Code:** 0\n", encoding="utf-8")

    result = validate_worker_summary_artifact(72, runs_dir=tmp_path)

    assert result.status == "blocked"
    assert "acceptance" in result.missing
    assert "validation evidence" in result.missing


def test_validate_worker_summary_artifact_blocks_unsafe_marker(tmp_path):
    plan = plan_worker_summary(repo="test/repo", issue=72, runs_dir=tmp_path)
    path = tmp_path / "issue-72-worker.summary.md"
    path.write_text(plan.content + "\nModel unavailable.\n", encoding="utf-8")

    result = validate_worker_summary_artifact(72, runs_dir=tmp_path)

    assert result.status == "blocked"
    assert result.stale_signal == "model unavailable"


def test_validate_worker_summary_artifact_blocks_unsafe_raw_marker(tmp_path):
    plan = plan_worker_summary(repo="test/repo", issue=72, runs_dir=tmp_path)
    write_manual_artifact(plan, apply=True)
    raw = tmp_path / "issue-72-worker.raw.txt"
    raw.write_text("The model is not supported for this account.\n", encoding="utf-8")

    result = validate_worker_summary_artifact(72, runs_dir=tmp_path)
    out = format_worker_artifact_validation(result)

    assert result.status == "blocked"
    assert result.raw_exists is True
    assert result.raw_stale_signal == "model is not supported"
    assert "Raw unsafe marker:" in out
    assert "preserve unsafe backend output separately" in out


def test_review_summary_plan_is_review_gate_compatible(tmp_path):
    plan = plan_review_summary(
        pr=31,
        findings=["CLI planning override is scoped and read-only."],
        reasoning="The reviewer contract is complete and CI was considered.",
        runs_dir=tmp_path,
    )
    write_manual_artifact(plan, apply=True)

    gate = evaluate_review_gate(
        "test/repo",
        31,
        summary_path=plan.path,
        allow_high_risk=True,
    )

    assert gate.gate_pass is True
    assert gate.merge_eligible is True
