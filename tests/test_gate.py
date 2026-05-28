"""Tests for signposter.gate decision logic.

Pure tests using string fixtures — no network, no real files.
"""

from __future__ import annotations

from signposter.gate import evaluate_gate


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
