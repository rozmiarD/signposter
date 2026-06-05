from __future__ import annotations

from signposter.artifact import (
    audit_run_artifacts,
    audit_worker_prompt,
    format_run_artifact_audit,
    format_worker_artifact_validation,
    format_worker_prompt_audit,
    plan_review_summary,
    plan_worker_summary,
    validate_worker_summary_artifact,
    write_manual_artifact,
)
from signposter.gate import evaluate_ci_gate
from signposter.review import evaluate_review_gate


def test_audit_run_artifacts_counts_canonical_and_diagnostic_pairs(tmp_path):
    (tmp_path / "issue-7-worker.summary.md").write_text("summary", encoding="utf-8")
    (tmp_path / "issue-7-worker.raw.txt").write_text("raw", encoding="utf-8")
    (tmp_path / "pr-3-reviewer.summary.md").write_text("summary", encoding="utf-8")
    (tmp_path / "pr-3-reviewer.raw.txt").write_text("raw", encoding="utf-8")
    (tmp_path / "issue-7-worker.codex-runtime.summary.md").write_text(
        "runtime summary",
        encoding="utf-8",
    )
    (tmp_path / "issue-7-worker.codex-runtime.raw.txt").write_text(
        "runtime raw",
        encoding="utf-8",
    )
    (tmp_path / "issue-8-worker.raw.txt").write_text("raw only", encoding="utf-8")
    (tmp_path / "loose.log").write_text("unknown", encoding="utf-8")

    result = audit_run_artifacts(runs_dir=tmp_path)
    out = format_run_artifact_audit(result)

    assert result.status == "ready"
    assert result.canonical_pairs == 2
    assert result.diagnostic_pairs == 1
    assert result.raw_without_summary == ("issue-8-worker.raw.txt",)
    assert result.unknown_names == ("loose.log",)
    assert "Signposter Run Artifact Audit" in out
    assert "diagnostic suffixes such as .codex-runtime.*" in out
    assert "No GitHub mutation was performed." in out
    assert "No local artifact was modified." in out


def test_audit_run_artifacts_blocks_missing_runs_dir(tmp_path):
    missing = tmp_path / "missing"

    result = audit_run_artifacts(runs_dir=missing)
    out = format_run_artifact_audit(result)

    assert result.status == "blocked"
    assert result.exists is False
    assert "runs directory is missing" in out
    assert "No GitHub mutation was performed." in out


def test_audit_run_artifacts_reports_unsafe_marker(tmp_path):
    (tmp_path / "issue-7-worker.summary.md").write_text("summary", encoding="utf-8")
    (tmp_path / "issue-7-worker.raw.txt").write_text(
        "Model unavailable.",
        encoding="utf-8",
    )

    result = audit_run_artifacts(runs_dir=tmp_path)
    out = format_run_artifact_audit(result)

    assert result.status == "ready"
    assert result.unsafe_markers == ("issue-7-worker.raw.txt: model unavailable",)
    assert "Unsafe markers:" in out
    assert "issue-7-worker.raw.txt: model unavailable" in out


def test_audit_worker_prompt_passes_required_task_boundary_fields(tmp_path):
    prompt = tmp_path / "issue-246.md"
    prompt.write_text(
        "# Signposter Worker Prompt\n"
        "\n"
        "## Context\n"
        "- Repository: ExatronOmega/signposter\n"
        "- Issue: #246 - H049-039 - Worker prompt quality audit\n"
        "- Labels: phase:build, state:active, risk:low\n"
        "- Route/phase/role/risk/area/gate: worker/build/worker/low/runner/ci\n"
        "- Working directory: ../signposter-work/246\n"
        "\n"
        "## Selected Role Policy\n"
        "- backend: codex-cli\n"
        "- role identity: WORKER_CORE\n"
        "- selected model: openai/gpt-5.4\n"
        "- selected reasoning effort: medium\n"
        "\n"
        "## Prompt Contract\n"
        "- expected output format: concise execution summary\n"
        "- artifact requirements: keep raw backend output local\n"
        "- uncertainty handling: state missing evidence\n"
        "\n"
        "## Issue Body\n"
        "Task body.\n"
        "\n"
        "## Rules\n"
        "- Do not fetch the GitHub URL.\n"
        "- Implement only this scoped issue.\n"
        "\n"
        "## Task\n"
        "Implement only the scoped changes.\n"
        "\n"
        "## Validation\n"
        "- Run targeted validation.\n",
        encoding="utf-8",
    )

    result = audit_worker_prompt(prompt_path=prompt)
    out = format_worker_prompt_audit(result)

    assert result.status == "ready"
    assert result.missing_fields == ()
    assert "Signposter Worker Prompt Audit" in out
    assert "Missing task-boundary fields:\n  none" in out
    assert "No GitHub mutation was performed." in out
    assert "No local prompt or artifact was modified." in out


