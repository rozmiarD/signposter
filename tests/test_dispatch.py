"""Tests for signposter.dispatch classification logic.

These tests are pure and do not touch the network or GitHub.
"""

from __future__ import annotations

from signposter.dispatch import classify_candidate, extract_label_value
from signposter.scan import LabeledItem


def make_item(number: int, labels: list[str], title: str = "Test item") -> LabeledItem:
    return LabeledItem(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        labels=labels,
        item_type="issue",
    )


def test_extract_label_value():
    labels = ["state:ready", "phase:build", "risk:low", "role:worker", "area:docs"]
    assert extract_label_value(labels, "phase") == "build"
    assert extract_label_value(labels, "state") == "ready"
    assert extract_label_value(labels, "role") == "worker"
    assert extract_label_value(labels, "nonexistent") is None


def test_classify_build_worker_low_risk():
    item = make_item(1, ["state:ready", "phase:build", "role:worker", "risk:low", "area:docs"])
    decision = classify_candidate(item)

    assert decision.proposed_route == "worker"
    assert decision.proposed_gate == "ci"
    assert "build task" in decision.reason.lower()


def test_classify_active_build_worker_medium_risk_keeps_ci_gate():
    item = make_item(
        5,
        [
            "state:active",
            "phase:build",
            "role:worker",
            "risk:medium",
            "area:scheduler",
            "gate:ci",
        ],
    )
    decision = classify_candidate(item)

    assert decision.proposed_route == "worker"
    assert decision.proposed_gate == "ci"


def test_classify_review_reviewer():
    item = make_item(2, ["state:ready", "phase:review", "role:reviewer", "risk:low"])
    decision = classify_candidate(item)

    assert decision.proposed_route == "reviewer"
    assert decision.proposed_gate == "review"


def test_classify_high_risk_fallback():
    item = make_item(3, ["state:ready", "phase:build", "role:worker", "risk:high"])
    decision = classify_candidate(item)

    assert decision.proposed_route == "reviewer"
    assert decision.proposed_gate == "human"


def test_classify_default_ready_state():
    item = make_item(4, ["state:ready", "area:core"])
    decision = classify_candidate(item)

    assert decision.proposed_route == "worker"
    assert decision.proposed_gate == "ci"
