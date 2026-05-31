from __future__ import annotations

from signposter.artifact import (
    plan_review_summary,
    plan_worker_summary,
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
