from __future__ import annotations

from signposter.dispatch import classify_candidate
from signposter.role_routing import (
    resolve_role_execution,
    select_role_for_issue,
    select_role_for_reconcile,
    select_role_for_review,
)
from signposter.scan import LabeledItem


def make_item(number: int, labels: list[str], title: str) -> LabeledItem:
    return LabeledItem(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        labels=labels,
        item_type="issue",
    )


def test_docs_issue_routes_to_worker_light():
    item = make_item(
        1,
        ["state:ready", "phase:build", "role:worker", "risk:low", "area:docs"],
        "Docs: improve role policy notes",
    )

    selection = select_role_for_issue(item, classify_candidate(item))

    assert selection.policy.name == "WORKER_LIGHT"


def test_test_only_issue_routes_to_worker_light():
    item = make_item(
        2,
        ["state:ready", "phase:build", "role:worker", "risk:low", "area:qa"],
        "tests: extend routing coverage",
    )

    selection = select_role_for_issue(item, classify_candidate(item))

    assert selection.policy.name == "WORKER_LIGHT"


def test_real_code_issue_routes_to_worker_code():
    item = make_item(
        3,
        ["state:ready", "phase:build", "role:worker", "risk:medium", "area:cli"],
        "Implement role selection formatting",
    )

    selection = select_role_for_issue(item, classify_candidate(item))

    assert selection.policy.name == "WORKER_CODE"


def test_core_issue_routes_to_worker_core():
    item = make_item(
        4,
        ["state:ready", "phase:build", "role:worker", "risk:high", "area:scheduler"],
        "Route deterministic scheduler stages to role policies",
    )

    selection = select_role_for_issue(item, classify_candidate(item))

    assert selection.policy.name == "WORKER_CORE"


def test_area_core_issue_routes_to_worker_core():
    item = make_item(
        14,
        ["state:ready", "phase:build", "role:worker", "risk:medium", "area:core"],
        "Audit model router policy boundaries",
    )

    selection = select_role_for_issue(item, classify_candidate(item))

    assert selection.policy.name == "WORKER_CORE"
    assert "core or high-risk" in selection.reason


def test_high_risk_test_issue_does_not_use_worker_light():
    item = make_item(
        15,
        ["state:ready", "phase:build", "role:worker", "risk:high", "area:tests"],
        "tests: cover merge safety boundaries",
    )

    selection = select_role_for_issue(item, classify_candidate(item))

    assert selection.policy.name == "WORKER_CORE"
    assert selection.policy.reasoning_effort == "medium"


def test_role_execution_selection_resolves_codex_cli_metadata():
    item = make_item(
        5,
        ["state:ready", "phase:build", "role:worker", "risk:high", "area:scheduler"],
        "Route deterministic scheduler stages to role policies",
    )
    selection = select_role_for_issue(item, classify_candidate(item))

    execution = resolve_role_execution(selection, backend="codex-cli")

    assert execution.backend == "codex-cli"
    assert execution.execution_agent == "codex_worker_core"
    assert execution.model == "openai/gpt-5.4"
    assert execution.reasoning_effort == "medium"
    assert "backend=codex-cli" in execution.reason


def test_role_execution_selection_preserves_openclaw_metadata():
    item = make_item(
        6,
        ["state:ready", "phase:build", "role:worker", "risk:high", "area:scheduler"],
        "Route deterministic scheduler stages to role policies",
    )
    selection = select_role_for_issue(item, classify_candidate(item))

    execution = resolve_role_execution(selection, backend="openclaw")

    assert execution.backend == "openclaw"
    assert execution.execution_agent == "worker_core"
    assert execution.model == "openai/gpt-5.4"
    assert execution.reasoning_effort == "medium"


def test_small_docs_review_routes_to_reviewer_light():
    selection = select_role_for_review(
        risk_level="low",
        size="small",
        file_paths=["README.md", "docs/runbook.md"],
    )

    assert selection.policy.name == "REVIEWER_LIGHT"


def test_core_review_routes_to_reviewer_core():
    selection = select_role_for_review(
        risk_level="medium",
        size="medium",
        file_paths=["src/signposter/merge.py", "tests/test_merge.py"],
    )

    assert selection.policy.name == "REVIEWER_CORE"


def test_role_policy_review_routes_to_reviewer_core():
    selection = select_role_for_review(
        risk_level="medium",
        size="medium",
        file_paths=["src/signposter/role_policy.py", "tests/test_role_policy.py"],
    )

    assert selection.policy.name == "REVIEWER_CORE"


def test_simple_reconcile_routes_to_reconcile_light():
    selection = select_role_for_reconcile("next")
    assert selection.policy.name == "RECONCILE_LIGHT"


def test_dag_reconcile_routes_to_reconcile_core():
    selection = select_role_for_reconcile("dag-change")
    assert selection.policy.name == "RECONCILE_CORE"
