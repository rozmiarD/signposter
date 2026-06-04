"""Tests for signposter.report module.

Pure tests with mocked subprocess for gh calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from signposter.artifact import build_worker_summary
from signposter.report import (
    REPORT_COMMENT_MAX_CHARS,
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


def test_format_comment_redacts_secret_in_structured_summary_excerpt():
    token = "github_pat_" + ("A" * 30)
    summary = f"""# Signposter Execution Summary
**Agent:** worker
**Exit Code:** 0

## Scoped completion evidence

PASS - scoped report behavior is complete.

## Validation evidence

- token seen in local output: {token}
"""

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        2,
        summary_path="artifacts/runs/issue-2-worker.summary.md",
        raw_path="artifacts/runs/issue-2-worker.raw.txt",
        raw_content="raw output should not be selected",
    )

    assert token not in body
    assert "[REDACTED:github-token]" in body
    assert "raw output should not be selected" not in body


def test_format_comment_rejects_auto_close_keyword_in_structured_excerpt():
    summary = """# Signposter Execution Summary
**Agent:** worker
**Exit Code:** 0

## Scoped completion evidence

Fixes #123
"""

    with pytest.raises(ValueError, match="auto-close keyword"):
        format_comment(
            summary,
            "ExatronOmega/signposter",
            2,
            summary_path="artifacts/runs/issue-2-worker.summary.md",
        )


def test_format_comment_body_is_bounded_for_huge_summary_and_raw():
    huge_validation = "\n".join(f"- validation evidence line {i}" for i in range(500))
    summary = f"""# Signposter Execution Summary
**Agent:** worker
**Exit Code:** 0

## Scoped completion evidence

PASS — report body guard implemented.

## Validation evidence

{huge_validation}
"""
    raw = "\n".join(f"raw execution line {i}" for i in range(2000))

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        243,
        summary_path="artifacts/runs/issue-243-worker.summary.md",
        raw_path="artifacts/runs/issue-243-worker.raw.txt",
        raw_content=raw,
    )

    assert len(body) <= REPORT_COMMENT_MAX_CHARS
    assert "Signposter Runner Report" in body
    assert "Key Evidence Excerpt (bounded)" in body
    assert "omitted; excerpt limited" in body
    assert "raw execution line 1999" not in body


def test_format_comment_without_structured_summary_bounds_large_raw_log():
    summary = "**Agent:** worker\n**Exit Code:** 0"
    raw = "\n".join(f"raw execution line {i} {'x' * 90}" for i in range(500))

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        244,
        summary_path="artifacts/runs/issue-244-worker.summary.md",
        raw_path="artifacts/runs/issue-244-worker.raw.txt",
        raw_content=raw,
    )

    assert len(body) <= REPORT_COMMENT_MAX_CHARS
    assert "raw execution line 0" in body
    assert "raw execution line 499" not in body
    assert "omitted; excerpt limited" in body


def test_format_comment_bounds_oversized_metadata():
    huge_value = "x" * (REPORT_COMMENT_MAX_CHARS * 2)
    summary = f"""# Signposter Execution Summary
**Agent:** worker {huge_value}
**Exit Code:** 0
"""

    body = format_comment(
        summary,
        "ExatronOmega/signposter",
        243,
        summary_path=f"artifacts/runs/{huge_value}/issue-243-worker.summary.md",
        raw_path=f"artifacts/runs/{huge_value}/issue-243-worker.raw.txt",
        prompt_path=f"artifacts/prompts/{huge_value}/issue-243.md",
        raw_content=huge_value,
    )

    assert len(body) <= REPORT_COMMENT_MAX_CHARS
    assert "Signposter Runner Report" in body
    assert "... (truncated)" in body
    assert huge_value not in body


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


@patch("signposter.report.post_comment")
def test_report_main_dry_run_passes_dry_run_to_post_comment(
    mock_post,
    tmp_path: Path,
    capsys,
):
    summary = tmp_path / "issue-72-worker.summary.md"
    summary.write_text(
        build_worker_summary(
            repo="test/repo",
            issue=72,
            changed_files=["src/signposter/report.py"],
            implemented_behavior=["Report dry-run test summary is schema-compatible."],
            targeted_validation=["python -m pytest tests/test_report.py -q"],
        ),
        encoding="utf-8",
    )
    mock_post.return_value = ["gh issue comment 72 -R test/repo --body ... # dry-run"]

    exit_code = report_main("test/repo", 72, summary, apply=False)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "=== Signposter Report (dry-run mode)" in out
    assert "=== Dry-run: No GitHub mutation performed ===" in out
    assert mock_post.call_args.kwargs["dry_run"] is True


@patch("signposter.report.post_comment")
def test_report_main_blocks_malformed_worker_summary_before_comment(
    mock_post,
    tmp_path: Path,
    capsys,
):
    summary = tmp_path / "issue-73-worker.summary.md"
    summary.write_text(
        "# Signposter Execution Summary\n"
        "**Exit Code:** 0\n"
        "\n"
        "## Scoped completion evidence\n"
        "\n"
        "PASS - incomplete report summary.\n",
        encoding="utf-8",
    )

    exit_code = report_main("test/repo", 73, summary, apply=True)

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Report blocked: worker summary validation did not pass." in out
    assert "Signposter Worker Artifact Validation" in out
    assert "repair worker summary fields before gate or complete" in out
    assert "No GitHub mutation was performed." in out
    assert "signposter artifact validate-worker-summary --issue 73" in out
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
    assert (
        "... (omitted; excerpt limited to 3 lines / 100 chars; "
        "omitted 22 lines / 169 chars)"
    ) in excerpt


def test_make_bounded_excerpt_reports_line_and_char_omissions():
    raw = "alpha\nbravo\ncharlie\ndelta"

    excerpt = _make_bounded_excerpt(raw, max_lines=2, max_chars=20)

    assert excerpt.splitlines() == [
        "alpha",
        "bravo",
        "... (omitted; excerpt limited to 2 lines / 20 chars; omitted 2 lines / 14 chars)",
    ]


def test_make_bounded_excerpt_truncates_single_long_line_by_char_budget():
    raw = "abcdef\nsecond"

    excerpt = _make_bounded_excerpt(raw, max_lines=5, max_chars=3)

    assert excerpt.splitlines() == [
        "abc",
        "... (omitted; excerpt limited to 5 lines / 3 chars; omitted 1 lines / 10 chars)",
    ]


def test_make_bounded_excerpt_zero_budget_still_reports_omission():
    raw = "alpha\nbravo"

    excerpt = _make_bounded_excerpt(raw, max_lines=0, max_chars=0)

    assert excerpt == (
        "... (omitted; excerpt limited to 0 lines / 0 chars; "
        "omitted 2 lines / 11 chars)"
    )


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
