"""Tests for signposter.report module.

Pure tests with mocked subprocess for gh calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from signposter.report import (
    _make_bounded_excerpt,
    derive_raw_path,
    find_report_artifact_safety_signal,
    format_comment,
    load_raw_output,
    load_summary,
    post_comment,
    report_main,
    strip_ansi,
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
    assert "Key Evidence Excerpt (bounded)" in body


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
    assert "Key Evidence Excerpt (bounded)" in body
    assert "omitted; excerpt limited" not in body  # short content


def test_format_comment_prefers_structured_summary_evidence_over_raw_noise():
    summary = """# Signposter Execution Summary
**Agent:** worker
**Exit Code:** 0

## Scoped completion evidence

PASS — scoped report behavior is complete.

## Validation evidence

- `pytest tests/test_report.py -q`

## Safety

No GitHub mutation was performed.
"""
    raw = "noisy provider banner\nraw line that should not be selected"

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        2,
        summary_path="artifacts/runs/issue-2-worker.summary.md",
        raw_path="artifacts/runs/issue-2-worker.raw.txt",
        raw_content=raw,
    )

    assert "PASS — scoped report behavior is complete." in body
    assert "pytest tests/test_report.py -q" in body
    assert "noisy provider banner" not in body


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
    body = "Signposter test body"
    cmds = post_comment("ExatronOmega/signposter", 2, body, dry_run=True)

    assert len(cmds) == 1
    assert "gh issue comment 2" in cmds[0]
    assert "dry-run" in cmds[0]
    mock_run.assert_not_called()


@patch("signposter.report.subprocess.run")
def test_post_comment_apply_calls_gh(mock_run):
    mock_run.return_value = type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

    body = "Signposter test body for apply"
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


def test_find_report_artifact_safety_signal_checks_summary_and_raw():
    signal = find_report_artifact_safety_signal(
        "clean summary",
        "Provider unavailable during failover.",
    )

    assert signal == "provider unavailable"


@patch("signposter.report.post_comment")
def test_report_main_blocks_stale_artifact_before_github_comment(mock_post, tmp_path: Path, capsys):
    summary = tmp_path / "issue-71-worker.summary.md"
    summary.write_text(
        "# Signposter Execution Summary\n"
        "**Exit Code:** 0\n"
        "Model unavailable; fallback provider failed.\n",
        encoding="utf-8",
    )

    exit_code = report_main("test/repo", 71, summary, apply=True)

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Report blocked" in out
    assert "model unavailable" in out
    assert "No GitHub mutation was performed." in out
    mock_post.assert_not_called()


# --- ANSI sanitization tests (HARDENING-002) ---


def test_strip_ansi_removes_basic_colors():
    text = "\x1b[36mHello\x1b[39m World"
    assert strip_ansi(text) == "Hello World"


def test_strip_ansi_removes_multiple_sequences():
    text = "\x1b[1;32mBold Green\x1b[0m normal \x1b[31mRed\x1b[0m"
    assert strip_ansi(text) == "Bold Green normal Red"


def test_strip_ansi_preserves_markdown_and_backticks():
    text = "**bold** `code` \x1b[36mcolored\x1b[0m text"
    assert strip_ansi(text) == "**bold** `code` colored text"


def test_make_bounded_excerpt_strips_ansi():
    raw_with_ansi = "\x1b[36mLine 1\x1b[0m\n\x1b[32mLine 2\x1b[0m\nLine 3"
    excerpt = _make_bounded_excerpt(raw_with_ansi, max_lines=5)

    assert "\x1b" not in excerpt
    assert "Line 1" in excerpt
    assert "Line 2" in excerpt


def test_make_bounded_excerpt_uses_clear_omission_marker():
    raw = "\n".join(f"line {i}" for i in range(25))

    excerpt = _make_bounded_excerpt(raw, max_lines=3, max_chars=100)

    assert "line 0" in excerpt
    assert "line 3" not in excerpt
    assert "... (omitted; excerpt limited to 3 lines / 100 chars)" in excerpt


def test_format_comment_excerpt_has_no_ansi():
    """End-to-end: ANSI in raw_content must not leak into the GitHub comment body."""
    summary = "**Agent:** worker\n**Exit Code:** 0"
    raw_with_ansi = (
        "\x1b[36m[worker] Starting...\x1b[0m\n"
        "\x1b[1;33mWarning\x1b[0m: something happened\n"
        "Normal line\n"
        "\x1b[31mError\x1b[0m line"
    )

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        42,
        summary_path="artifacts/runs/issue-42.summary.md",
        raw_path="artifacts/runs/issue-42.raw.txt",
        raw_content=raw_with_ansi,
    )

    # No ANSI escape characters should be present anywhere in the comment
    assert "\x1b" not in body
    assert "\x1B" not in body
    # Actual content should still be there
    assert "[worker] Starting..." in body
    assert "Warning: something happened" in body
    assert "Normal line" in body


@patch("signposter.report.subprocess.run")
def test_post_comment_rejects_unsafe_body_before_subprocess(mock_run):
    body = "Signposter report\n\nFixes #2"

    with pytest.raises(ValueError, match="auto-close keyword"):
        post_comment("ExatronOmega/signposter", 2, body, dry_run=False)

    mock_run.assert_not_called()


@patch("signposter.report.subprocess.run")
def test_post_comment_redacts_secret_body_before_subprocess(mock_run):
    mock_run.return_value = type("obj", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()
    token = "github_pat_" + ("A" * 30)

    post_comment("ExatronOmega/signposter", 2, f"Signposter report\n\n{token}", dry_run=False)

    sent_cmd = mock_run.call_args.args[0]
    body = sent_cmd[sent_cmd.index("--body") + 1]
    assert token not in body
    assert "[REDACTED:github-token]" in body


def test_format_comment_redacts_secret_in_excerpt():
    summary = "**Agent:** worker\n**Exit Code:** 0"
    token = "github_pat_" + ("A" * 30)

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        42,
        summary_path="artifacts/runs/issue-42.summary.md",
        raw_path="artifacts/runs/issue-42.raw.txt",
        raw_content=f"Signposter output\n{token}",
    )

    assert token not in body
    assert "[REDACTED:github-token]" in body