def test_audit_worker_prompt_blocks_missing_prompt(tmp_path):
    result = audit_worker_prompt(prompt_path=tmp_path / "missing.md")
    out = format_worker_prompt_audit(result)

    assert result.status == "blocked"
    assert result.exists is False
    assert result.missing_fields == ("prompt artifact",)
    assert "exists: no" in out


def test_audit_worker_prompt_reports_missing_task_boundary_fields(tmp_path):
    prompt = tmp_path / "issue-246.md"
    prompt.write_text(
        "# Signposter Worker Prompt\n\n## Context\n- Repository: test/repo\n",
        encoding="utf-8",
    )

    result = audit_worker_prompt(prompt_path=prompt)

    assert result.status == "blocked"
    assert "issue context" in result.missing_fields
    assert "selected role policy section" in result.missing_fields
    assert "validation section" in result.missing_fields


def test_audit_worker_prompt_reports_repeated_policy_lines(tmp_path):
    prompt = tmp_path / "issue-246.md"
    repeated = "- Do not broaden scope beyond the current issue."
    prompt.write_text(
        "# Signposter Worker Prompt\n"
        "\n"
        "## Context\n"
        "- Repository: ExatronOmega/signposter\n"
        "- Issue: #246 - H049-039 - Worker prompt quality audit\n"
        "- Labels: phase:build, state:active, risk:low\n"
        "- Route/phase/role/risk/area/gate: worker/build/worker/low/runner/ci\n"
        "- Working directory: ../signposter-work/246\n"
        "\n"
        "## Selected Role Policy\n"
        "- backend: codex-cli\n"
        "- role identity: WORKER_CORE\n"
        "- selected model: openai/gpt-5.4\n"
        "- selected reasoning effort: medium\n"
        "\n"
        "## Prompt Contract\n"
        "- expected output format: concise execution summary\n"
        "- artifact requirements: keep raw backend output local\n"
        "- uncertainty handling: state missing evidence\n"
        "\n"
        "## Issue Body\n"
        "Task body.\n"
        "\n"
        "## Rules\n"
        "- Do not fetch the GitHub URL.\n"
        "- Implement only this scoped issue.\n"
        f"{repeated}\n"
        f"{repeated}\n"
        "\n"
        "## Task\n"
        "Implement only the scoped changes.\n"
        "\n"
        "## Validation\n"
        "- Run targeted validation.\n",
        encoding="utf-8",
    )

    result = audit_worker_prompt(prompt_path=prompt)

    assert result.status == "ready"
    assert result.repeated_lines == (f"2x {repeated}",)


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


def test_worker_summary_plan_includes_manual_takeover_provenance():
    plan = plan_worker_summary(repo="test/repo", issue=32)
    decision = evaluate_ci_gate(0, plan.content)

    assert decision.decision == "pass"
    assert "## Manual takeover provenance" in plan.content
    assert "Takeover agent: human/operator" in plan.content
    assert "Takeover artifact: parser-compatible worker summary." in plan.content
    assert "Runtime artifact handling: raw backend output remains local" in plan.content
    assert "Validation provenance: signposter.validation-result.v1 records above." in plan.content
    assert "GitHub comment provenance: bounded report excerpt only." in plan.content


