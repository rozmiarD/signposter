"""Tests for signposter.gate decision logic.

Pure tests using string fixtures — no network, no real files.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from signposter.gate import (
    _is_already_integrated_issue,
    evaluate_gate,
    format_gate_report,
    run_gate_dry_run,
)


def test_evaluate_pass_on_clean_success():
    summary = """
**Exit Code:** 0
**Review Findings**
**Evidence Status:** All evidence present.
**Observations:**
- All constraints respected
- No risks/gaps
**Next Steps:** Review complete.
"""
    decision = evaluate_gate(0, summary)
    assert decision.decision == "pass"
    assert "successful" in decision.reason.lower() or "complete" in decision.reason.lower()
    assert decision.proposed_transition == "state:active → state:done"


def test_evaluate_fail_on_nonzero_exit():
    decision = evaluate_gate(1, "Some output")
    assert decision.decision == "fail"
    assert "non-zero" in decision.reason.lower()


def test_evaluate_needs_work_on_missing_evidence():
    summary = """
**Exit Code:** 0
**Evidence Status:** missing required evidence
**Observations:** cannot proceed without more context
"""
    decision = evaluate_gate(0, summary)
    assert decision.decision == "needs-work"
    assert (
        "missing required evidence" in decision.reason
        or "cannot proceed" in decision.reason.lower()
    )


def test_evaluate_needs_work_when_unclear():
    """Conservative default when exit 0 but no strong positive signals."""
    summary = "**Exit Code:** 0\nSome generic output with no clear signals."
    decision = evaluate_gate(0, summary)
    assert decision.decision == "needs-work"
    assert decision.confidence == "low"


def test_evaluate_ci_gate_pass_on_worker_execution_complete():
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Agent:** worker

**Execution complete.**

### 1. Files changed
- `README.md` (only file edited for this task)

### 2. Summary of README change
Added MVP Status section.

### 3. Evidence: `git diff -- README.md`

Code behavior was unchanged.
"""
    decision = evaluate_ci_gate(0, summary)
    assert decision.decision == "pass"
    assert decision.proposed_transition == "state:active → state:done"


def test_evaluate_ci_gate_needs_work_when_worker_output_unclear():
    from signposter.gate import evaluate_ci_gate

    decision = evaluate_ci_gate(0, "**Exit Code:** 0\nGeneric output only.")
    assert decision.decision == "needs-work"
    assert decision.confidence == "low"


# --- HARDENING-025E-B: Already-integrated gate behavior ---


@pytest.mark.parametrize(
    "issue_state,expected",
    [
        ({"state": "CLOSED", "labels": ["state:merged", "phase:build"]}, True),
        ({"state": "CLOSED", "labels": ["state:merged"]}, True),
        ({"state": "OPEN", "labels": ["state:merged"]}, False),
        ({"state": "CLOSED", "labels": ["phase:build"]}, False),
        ({"state": "CLOSED", "labels": []}, False),
        ({"state": "CLOSED", "labels": ["state:active", "gate:ci"]}, False),
    ],
)
def test_is_already_integrated_issue(issue_state, expected):
    assert _is_already_integrated_issue(issue_state) is expected


def test_run_gate_dry_run_returns_not_applicable_for_integrated_issue():
    """CLOSED + state:merged must short-circuit to NOT-APPLICABLE before any gate logic."""
    fake_issue = {
        "number": 6,
        "title": "Smoke test: full lifecycle docs-only issue",
        "state": "CLOSED",
        "labels": ["state:merged", "phase:build", "risk:low"],
    }

    with patch("signposter.gate.fetch_issue_state", return_value=fake_issue):
        # summary_path is never used (short-circuit before load_summary)
        result = run_gate_dry_run(
            "ExatronOmega/signposter", 6, "artifacts/runs/issue-6.summary.md"
        )

    assert result["is_already_integrated"] is True
    assert result["decision"] == "NOT-APPLICABLE"
    assert result["reason"] == "Issue is already closed and integrated."
    assert result["confidence"] == "high"
    assert result["status"] == "completed"
    assert "No gate action is required." in result["notes"]
    assert "No GitHub mutation was performed." in result["notes"]


