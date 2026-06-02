"""Regression tests for conservative model-router defaults."""

from __future__ import annotations

from signposter.dispatch import classify_candidate
from signposter.role_routing import (
    select_role_for_issue,
    select_role_for_reconcile,
    select_role_for_review,
)
from signposter.scan import LabeledItem


def _item(number: int, labels: list[str], title: str) -> LabeledItem:
    return LabeledItem(
        number=number,
        title=title,
        html_url=f"https://github.com/example/repo/issues/{number}",
        labels=labels,
        item_type="issue",
    )


def _issue_role(labels: list[str], title: str) -> str:
    item = _item(10, labels, title)
    return select_role_for_issue(item, classify_candidate(item)).policy.name


def test_low_risk_docs_and_tests_can_use_light_worker() -> None:
    assert (
        _issue_role(
            ["state:ready", "phase:build", "role:worker", "risk:low", "area:docs"],
            "docs: improve role policy note",
        )
        == "WORKER_LIGHT"
    )
    assert (
        _issue_role(
            ["state:ready", "phase:build", "role:worker", "risk:low", "area:tests"],
            "tests: cover small formatting helper",
        )
        == "WORKER_LIGHT"
    )


def test_high_risk_and_core_issue_defaults_prefer_worker_core() -> None:
    assert (
        _issue_role(
            ["state:ready", "phase:build", "role:worker", "risk:high", "area:tests"],
            "tests: cover lifecycle mutation boundaries",
        )
        == "WORKER_CORE"
    )
    assert (
        _issue_role(
            ["state:ready", "phase:build", "role:worker", "risk:medium", "area:core"],
            "audit model router policy",
        )
        == "WORKER_CORE"
    )


def test_unclassified_code_issue_defaults_to_worker_code() -> None:
    assert (
        _issue_role(
            ["state:ready", "phase:build", "role:worker", "risk:medium", "area:cli"],
            "implement operator output formatting",
        )
        == "WORKER_CODE"
    )


def test_review_defaults_escalate_core_paths_and_high_risk() -> None:
    assert (
        select_role_for_review(
            risk_level="medium",
            size="small",
            file_paths=["src/signposter/role_routing.py"],
        ).policy.name
        == "REVIEWER_CORE"
    )
    assert (
        select_role_for_review(
            risk_level="high",
            size="small",
            file_paths=["tests/test_gate.py"],
        ).policy.name
        == "REVIEWER_CORE"
    )


def test_review_defaults_keep_docs_only_small_pr_light() -> None:
    assert (
        select_role_for_review(
            risk_level="low",
            size="small",
            file_paths=["README.md", "docs/operator.md"],
        ).policy.name
        == "REVIEWER_LIGHT"
    )


def test_reconcile_defaults_keep_dag_changes_core() -> None:
    assert select_role_for_reconcile("next").policy.name == "RECONCILE_LIGHT"
    assert select_role_for_reconcile("dependency-change").policy.name == "RECONCILE_CORE"


def test_normal_routing_never_selects_manual_or_legacy_roles() -> None:
    selected = {
        _issue_role(
            ["state:ready", "phase:build", "role:worker", "risk:high", "area:core"],
            "audit safety routing",
        ),
        select_role_for_review(
            risk_level="high",
            size="medium",
            file_paths=["src/signposter/gate.py"],
        ).policy.name,
        select_role_for_reconcile("dag-change").policy.name,
    }

    assert "CRITICAL_OVERRIDE" not in selected
    assert "LEGACY_BACKUP" not in selected