def test_worker_summary_docs_only_plan_adds_preflight_fields(tmp_path):
    plan = plan_worker_summary(
        repo="test/repo",
        issue=33,
        changed_files=["docs/operator-lifecycle-runbook.md", "README.md"],
        implemented_behavior=["Documentation-only worker artifact fields were verified."],
        runs_dir=tmp_path,
    )
    write_manual_artifact(plan, apply=True)

    validation = validate_worker_summary_artifact(33, runs_dir=tmp_path)
    decision = evaluate_ci_gate(0, plan.content)

    assert validation.status == "pass"
    assert "Docs-only scope: yes" in plan.content
    assert "Changed files are documentation-only: yes" in plan.content
    assert "Code behavior unchanged: yes" in plan.content
    assert "Scope stayed inside requested documentation task: yes" in plan.content
    assert "Dirty guard: clean" in plan.content
    assert "No code changes" not in plan.content
    assert "No scope broadening" not in plan.content
    assert decision.decision == "pass"


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


def test_validate_worker_summary_artifact_requires_schema_fields(tmp_path):
    path = tmp_path / "issue-72-worker.summary.md"
    path.write_text(
        "# Signposter Execution Summary\n"
        "**Exit Code:** 0\n"
        "**Acceptance:** pass\n"
        "## Validation evidence\n"
        "Targeted validation passed\n"
        "Full validation passed\n"
        "## Safety\n"
        "No GitHub mutation was performed.\n"
        "No unrelated files were changed.\n",
        encoding="utf-8",
    )

    result = validate_worker_summary_artifact(72, runs_dir=tmp_path)

    assert result.status == "blocked"
    assert "repository" in result.missing
    assert "agent" in result.missing
    assert "dirty guard" in result.missing
    assert "gate recommendation" in result.missing


def test_validate_worker_summary_artifact_requires_docs_only_fields(tmp_path):
    plan = plan_worker_summary(
        repo="test/repo",
        issue=73,
        changed_files=["docs/operator-lifecycle-runbook.md"],
        runs_dir=tmp_path,
    )
    text = plan.content.replace(
        "## Docs-only preflight fields\n\n"
        "Docs-only scope: yes\n"
        "Changed files are documentation-only: yes\n"
        "Code behavior unchanged: yes\n"
        "Scope stayed inside requested documentation task: yes\n"
        "Dirty guard: clean\n\n",
        "",
    )
    (tmp_path / "issue-73-worker.summary.md").write_text(text, encoding="utf-8")

    result = validate_worker_summary_artifact(73, runs_dir=tmp_path)

    assert result.status == "blocked"
    assert "documentation-only file boundary" in result.missing
    assert "non-code behavior boundary" in result.missing


def test_validate_worker_summary_artifact_does_not_treat_discussion_as_docs_only(tmp_path):
    plan = plan_worker_summary(
        repo="test/repo",
        issue=74,
        changed_files=["src/signposter/artifact.py", "tests/test_artifact.py"],
        implemented_behavior=[
            "Docs-only summaries are discussed without changing this code task boundary.",
        ],
        runs_dir=tmp_path,
    )
    write_manual_artifact(plan, apply=True)

    result = validate_worker_summary_artifact(74, runs_dir=tmp_path)

    assert result.status == "pass"
    assert result.missing == []


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


def test_validate_worker_summary_allows_preserved_diagnostic_runtime_pair(tmp_path):
    plan = plan_worker_summary(repo="test/repo", issue=72, runs_dir=tmp_path)
    write_manual_artifact(plan, apply=True)
    (tmp_path / "issue-72-worker.codex-runtime.summary.md").write_text(
        "runtime diagnostic summary",
        encoding="utf-8",
    )
    (tmp_path / "issue-72-worker.codex-runtime.raw.txt").write_text(
        "The model is not supported for this account.\n",
        encoding="utf-8",
    )

    validation = validate_worker_summary_artifact(72, runs_dir=tmp_path)
    audit = audit_run_artifacts(runs_dir=tmp_path)
    out = format_run_artifact_audit(audit)

    assert validation.status == "pass"
    assert validation.raw_exists is False
    assert any("raw output artifact not found" in item for item in validation.guidance)
    assert audit.diagnostic_pairs == 1
    assert (
        "issue-72-worker.codex-runtime.raw.txt: model is not supported"
        in audit.unsafe_markers
    )
    assert "retained diagnostic raw/summary pairs: 1" in out
    assert "diagnostic suffixes such as .codex-runtime.*" in out


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
