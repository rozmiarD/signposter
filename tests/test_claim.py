"""Tests for signposter.claim planning logic.

Pure unit tests — no network access.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from signposter.claim import (
    ClaimDryRunResult,
    ClaimPlan,
    _claim_sort_key,
    build_claim_plan,
)
from signposter.dispatch import DispatchDecision
from signposter.scan import LabeledItem


def make_ready_item(number: int, extra_labels: list[str] | None = None) -> LabeledItem:
    base = ["state:ready"]
    if extra_labels:
        base.extend(extra_labels)
    return LabeledItem(
        number=number,
        title=f"Test claim item #{number}",
        html_url=f"https://github.com/example/repo/issues/{number}",
        labels=base,
        item_type="issue",
    )


def make_decision(item: LabeledItem, **overrides) -> DispatchDecision:
    """Helper to create a minimal DispatchDecision for testing."""
    base = {
        "phase": None,
        "state": "ready",
        "role": None,
        "risk": None,
        "area": None,
        "proposed_route": "worker",
        "proposed_gate": None,
        "reason": "test",
    }
    base.update(overrides)
    return DispatchDecision(item=item, **base)  # type: ignore[arg-type]


def make_plan(item: LabeledItem, risk: str | None = None, phase: str | None = None) -> ClaimPlan:
    decision = make_decision(item, risk=risk, phase=phase)
    return ClaimPlan(
        item=item,
        dispatch=decision,
        lease_owner="local-dry-run-worker",
        proposed_state="active",
        labels_to_remove=["state:ready"],
        labels_to_add=["state:active"],
        reason="test",
    )


def test_claim_sort_key_prefers_low_risk():
    item_high = make_ready_item(10, ["risk:high"])
    item_low = make_ready_item(20, ["risk:low"])

    plan_high = make_plan(item_high, risk="high")
    plan_low = make_plan(item_low, risk="low")

    assert _claim_sort_key(plan_low) < _claim_sort_key(plan_high)


def test_claim_sort_key_prefers_build_over_review():
    item_build = make_ready_item(5, ["phase:build"])
    item_review = make_ready_item(6, ["phase:review"])

    plan_build = make_plan(item_build, phase="build")
    plan_review = make_plan(item_review, phase="review")

    assert _claim_sort_key(plan_build) < _claim_sort_key(plan_review)


def test_claim_sort_key_tie_breaks_by_number():
    item_10 = make_ready_item(10, ["risk:low", "phase:build"])
    item_5 = make_ready_item(5, ["risk:low", "phase:build"])

    plan_10 = make_plan(item_10, risk="low", phase="build")
    plan_5 = make_plan(item_5, risk="low", phase="build")

    assert _claim_sort_key(plan_5) < _claim_sort_key(plan_10)


def test_claim_result_default_limit_behavior():
    """Default limit=1 should select only the first after sorting."""
    plans = [
        make_plan(make_ready_item(1, ["risk:low", "phase:build"]), risk="low", phase="build"),
        make_plan(make_ready_item(2, ["risk:low", "phase:review"]), risk="low", phase="review"),
    ]
    # Simulate what plan_claims would return with limit=1
    result = ClaimDryRunResult(selected=plans[:1], total_claimable=2, limit=1)

    assert len(result.selected) == 1
    assert result.total_claimable == 2
    assert result.limit == 1


def test_claim_result_higher_limit():
    plans = [
        make_plan(make_ready_item(1), risk="low"),
        make_plan(make_ready_item(2), risk="low"),
    ]
    result = ClaimDryRunResult(selected=plans, total_claimable=2, limit=2)

    assert len(result.selected) == 2
    assert result.total_claimable == 2
    assert result.limit == 2


def test_build_claim_plan_uses_shared_ready_rules():
    item = make_ready_item(7, ["phase:build", "risk:high"])
    decision = make_decision(
        item,
        phase="build",
        risk="high",
        proposed_route="reviewer",
        proposed_gate="human",
    )

    plan = build_claim_plan(decision)

    assert plan.item.number == 7
    assert plan.lease_owner == "local-worker"
    assert plan.labels_to_remove == ["state:ready"]
    assert plan.labels_to_add == ["state:active", "gate:human"]


# --- Mutation simulation tests (mocked) ---


def test_perform_claim_mutation_returns_correct_commands():
    """Verify that perform_claim_mutation generates the expected gh commands."""
    from signposter.claim import perform_claim_mutation

    item = make_ready_item(42, ["phase:build", "risk:low", "role:worker"])
    decision = make_decision(
        item, phase="build", risk="low", proposed_route="worker", proposed_gate="ci"
    )
    plan = ClaimPlan(
        item=item,
        dispatch=decision,
        lease_owner="local-dry-run-worker",
        proposed_state="active",
        labels_to_remove=["state:ready"],
        labels_to_add=["state:active", "gate:ci"],
        reason="test",
    )

    commands = perform_claim_mutation(plan, "ExatronOmega/signposter", dry_run=True)

    assert len(commands) == 2
    assert "gh issue edit 42" in commands[0]
    assert "--add-label state:active,gate:ci" in commands[0]
    assert "--remove-label state:ready" in commands[0]
    assert "gh issue comment 42" in commands[1]
    assert "**Signposter:** claimed task for local worker run." in commands[1]
    assert "`state:ready → state:active`" in commands[1]
    assert "`route:worker`" in commands[1]
    assert "`gate:ci`" in commands[1]


def test_claim_apply_audits_comment_before_label_mutation(monkeypatch):
    """Unsafe claim comments must block before the label edit command runs."""
    from signposter.claim import perform_claim_mutation

    item = make_ready_item(42, ["phase:build", "risk:low", "role:worker"])
    decision = make_decision(
        item, phase="build", risk="low", proposed_route="worker", proposed_gate="ci"
    )
    plan = ClaimPlan(
        item=item,
        dispatch=decision,
        lease_owner="local-dry-run-worker",
        proposed_state="active",
        labels_to_remove=["state:ready"],
        labels_to_add=["state:active", "gate:ci"],
        reason="test",
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("subprocess must not run")

    monkeypatch.setattr("signposter.claim.subprocess.run", fake_run)
    monkeypatch.setattr(
        "signposter.claim.format_claim_comment",
        lambda **kwargs: "Signposter claim\n\nCloses #42",
    )

    with pytest.raises(ValueError, match="auto-close keyword"):
        perform_claim_mutation(plan, "ExatronOmega/signposter", dry_run=False)

    assert calls == []


# =============================================================================
# H023D-A: Label preflight before claim apply
# =============================================================================


def test_claim_apply_refuses_when_required_labels_missing():
    """H023D-A: claim --apply must refuse before any mutation if labels are missing."""
    from signposter.claim import ClaimDryRunResult, ClaimPlan, cli_main

    item = LabeledItem(
        number=42, title="Test", html_url="https://example.com/42",
        labels=["state:ready"], item_type="issue",
    )
    decision = DispatchDecision(
        item=item, state="ready", proposed_route="worker",
        proposed_gate=None, phase=None, risk=None, role=None, area=None, reason="test",
    )
    plan = ClaimPlan(
        item=item, dispatch=decision, lease_owner="local-dry-run-worker",
        proposed_state="active", labels_to_remove=["state:ready"],
        labels_to_add=["state:active"], reason="test",
    )
    fake_result = ClaimDryRunResult(selected=[plan], total_claimable=1, limit=1)

    with patch("signposter.claim.plan_claims", return_value=fake_result), \
         patch("signposter.claim.check_labels") as mock_check, \
         patch("signposter.claim.perform_claim_mutation") as mock_mutate:

        mock_check.return_value.missing = ["state:active"]
        mock_check.return_value.error = None

        rc = cli_main("owner/repo", limit=1, apply=True)

    assert rc == 1
    mock_mutate.assert_not_called()  # No mutation attempted


def test_claim_dry_run_does_not_trigger_label_preflight_mutation():
    """Dry-run must never reach the label preflight mutation guard."""
    from signposter.claim import ClaimDryRunResult, cli_main

    fake_result = ClaimDryRunResult(selected=[], total_claimable=0, limit=1)

    with patch("signposter.claim.plan_claims", return_value=fake_result), \
         patch("signposter.claim.check_labels") as mock_check:

        rc = cli_main("owner/repo", limit=1, apply=False)  # dry-run

    assert rc == 0
    mock_check.assert_not_called()  # preflight only runs on apply path


def test_claim_apply_blocks_on_label_preflight_error():
    """Preflight failure (e.g. gh error) must block safely with no mutation."""
    from signposter.claim import ClaimDryRunResult, ClaimPlan, cli_main

    item = LabeledItem(
        number=99, title="X", html_url="u",
        labels=["state:ready"], item_type="issue",
    )
    decision = DispatchDecision(
        item=item, state="ready", proposed_route="worker",
        proposed_gate=None, phase=None, risk=None, role=None, area=None, reason="t",
    )
    plan = ClaimPlan(
        item=item, dispatch=decision, lease_owner="w",
        proposed_state="active",
        labels_to_remove=["state:ready"],
        labels_to_add=["state:active"],
        reason="t",
    )
    fake_result = ClaimDryRunResult(selected=[plan], total_claimable=1, limit=1)

    with patch("signposter.claim.plan_claims", return_value=fake_result), \
         patch("signposter.claim.check_labels") as mock_check, \
         patch("signposter.claim.perform_claim_mutation") as mock_mutate:

        mock_check.return_value.missing = []
        mock_check.return_value.error = "gh label list failed"

        rc = cli_main("owner/repo", limit=1, apply=True)

    assert rc == 1
    mock_mutate.assert_not_called()
