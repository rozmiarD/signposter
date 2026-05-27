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
    # New structure assertions
    assert "## Role Profile" in content
    assert "## Private Repository Rule" in content
    assert "Do not fetch the GitHub URL" in content


def test_render_prompt_role_specific_instruction():
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review")
    content = render_prompt(plan, "test/repo")

    assert "# Reviewer Profile" in content
    assert "Do not fetch private GitHub URLs" in content


# --- Post-claim freshness tests ---


def test_render_prompt_reflects_post_claim_labels():
    """Prompt artifact must reflect current labels after claim (state:active + gate:review)."""
    from signposter.dispatch import classify_candidate
    from signposter.runner import RunnerPlan, render_prompt

    # Simulate post-claim item (as it would appear after refresh)
    post_claim_item = make_item(
        2,
        ["phase:review", "state:active", "risk:low", "role:reviewer", "area:core", "gate:review"],
    )
    fresh_dispatch = classify_candidate(post_claim_item)

    plan = RunnerPlan(
        item=post_claim_item,
        dispatch=fresh_dispatch,
        proposed_runner="openclaw",
        proposed_profile="reviewer",
        proposed_working_dir="~/work/2",
        proposed_prompt_path="artifacts/prompts/issue-2.md",
        proposed_command_shape="openclaw run ...",
        reason="post-claim refresh test",
    )

    content = render_prompt(plan, "ExatronOmega/signposter")

    # Must show post-claim state, not stale ready state
    assert "state:active" in content
    assert "gate:review" in content
    assert "state:ready" not in content
    assert "**Labels:** phase:review, state:active" in content  # new format
    assert "## Private Repository Rule" in content


# --- BOOTSTRAP-019A rich prompt tests ---

def test_render_prompt_includes_private_repo_rule():
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review", number=2)
    content = render_prompt(plan, "ExatronOmega/signposter")

    assert "Do not fetch the GitHub URL. This is a private repository." in content
    assert "Use only the embedded issue context" in content


def test_render_prompt_includes_reviewer_profile():
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review")
    content = render_prompt(plan, "test/repo")

    assert "# Reviewer Profile" in content
    assert "You are the Signposter reviewer." in content
    assert "Do not fetch private GitHub URLs." in content


def test_render_prompt_embeds_issue_body_or_empty():
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review")
    content = render_prompt(plan, "test/repo")  # no context provided

    assert "Issue body:" in content  # either "empty" or fallback message


def test_render_prompt_no_longer_relies_on_url_as_primary_context():
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review", number=2)
    content = render_prompt(plan, "ExatronOmega/signposter")

    # The prompt should tell the agent not to rely on fetching the URL
    assert "Do not fetch the GitHub URL" in content
    # URL is present only as reference
    assert "URL (reference only)" in content


# --- BOOTSTRAP-019B Evidence Bundle tests ---

def test_render_prompt_includes_evidence_bundle_for_reviewer():
    """For reviewer role, the prompt must contain an Evidence Bundle section."""
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review", number=2)
    # Pass a minimal evidence bundle
    evidence = {
        "scan": "[MOCK SCAN] Issue #2 state:active",
        "note": "Use the embedded evidence below. Do not fetch GitHub URLs.",
    }
    content = render_prompt(plan, "ExatronOmega/signposter", evidence_bundle=evidence)

    assert "## Evidence Bundle" in content
    assert "Use the embedded evidence below. Do not fetch GitHub URLs." in content
    assert "[MOCK SCAN]" in content


def test_render_prompt_embeds_scan_output():
    """Scan output can be embedded via evidence_bundle."""
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review")
    evidence = {"scan": "Signposter Scan Report\nCandidate Items (1): #2 state:active"}
    content = render_prompt(plan, "test/repo", evidence_bundle=evidence)

    assert "Evidence Bundle" in content
    assert "Signposter Scan Report" in content


def test_render_prompt_private_rule_still_present_with_evidence():
    """Private repo no-fetch rule must remain even when Evidence Bundle is present."""
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review", number=2)
    evidence = {"scan": "mock"}
    content = render_prompt(plan, "ExatronOmega/signposter", evidence_bundle=evidence)

    assert "Do not fetch the GitHub URL. This is a private repository." in content
    assert "## Evidence Bundle" in content


def test_render_prompt_evidence_includes_prompt_preview():
    """Reviewer evidence should include prompt artifact status and preview."""
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review", number=2)
    evidence = {
        "scan": "mock scan",
        "prompt_path": "artifacts/prompts/issue-2.md",
        "prompt_exists": True,
        "prompt_preview": "# Signposter Task Prompt\n\n## Role Profile\n...",
        "working_dir": "~/work/2",
        "working_dir_status": "not prepared yet",
        "command_shape": "openclaw agent ...",
    }
    content = render_prompt(plan, "ExatronOmega/signposter", evidence_bundle=evidence)

    assert "Prompt Artifact:" in content
    assert "**Exists:** True" in content
    assert "Prompt Preview (first ~80 lines or bounded):" in content
    assert "not prepared yet" in content


def test_render_prompt_evidence_has_working_dir_note():
    """The evidence note must explain that missing working_dir is expected pre-execution."""
    from signposter.runner import render_prompt

    plan = make_runner_plan_for_test("reviewer", "review", number=2)
    note = (
        "Use the embedded evidence below. Do not fetch GitHub URLs. "
        "A missing working_dir is not a failure before execution. "
        "Treat it as pending preparation unless this task is an execution step."
    )
    evidence = {
        "scan": "mock",
        "working_dir": "~/work/2",
        "working_dir_status": "not prepared yet",
        "command_shape": "openclaw agent ...",
        "note": note,
    }
    content = render_prompt(plan, "test/repo", evidence_bundle=evidence)

    assert "A missing working_dir is not a failure before execution" in content
    assert "pending preparation" in content
