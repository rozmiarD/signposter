"""Pure tests for signposter.runner planning logic."""

from __future__ import annotations

from signposter.dispatch import DispatchDecision
from signposter.runner import _select_runner_and_profile
from signposter.scan import LabeledItem


def make_item(number: int, labels: list[str]) -> LabeledItem:
    return LabeledItem(
        number=number,
        title=f"Test runner item #{number}",
        html_url=f"https://github.com/example/repo/issues/{number}",
        labels=labels,
        item_type="issue",
    )


def make_dispatch(**kwargs) -> DispatchDecision:
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
    base.update(kwargs)
    item = make_item(99, [])
    return DispatchDecision(item=item, **base)  # type: ignore[arg-type]


def test_select_runner_profile_worker_build():
    d = make_dispatch(role="worker", phase="build")
    runner, profile = _select_runner_and_profile(d)
    assert runner == "openclaw"
    assert profile == "worker"


def test_select_runner_profile_reviewer_review():
    d = make_dispatch(role="reviewer", phase="review")
    runner, profile = _select_runner_and_profile(d)
    assert runner == "openclaw"
    assert profile == "reviewer"


def test_select_runner_profile_planner_plan():
    d = make_dispatch(role="planner", phase="plan")
    runner, profile = _select_runner_and_profile(d)
    assert runner == "openclaw"
    assert profile == "planner"


def test_select_runner_profile_gatekeeper():
    d = make_dispatch(role="gatekeeper")
    runner, profile = _select_runner_and_profile(d)
    assert runner == "openclaw"
    assert profile == "gatekeeper"


def test_select_runner_profile_default():
    d = make_dispatch(role="unknown", phase="unknown")
    runner, profile = _select_runner_and_profile(d)
    assert runner == "openclaw"
    assert profile == "worker"
