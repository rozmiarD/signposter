"""Tests for artifact locality guarantees across GitHub-facing reports."""

from __future__ import annotations

from signposter.report import format_comment


def test_raw_artifact_path_stays_local_with_bounded_raw_excerpt():
    raw_tail = "RAW_ARTIFACT_TAIL_SHOULD_STAY_LOCAL"
    raw = "\n".join(
        [
            "raw execution line 0",
            *(f"raw execution line {i} {'x' * 90}" for i in range(1, 160)),
            raw_tail,
        ]
    )

    body = format_comment(
        "**Agent:** worker\n**Exit Code:** 0",
        "ExatronOmega/signposter",
        404,
        summary_path="artifacts/runs/issue-404-worker.summary.md",
        raw_path="artifacts/runs/issue-404-worker.raw.txt",
        raw_content=raw,
    )

    assert "artifacts/runs/issue-404-worker.raw.txt" not in body
    assert "Local artifacts remain local-only; no lifecycle transition is implied." in body
    assert "Full execution logs remain local only" in body
    assert "raw execution line 0" in body
    assert raw_tail not in body
    assert "omitted; excerpt limited" in body


def test_runtime_raw_secret_is_redacted_from_report_comment():
    token = "github_pat_" + ("A" * 30)
    raw = "\n".join(
        [
            "runtime banner",
            f"captured token: {token}",
            "runtime tail",
        ]
    )

    body = format_comment(
        "**Agent:** worker\n**Exit Code:** 0",
        "ExatronOmega/signposter",
        405,
        summary_path="artifacts/runs/issue-405-worker.summary.md",
        raw_path="artifacts/runs/issue-405-worker.raw.txt",
        raw_content=raw,
    )

    assert token not in body
    assert "[REDACTED:github-token]" in body
    assert "artifacts/runs/issue-405-worker.raw.txt" not in body
    assert "Local artifacts remain local-only; no lifecycle transition is implied." in body


def test_worker_summary_includes_validation_result_records(tmp_path):
    from signposter.artifact import build_worker_summary, validate_worker_summary_artifact

    summary = build_worker_summary(
        repo="ExatronOmega/signposter",
        issue=419,
        changed_files=["src/signposter/artifact.py", "tests/test_artifacts.py"],
        implemented_behavior=["Validation result records were added."],
        targeted_validation=["ruff check src/signposter/artifact.py tests/test_artifacts.py"],
        full_validation=["python -m pytest tests/ -q"],
        manual_smoke=["Artifact schema is represented in the worker summary."],
    )

    assert "## Validation result records" in summary
    assert "Schema: signposter.validation-result.v1" in summary
    assert "Fields: scope, status, command" in summary
    assert "- scope: targeted" in summary
    assert "- scope: full" in summary
    assert "  status: passed" in summary
    assert (
        "  command: `ruff check src/signposter/artifact.py tests/test_artifacts.py`"
        in summary
    )

    path = tmp_path / "issue-419-worker.summary.md"
    path.write_text(summary, encoding="utf-8")

    result = validate_worker_summary_artifact(419, summary_path=path)

    assert result.status == "pass"
    assert result.missing == []
