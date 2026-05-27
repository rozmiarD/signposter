"""Tests for signposter.report module.

Pure tests with mocked subprocess for gh calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from signposter.report import (
    derive_raw_path,
    format_comment,
    load_raw_output,
    load_summary,
    post_comment,
)


def test_derive_raw_path():
    summary = Path("artifacts/runs/issue-2-reviewer.summary.md")
    raw = derive_raw_path(summary)
    assert raw.name == "issue-2-reviewer.raw.txt"
    assert str(raw) == "artifacts/runs/issue-2-reviewer.raw.txt"


def test_format_comment_has_clear_artifact_paths():
    summary = """# Signposter Execution Summary
**Agent:** reviewer
**Exit Code:** 0
**Output Size:** 13 lines, 865 bytes
**Prompt Artifact:** artifacts/prompts/issue-2.md
"""
    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        2,
        summary_path="artifacts/runs/issue-2-reviewer.summary.md",
        raw_path="artifacts/runs/issue-2-reviewer.raw.txt",
        prompt_path="artifacts/prompts/issue-2.md",
    )

    assert "- **Summary:** `artifacts/runs/issue-2-reviewer.summary.md`" in body
    assert "- **Raw output:** `artifacts/runs/issue-2-reviewer.raw.txt`" in body
    assert "- **Prompt used:** `artifacts/prompts/issue-2.md`" in body
    assert "Key Excerpt" in body


def test_format_comment_uses_raw_content_for_excerpt():
    summary = "**Agent:** reviewer\n**Exit Code:** 0"
    raw = (
        "[agents/tool-policy] tool policy removed...\n"
        "**Review Findings — Issue #2**\n"
        "**Evidence Status:** Identical to prior review.\n"
        "**Observations:**\n- No changes to scan output\n"
        "**Next Steps:** Await working_dir preparation"
    )

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        2,
        summary_path="artifacts/runs/issue-2-reviewer.summary.md",
        raw_path="artifacts/runs/issue-2-reviewer.raw.txt",
        raw_content=raw,
    )

    assert "**Review Findings — Issue #2**" in body
    assert "No changes to scan output" in body
    assert "Key Excerpt (first ~20 lines or 1500 chars)" in body
    assert "... (truncated)" not in body  # short content


def test_format_comment_shows_missing_artifacts():
    summary = "**Agent:** reviewer\n**Exit Code:** 0"

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        2,
        summary_path="artifacts/runs/issue-2-reviewer.summary.md",
        raw_path=None,  # deliberately missing
        prompt_path=None,
    )

    assert "- **Raw output:** missing" in body
    assert "- **Prompt used:** missing" in body


@patch("signposter.report.subprocess.run")
def test_post_comment_dry_run(mock_run):
    body = "Test body"
    cmds = post_comment("ExatronOmega/signposter", 2, body, dry_run=True)

    assert len(cmds) == 1
    assert "gh issue comment 2" in cmds[0]
    assert "dry-run" in cmds[0]
    mock_run.assert_not_called()


@patch("signposter.report.subprocess.run")
def test_post_comment_apply_calls_gh(mock_run):
    mock_run.return_value = type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

    body = "Test body for apply"
    cmds = post_comment("ExatronOmega/signposter", 2, body, dry_run=False)

    mock_run.assert_called_once()
    assert any("gh" in str(part) for part in cmds)


def test_load_summary_missing_raises(tmp_path: Path):
    missing = tmp_path / "nonexistent.md"
    with pytest.raises(FileNotFoundError):
        load_summary(missing)


def test_load_raw_output_missing_returns_none(tmp_path: Path):
    missing = tmp_path / "nope.raw.txt"
    assert load_raw_output(missing) is None
