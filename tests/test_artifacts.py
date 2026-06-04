"""Tests for artifact locality guarantees across GitHub-facing reports."""

from __future__ import annotations

from signposter.report import format_comment


def test_raw_artifact_path_is_reported_but_full_raw_output_stays_local():
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

    assert "- **Raw output:** `artifacts/runs/issue-404-worker.raw.txt`" in body
    assert "(full log, stored locally)" in body
    assert "Full execution logs remain local only" in body
    assert "raw execution line 0" in body
    assert raw_tail not in body
    assert "omitted; excerpt limited" in body
