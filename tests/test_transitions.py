"""Pure tests for signposter.transitions planning logic."""

from __future__ import annotations

from signposter.transitions import (
    plan_complete,
    plan_fail,
    plan_release,
)


def make_labels(state: str, gate: str | None = None) -> list[str]:
    labels = [f"state:{state}"]
    if gate:
        labels.append(f"gate:{gate}")
    labels.extend(["phase:build", "risk:low"])
    return labels


def test_plan_release_valid():
    labels = make_labels("active", "ci")
    plan = plan_release(labels, 1)

    assert plan.valid is True
    assert plan.new_state == "ready"
    assert "state:active" in plan.labels_to_remove
    assert "gate:ci" in plan.labels_to_remove
    assert "state:ready" in plan.labels_to_add


def test_plan_release_invalid_not_active():
    labels = make_labels("ready")
    plan = plan_release(labels, 1)

    assert plan.valid is False
    assert "not in state:active" in plan.reason


def test_plan_complete_valid():
    labels = make_labels("active", "review")
    plan = plan_complete(labels, 5)

    assert plan.valid is True
    assert plan.new_state == "done"
    assert "state:done" in plan.labels_to_add
    assert "gate:review" in plan.labels_to_remove


def test_plan_fail_valid():
    labels = make_labels("active", "ci")
    plan = plan_fail(labels, 7)

    assert plan.valid is True
    assert plan.new_state == "failed"
    assert "state:failed" in plan.labels_to_add


def test_plan_fail_invalid():
    labels = make_labels("done")
    plan = plan_fail(labels, 7)

    assert plan.valid is False