def test_format_gate_report_for_already_integrated_issue():
    result = {
        "repo": "ExatronOmega/signposter",
        "issue": 6,
        "issue_title": "Smoke test: full lifecycle docs-only issue",
        "current_state": "CLOSED",
        "labels": ["state:merged", "phase:build"],
        "is_already_integrated": True,
        "decision": "NOT-APPLICABLE",
        "reason": "Issue is already closed and integrated.",
        "confidence": "high",
        "status": "completed",
        "notes": [
            "No gate action is required.",
            "No GitHub mutation was performed.",
        ],
    }
    output = format_gate_report(result)

    assert "NOT-APPLICABLE" in output
    assert "Reason: Issue is already closed and integrated." in output
    assert "Status:" in output
    assert "completed" in output
    assert "No gate action is required." in output
    assert "No GitHub mutation was performed." in output
    # Must NOT contain the normal gate validation sections
    assert "Gate Validation:" not in output
    assert "Evidence:" not in output
    assert "WARNING:" not in output


def test_closed_without_state_merged_does_not_report_completed():
    """CLOSED issue without state:merged must NOT take the integrated path."""
    fake_issue = {
        "number": 99,
        "title": "Some closed issue",
        "state": "CLOSED",
        "labels": ["phase:build"],
    }

    with patch("signposter.gate.fetch_issue_state", return_value=fake_issue), \
         patch("signposter.gate.load_summary", return_value="**Exit Code:** 0\nDummy summary"):
        result = run_gate_dry_run(
            "ExatronOmega/signposter", 99, "artifacts/runs/issue-99.summary.md"
        )

    # Should fall through to normal "no gate label" path
    assert result.get("is_already_integrated") is not True
    assert result["decision"] == "needs-work"
    assert "no supported gate label" in result["reason"].lower()


def test_integrated_path_does_not_require_state_active_or_gate_labels():
    """The integrated short-circuit must succeed even without state:active or any gate:* label."""
    fake_issue = {
        "number": 6,
        "title": "Integrated issue",
        "state": "CLOSED",
        "labels": ["state:merged"],
    }

    with patch("signposter.gate.fetch_issue_state", return_value=fake_issue):
        result = run_gate_dry_run("ExatronOmega/signposter", 6, "/nonexistent/summary.md")

    assert result["decision"] == "NOT-APPLICABLE"
    assert result["status"] == "completed"


def test_active_gate_ci_behavior_unchanged_positive():
    """Active gate:ci positive path must continue to work (regression guard)."""
    from signposter.gate import evaluate_ci_gate

    summary = """
**Exit Code:** 0
**Agent:** worker

**Execution complete.**

### 1. Files changed
- `README.md` (only file edited)

### 3. Evidence: `git diff -- README.md`

Code behavior was unchanged.
Scope followed: 100%
Dirty guard: clean
"""
    decision = evaluate_ci_gate(0, summary)
    assert decision.decision == "pass"


def test_active_gate_ci_behavior_unchanged_negative():
    """Active gate:ci negative path must continue to block (regression guard)."""
    from signposter.gate import evaluate_ci_gate

    decision = evaluate_ci_gate(0, "**Exit Code:** 0\nGeneric unclear output.")
    assert decision.decision == "needs-work"


def test_evaluate_ci_gate_pass_on_scoped_lifecycle_watch_code_task():
    """Scoped src+tests worker task with validation evidence should pass CI gate."""
    from signposter.gate import evaluate_ci_gate

    summary = """
# Signposter Execution Summary

**Repository:** ExatronOmega/signposter
**Issue:** #10 — WATCH-001 — Define lifecycle watch CLI contract
**Agent:** worker
**Exit Code:** 0
**Dirty Guard:** clean
**Task execution complete:** yes
**Acceptance:** pass

## Files changed

- src/signposter/cli.py
- tests/test_lifecycle.py

## Implemented behavior

Added lifecycle watch subcommand under existing signposter lifecycle.

## Validation evidence

Targeted validation in isolated worktree passed:

- ruff check src/signposter/cli.py tests/test_lifecycle.py
- pytest tests/test_lifecycle.py -q

Full validation in isolated worktree passed:

- ruff check .
- pytest tests/ -q

Manual CLI smoke passed.

## Safety

No GitHub mutation was performed by the implemented command.
No OpenClaw execution was performed by the implemented command.
No manifest mutation was performed.
No unrelated files were changed.
"""

    decision = evaluate_ci_gate(0, summary)

    assert decision.decision == "pass"
    assert decision.proposed_transition == "state:active → state:done"
    assert "scoped code change evidence" in decision.reason


