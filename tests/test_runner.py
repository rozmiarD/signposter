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


# --- Prompt rendering tests ---


def make_runner_plan_for_test(role: str, phase: str, number: int = 42):
    from signposter.runner import RunnerPlan

    item = make_item(number, ["state:ready", f"phase:{phase}", f"role:{role}"])
    dispatch = make_dispatch(role=role, phase=phase)

    # Create a minimal dispatch with the item attached
    dispatch = DispatchDecision(
        item=item,
        phase=phase,
        state="ready",
        role=role,
        risk="low",
        area="core",
        proposed_route=role,
        proposed_gate="review",
        reason="test",
    )

    return RunnerPlan(
        item=item,
        dispatch=dispatch,
        proposed_runner="openclaw",
        proposed_profile=role,
        proposed_working_dir=f"~/work/{number}",
        proposed_prompt_path=f"artifacts/prompts/issue-{number}.md",
        proposed_command_shape="openclaw run ...",
        reason="test",
    )


def test_render_prompt_contains_key_sections():
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review", number=2)
    content = render_prompt(plan, "ExatronOmega/signposter")

    assert "**Repository:** ExatronOmega/signposter" in content
    assert "**Issue:** #2" in content
    assert "role:   reviewer" in content
    assert "phase:  review" in content
    assert "gate:   review" in content
    assert "profile: reviewer" in content
    assert "Do not broaden scope" in content
    assert "Do not mutate GitHub unless explicitly instructed" in content
    assert "review the issue/request and propose next steps" in content.lower()


def test_render_prompt_role_specific_instruction():
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review")
    content = render_prompt(plan, "test/repo")

    assert "review the issue/request and propose next steps" in content.lower()
    assert "do not edit files yet" in content.lower()
